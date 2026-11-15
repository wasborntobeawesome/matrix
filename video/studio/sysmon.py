"""Live system monitor: a 1 Hz sampler thread with a 30-minute ring buffer.

Sources (all cheap; measured overhead is reported by `overhead()`):
  - NVML (nvidia-ml-py, BSD-3): GPU utilisation, VRAM used/total, temperature, power + limit
  - psutil (BSD-3): CPU %, RAM used/total, per-process RAM, the process tree
  - Windows PDH counters via ctypes: "\\GPU Process Memory(*)\\Dedicated Usage" and
    "\\GPU Engine(*)\\Utilization Percentage" per process (the same counters gpu.py used to read
    through PowerShell, ~2 s per probe; PDH reads them in about a millisecond)

VRAM and RAM are attributed to owners:
  ovoz    the process listening on the Ovoz port, its descendants, anything running from OVOZ_DIR
  ai      the warm AI workers / AI job subprocesses and their descendants
  studio  this server and its other descendants (render.py + headless Chromium)
  other   everything else (desktop, browsers, games …; for VRAM also driver overhead)

Stage markers (the "Make video" stages, AI jobs, renders, voice jobs) are recorded as time spans from
an `activity()` callback that reports what is running right now.
"""
from __future__ import annotations

import collections
import ctypes
import os
import re
import threading
import time

import gpu

HISTORY_S = 30 * 60
NOWIN = 0


# ---------------------------------------------------------------- PDH (Windows performance counters)
class _Pdh:
    PDH_FMT_DOUBLE = 0x200
    PDH_FMT_LARGE = 0x400
    PDH_FMT_NOCAP100 = 0x8000

    def __init__(self):
        from ctypes import wintypes as W
        self.W = W
        self.pdh = ctypes.WinDLL("pdh")

        class FMT(ctypes.Structure):
            _fields_ = [("CStatus", W.DWORD), ("pad", W.DWORD), ("value", ctypes.c_longlong)]

        class FMTD(ctypes.Structure):
            _fields_ = [("CStatus", W.DWORD), ("pad", W.DWORD), ("value", ctypes.c_double)]

        class ITEM(ctypes.Structure):
            _fields_ = [("szName", W.LPWSTR), ("FmtValue", FMT)]

        class ITEMD(ctypes.Structure):
            _fields_ = [("szName", W.LPWSTR), ("FmtValue", FMTD)]
        self.ITEM, self.ITEMD = ITEM, ITEMD
        self.q = W.HANDLE()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.q)) != 0:
            raise OSError("PdhOpenQuery failed")
        self.mem = self._add("\\GPU Process Memory(*)\\Dedicated Usage")
        self.eng = self._add("\\GPU Engine(*)\\Utilization Percentage")
        self.pdh.PdhCollectQueryData(self.q)

    def _add(self, path):
        h = self.W.HANDLE()
        r = self.pdh.PdhAddEnglishCounterW(self.q, path, 0, ctypes.byref(h))
        return h if r == 0 else None

    def _array(self, counter, fmt, item_t):
        if counter is None:
            return []
        size, n = self.W.DWORD(0), self.W.DWORD(0)
        self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(n), None)
        if not size.value:
            return []
        buf = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(counter, fmt, ctypes.byref(size), ctypes.byref(n), buf) != 0:
            return []
        items = ctypes.cast(buf, ctypes.POINTER(item_t))
        return [(items[i].szName, items[i].FmtValue.value) for i in range(n.value)]

    def read(self) -> tuple[dict, dict]:
        """({pid: dedicated VRAM bytes}, {pid: GPU engine %})."""
        self.pdh.PdhCollectQueryData(self.q)
        mem, eng = {}, {}
        for name, v in self._array(self.mem, self.PDH_FMT_LARGE, self.ITEM):
            m = re.match(r"pid_(\d+)_", name)
            if m:
                pid = int(m.group(1))
                mem[pid] = mem.get(pid, 0) + v
        for name, v in self._array(self.eng, self.PDH_FMT_DOUBLE | self.PDH_FMT_NOCAP100, self.ITEMD):
            m = re.match(r"pid_(\d+)_", name)
            if m and v > 0:
                pid = int(m.group(1))
                eng[pid] = eng.get(pid, 0.0) + v
        return mem, eng


