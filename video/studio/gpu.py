"""GPU memory status for the AI queue: nvidia-smi totals + per-process use on Windows.

nvidia-smi on Windows (WDDM) reports no per-process memory, so the per-process numbers
come from the "GPU Process Memory" performance counters (PowerShell). Processes are
attributed to Ovoz when they listen on the Ovoz port, descend from that process, or run
from the Ovoz folder (OVOZ_DIR). The probe takes ~2 s, so it runs in a background
thread and only while someone asks for it (UI polling or a job waiting for memory).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from urllib.parse import urlparse

OVOZ_URL = os.environ.get("OVOZ_URL", "http://127.0.0.1:8000")
OVOZ_PORT = urlparse(OVOZ_URL).port or 8000
OVOZ_DIR = os.environ.get("OVOZ_DIR", r"C:\Users\islom\orca\projects\Matrix").lower()
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$mem = @{}
foreach ($s in (Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples) {
  if ($s.InstanceName -match '^pid_(\d+)_') { $k = [int]$matches[1]; $mem[$k] = [double]$mem[$k] + $s.CookedValue }
}
$all = Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,Name,CommandLine
$listen = @(Get-NetTCPConnection -LocalPort __PORT__ -State Listen | Select-Object -ExpandProperty OwningProcess)
$out = @{ listen = $listen; ppid = @{}; gpu = @() }
foreach ($p in $all) {
  $out.ppid[[string]$p.ProcessId] = [int]$p.ParentProcessId
  $m = $mem[[int]$p.ProcessId]
  if ($m -gt 20MB) {
    $cl = [string]$p.CommandLine; if ($cl.Length -gt 400) { $cl = $cl.Substring(0, 400) }
    $out.gpu += @{ pid = [int]$p.ProcessId; ppid = [int]$p.ParentProcessId; name = $p.Name; cmd = $cl; mb = [int]($m / 1MB) }
  }
}
$out | ConvertTo-Json -Depth 4 -Compress
"""


def smi() -> dict | None:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10,
                           creationflags=NOWIN)
        name, total, used, free, util = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",")]
        return {"name": name, "total_mb": int(total), "used_mb": int(used), "free_mb": int(free),
                "util": int(util) if util.isdigit() else None}
    except Exception:
        return None


def processes() -> dict | None:
    if os.name != "nt":
        return None
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            _PS.replace("__PORT__", str(OVOZ_PORT))],
                           capture_output=True, text=True, timeout=30, creationflags=NOWIN)
        d = json.loads(r.stdout or "{}")
    except Exception:
        return None
    gpu = d.get("gpu") or []
    if isinstance(gpu, dict):
        gpu = [gpu]
    listen = d.get("listen") or []
    if not isinstance(listen, list):
        listen = [listen]
    return {"gpu": gpu, "ppid": {int(k): v for k, v in (d.get("ppid") or {}).items()}, "listen": [int(x) for x in listen]}


def _descends(pid: int, roots: set, ppid: dict) -> bool:
    seen = 0
    while pid and seen < 30:
        if pid in roots:
            return True
        pid = ppid.get(pid, 0)
        seen += 1
    return False


def classify(procs: dict | None, ai_pids: set) -> dict:
    """Sum per-process MB into ovoz / ai job / desktop+other."""
    if not procs:
        return {"ovoz_mb": None, "ai_mb": None, "procs": []}
    roots = set(procs["listen"])
    ovoz = ai = 0
    rows = []
    for p in procs["gpu"]:
        cmd = (p.get("cmd") or "").lower()
        if ai_pids and _descends(p["pid"], ai_pids, procs["ppid"]):
            who = "ai"
            ai += p["mb"]
        elif (roots and _descends(p["pid"], roots, procs["ppid"])) or (OVOZ_DIR and OVOZ_DIR in cmd):
            who = "ovoz"
            ovoz += p["mb"]
        else:
            who = "other"
        label = p.get("name") or "?"
        m = re.search(r"([\w-]+\.py)", p.get("cmd") or "")
        if m:
            label += " " + m.group(1)
        elif "uvicorn" in cmd:
            label += " uvicorn"
        rows.append({"pid": p["pid"], "name": label, "mb": p["mb"], "who": who})
    rows.sort(key=lambda r: -r["mb"])
    return {"ovoz_mb": ovoz, "ai_mb": ai, "procs": rows[:12]}


class Monitor:
    """GPU memory snapshot for the AI queue's VRAM gate and the "Make video" unload stage.

    With a `sysmon.Sampler` (the normal case) this is just the sampler's latest 1 Hz sample (NVML + PDH
    counters, ~3 ms). Without one it falls back to the old nvidia-smi + PowerShell probe (~2 s)."""

    def __init__(self, ai_pids=lambda: set(), sampler=None):
        self.ai_pids = ai_pids
        self.sampler = sampler
        self.snap: dict = {"ok": False, "checked": 0}
        self.wanted = 0.0
        self.lock = threading.Lock()
        self.ev = threading.Event()
        if sampler is None:
            threading.Thread(target=self._loop, name="gpu-monitor", daemon=True).start()

    def get(self, fresh: bool = False) -> dict:
        if self.sampler is not None:
            return self.refresh() if fresh else self.sampler.gate_snapshot()
        self.wanted = time.time()
        if fresh or not self.snap.get("checked"):
            self.refresh()
        else:
            self.ev.set()
        return self.snap

    def refresh(self) -> dict:
        if self.sampler is not None:              # wait for the next sample (<= 1 s), so it is really fresh
            self.sampler.wait_next(time.time(), timeout=3)
            return self.sampler.gate_snapshot()
        with self.lock:
            s = smi()
            procs = processes()
            c = classify(procs, self.ai_pids())
            if s:
                snap = {"ok": True, **s, **c, "checked": time.time()}
            else:
                snap = {"ok": False, "error": "nvidia-smi not available", **c, "checked": time.time()}
            self.snap = snap
            return snap

    def _loop(self) -> None:
        while True:
            self.ev.wait(4.0)
            self.ev.clear()
            if time.time() - self.wanted < 30:
                try:
                    self.refresh()
                except Exception:
                    pass


def gb(mb) -> float | None:
    return None if mb is None else round(mb / 1024, 1)