# ---------------------------------------------------------------- sampler
class Sampler:
    def __init__(self, ai_pids=lambda: {}, activity=lambda: [], warm_ram=lambda: {}):
        import psutil
        self.ps = psutil
        self.ai_pids, self.activity, self.warm_ram = ai_pids, activity, warm_ram
        self.me = os.getpid()
        self.hist: collections.deque = collections.deque(maxlen=HISTORY_S + 5)
        self.markers: collections.deque = collections.deque(maxlen=500)
        self.open: dict[str, dict] = {}
        self.procs: list[dict] = []
        self.latest: dict = {}
        self.cond = threading.Condition()
        self.nv = None
        self.info = {"gpu": None, "driver": None, "cpu_threads": psutil.cpu_count(), "cpu": _cpu_name()}
        self.err: dict = {}
        self._tree: dict[int, tuple] = {}        # pid -> (ppid, name, cmdline_lower)
        self._tree_t = 0.0
        self._listen: set = set()
        self._listen_t = 0.0
        self.cost = {"n": 0, "cpu_s": 0.0, "wall_s": 0.0, "max_wall_ms": 0.0}
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nv = pynvml
            self.h = pynvml.nvmlDeviceGetHandleByIndex(0)
            nm = pynvml.nvmlDeviceGetName(self.h)
            self.info["gpu"] = nm.decode() if isinstance(nm, bytes) else nm
            dv = pynvml.nvmlSystemGetDriverVersion()
            self.info["driver"] = dv.decode() if isinstance(dv, bytes) else dv
            self.info["power_limit_w"] = round(pynvml.nvmlDeviceGetPowerManagementLimit(self.h) / 1000)
        except Exception as e:  # no NVIDIA GPU / driver: CPU + RAM only
            self.err["nvml"] = str(e)
        try:
            self.pdh = _Pdh() if os.name == "nt" else None
        except Exception as e:
            self.pdh = None
            self.err["pdh"] = str(e)
        psutil.cpu_percent(None)
        threading.Thread(target=self._loop, name="sysmon", daemon=True).start()

    # ------------------------------------------------------------ process tree
    def _refresh_tree(self) -> None:
        now = time.time()
        if now - self._tree_t > 5:
            tree = {}
            for p in self.ps.process_iter(["pid", "ppid", "name"]):
                i = p.info
                old = self._tree.get(i["pid"])
                cl = old[2] if old and old[1] == i["name"] else None
                tree[i["pid"]] = (i["ppid"] or 0, i["name"] or "?", cl)
            self._tree, self._tree_t = tree, now
        if now - self._listen_t > 10:
            try:
                self._listen = {c.pid for c in self.ps.net_connections("tcp4")
                                if c.status == "LISTEN" and c.laddr and c.laddr.port == gpu.OVOZ_PORT and c.pid}
            except Exception:
                pass
            self._listen_t = now

    def _cmd(self, pid: int) -> str:
        t = self._tree.get(pid)
        if t is None:
            return ""
        if t[2] is None:
            try:
                cl = " ".join(self.ps.Process(pid).cmdline())[:400].lower()
            except Exception:
                cl = ""
            self._tree[pid] = (t[0], t[1], cl)
            return cl
        return t[2]

    def _descends(self, pid: int, roots) -> bool:
        seen = 0
        while pid and seen < 40:
            if pid in roots:
                return True
            pid = self._tree.get(pid, (0,))[0]
            seen += 1
        return False

    def owner(self, pid: int, ai: set) -> str:
        if ai and self._descends(pid, ai):
            return "ai"
        if (self._listen and self._descends(pid, self._listen)) or (gpu.OVOZ_DIR and gpu.OVOZ_DIR in self._cmd(pid)):
            return "ovoz"
        if self._descends(pid, {self.me}):
            return "studio"
        return "other"

    # ------------------------------------------------------------ one sample
    def sample(self) -> dict:
        t0, c0 = time.perf_counter(), time.thread_time()
        now = time.time()
        s: dict = {"t": round(now, 1)}
        nv = self.nv
        if nv:
            try:
                u = nv.nvmlDeviceGetUtilizationRates(self.h)
                m = nv.nvmlDeviceGetMemoryInfo(self.h)
                s.update(gu=u.gpu, vt=m.total >> 20, vu=m.used >> 20,
                         tc=nv.nvmlDeviceGetTemperature(self.h, nv.NVML_TEMPERATURE_GPU),
                         pw=round(nv.nvmlDeviceGetPowerUsage(self.h) / 1000, 1))
            except Exception as e:
                self.err["nvml_read"] = str(e)
        s["cpu"] = round(self.ps.cpu_percent(None), 1)
        vm = self.ps.virtual_memory()
        s["rt"], s["ru"] = round(vm.total / 2**30, 1), round((vm.total - vm.available) / 2**30, 2)
        self._refresh_tree()
        ai = set(self.ai_pids() or {})
        mem, eng = self.pdh.read() if self.pdh else ({}, {})
        owners = {"ovoz": 0, "ai": 0, "studio": 0}
        rows = []
        for pid, b in mem.items():
            if b < 16 * 2**20 and eng.get(pid, 0) < 1:
                continue
            who = self.owner(pid, ai)
            mb = b >> 20
            if who in owners:
                owners[who] += mb
            rows.append({"pid": pid, "name": self._tree.get(pid, (0, "?"))[1], "who": who, "vram_mb": mb,
                         "gpu": round(min(100.0, eng.get(pid, 0.0)), 1)})
        if self.pdh:
            s.update(vo=owners["ovoz"], va=owners["ai"], vs=owners["studio"])
        # RAM per owner (working sets of the attributed process trees)
        ram = {"ovoz": 0.0, "ai": 0.0, "studio": 0.0}
        for pid in list(self._tree):
            who = self.owner(pid, ai)
            if who == "other":
                continue
            try:
                rss = self.ps.Process(pid).memory_info().rss
            except Exception:
                continue
            ram[who] += rss / 2**30
            for r in rows:
                if r["pid"] == pid:
                    r["ram_gb"] = round(rss / 2**30, 2)
        s.update(ra=round(ram["ai"], 2), ro=round(ram["ovoz"], 2), rs=round(ram["studio"], 2))
        wr = self.warm_ram() or {}
        s["rw"] = round(sum(wr.values()), 2)          # RAM held by warm model workers (subset of ra)
        for r in rows:
            r["label"] = _label(r["name"], self._cmd(r["pid"]))
        rows.sort(key=lambda r: -r["vram_mb"])
        self.procs = rows[:14]
        self._markers(now)
        s["act"] = [m["label"] for m in self.open.values()]
        dt = time.perf_counter() - t0
        if self.cost.get("warmup", 0) < 10:          # the first samples read every process's command line once
            self.cost["warmup"] = self.cost.get("warmup", 0) + 1
            return s
        self.cost["n"] += 1
        self.cost["wall_s"] += dt
        self.cost["cpu_s"] += time.thread_time() - c0
        self.cost["max_wall_ms"] = max(self.cost["max_wall_ms"], dt * 1000)
        return s

    def _markers(self, now: float) -> None:
        try:
            act = self.activity() or []
        except Exception:
            act = []
        keys = set()
        for key, label, cat in act:
            keys.add(key)
            m = self.open.get(key)
            if m is None:
                m = {"key": key, "label": label, "cat": cat, "t0": round(now, 1), "t1": None}
                self.open[key] = m
                self.markers.append(m)
            else:
                m["label"] = label
        for key in [k for k in self.open if k not in keys]:
            self.open.pop(key)["t1"] = round(now, 1)

    def _loop(self) -> None:
        while True:
            nxt = int(time.time()) + 1
            time.sleep(max(0.0, nxt - time.time()))
            try:
                s = self.sample()
            except Exception as e:  # never let the monitor die
                self.err["sample"] = repr(e)
                continue
            with self.cond:
                self.hist.append(s)
                self.latest = s
                self.cond.notify_all()

    # ------------------------------------------------------------ API helpers
    def wait_next(self, after: float, timeout: float = 5.0) -> dict:
        with self.cond:
            if not self.latest or self.latest.get("t", 0) <= after:
                self.cond.wait(timeout)
            return self.latest

    def history(self, since: float = 0) -> list[dict]:
        cut = max(since, time.time() - HISTORY_S)
        return [s for s in list(self.hist) if s["t"] > cut]

    def marker_list(self) -> list[dict]:
        cut = time.time() - HISTORY_S
        return [dict(m) for m in list(self.markers) if (m["t1"] or time.time()) > cut]

    def overhead(self) -> dict:
        c = self.cost
        n = max(1, c["n"])
        return {"samples": c["n"], "avg_cpu_ms": round(c["cpu_s"] / n * 1000, 2),
                "avg_wall_ms": round(c["wall_s"] / n * 1000, 2), "max_wall_ms": round(c["max_wall_ms"], 1),
                "cpu_core_pct": round(c["cpu_s"] / n * 100, 3),
                "cpu_machine_pct": round(c["cpu_s"] / n * 100 / max(1, self.info["cpu_threads"] or 1), 4)}

    def gate_snapshot(self) -> dict:
        """The shape gpu.Monitor used to return (for the AI queue's VRAM gate and the unload stage)."""
        s = self.latest or self.sample()
        if "vt" not in s:
            return {"ok": False, "error": self.err.get("nvml") or "NVML not available", "ovoz_mb": None, "ai_mb": None,
                    "procs": [], "checked": s.get("t")}
        rows = [{"pid": r["pid"], "name": r["label"], "mb": r["vram_mb"], "who": r["who"]} for r in self.procs]
        return {"ok": True, "name": self.info["gpu"], "total_mb": s["vt"], "used_mb": s["vu"], "free_mb": s["vt"] - s["vu"],
                "util": s.get("gu"), "ovoz_mb": s.get("vo") if self.pdh else None, "ai_mb": s.get("va") if self.pdh else None,
                "studio_mb": s.get("vs"), "procs": rows[:12], "checked": s["t"], "temp_c": s.get("tc"), "power_w": s.get("pw")}


def _label(name: str, cmd: str) -> str:
    m = re.search(r"([\w-]+\.py)(?:\s+--family\s+([\w-]+))?", cmd or "")
    if m:
        return f"{name} {m.group(1)}" + (f" {m.group(2)}" if m.group(2) else "")
    if "uvicorn" in (cmd or ""):
        return f"{name} uvicorn"
    if "chrom" in (name or "").lower() and "headless" in (cmd or ""):
        return f"{name} (headless)"
    return name


def _cpu_name() -> str | None:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except Exception:
        return None
