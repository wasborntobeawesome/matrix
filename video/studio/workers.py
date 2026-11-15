"""Warm AI model workers: one long-lived `video/ai/worker.py` process per model family.

The weights stay in system RAM between jobs (the machine has 128 GB), so a repeat job skips the slow
model load. During a job the worker moves weights to the GPU (offload hooks), afterwards everything goes
back to RAM; `release_gpu()` (before the "Make video" voice stage) makes sure no worker keeps GPU memory
so Ovoz gets the whole GPU. Settings (library/ai-settings.json):

    warm          keep workers alive between jobs (False = the old behaviour: load, run, exit)
    idle_minutes  a worker that did nothing for this long exits (default 30)
    ram_budget_gb before a job, least recently used *other* workers are stopped until the total RAM of all
                  workers (+ what the next job still has to load) fits (default 80)

Families: wan (Wan2.2-TI2V-5B + FastWan 5B, portraits), a14b-t2v, a14b-i2v, echomimic, acestep.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

import library as L

VIDEO = Path(__file__).resolve().parent.parent
AI = VIDEO / "ai"
AI_PY = AI / ".venv" / "Scripts" / "python.exe"
SETTINGS_FILE = L.LIB / "ai-settings.json"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULTS = {"warm": True, "idle_minutes": 30, "ram_budget_gb": 80}
# RAM a family needs before it has reported real numbers (GB, bf16 weights + pinned buffers)
# measured working sets (RTX 5070 Ti PC): a model's weights + ~3-8 GB of torch/CUDA runtime per process
FAMILY_RAM = {"wan": 36.0, "a14b-t2v": 68.0, "a14b-i2v": 68.0, "echomimic": 43.0, "acestep": 19.0}
MODEL_RAM = {"wan": {"umt5": 14.0, "vae22": 2.8, "vae22-bf16": 1.4, "wan22-5b": 9.3, "fastwan-5b": 9.3},
             "a14b-t2v": {"umt5": 14.0, "a14b-t2v": 54.0}, "a14b-i2v": {"umt5": 14.0, "a14b-i2v": 54.0},
             "echomimic": {"echomimic": 43.0}, "acestep": {"acestep": 19.0}}
LABELS = {"wan": "Wan2.2 5B / FastWan", "a14b-t2v": "Wan2.2 A14B (text→video)", "a14b-i2v": "Wan2.2 A14B (image→video)",
          "echomimic": "EchoMimicV3 presenter", "acestep": "ACE-Step music"}


class Cancelled(Exception):
    pass


def _rss_gb(pid: int) -> float | None:
    try:
        import psutil
        m = psutil.Process(pid).memory_info()
        return round(m.rss / 2**30, 2)       # working set = physical RAM held (commit charge is higher)
    except Exception:
        return None


def kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)


class Worker:
    def __init__(self, family: str, warm: bool):
        self.family, self.warm = family, warm
        # the worker's localhost port is announced on stdout: the first line on the socket must carry this
        # secret, so no other program (or a web page racing to the port) can drive the worker
        self.token = secrets.token_hex(16)
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
               "STUDIO_WORKER_TOKEN": self.token}
        self.proc = subprocess.Popen([str(AI_PY), "worker.py", "--family", family], cwd=str(AI),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                     creationflags=NOWIN | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        self.pid = self.proc.pid
        self.started = time.time()
        self.last_used = time.time()
        self.status: dict = {"models": [], "busy": None}
        self.ready = threading.Event()
        self.lock = threading.Lock()          # one job at a time
        self.wlock = threading.Lock()
        self.listener = None                  # callback(kind, data_or_line) while a job runs
        self.sock = None                      # commands go over a localhost socket (see worker.py)
        self.jobs_run = 0
        self.log: list[str] = []
        threading.Thread(target=self._read, name=f"worker-{family}", daemon=True).start()

    # ---------------------------------------------------------------- io
    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, cmd: dict) -> None:
        with self.wlock:
            if self.sock is None:
                return
            try:
                self.sock.sendall((json.dumps(cmd) + "\n").encode())
            except OSError:
                pass

    def _connect(self, port) -> None:
        try:
            s = socket.create_connection(("127.0.0.1", int(port)), timeout=10)
            s.settimeout(None)
            s.sendall((json.dumps({"op": "hello", "token": self.token}) + "\n").encode())
            self.sock = s
        except (OSError, TypeError, ValueError) as e:
            self.log.append(f"could not connect to the worker: {e}")

    def _read(self) -> None:
        buf = b""
        pat = re.compile(r"@@(PROGRESS|RESULT|ERROR|STATUS|READY) (\{.*\})\s*$")
        while True:
            chunk = self.proc.stdout.read1(65536) if hasattr(self.proc.stdout, "read1") else self.proc.stdout.read(1)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]", buf)
            buf = parts.pop()
            for raw in parts:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                m = pat.search(line)
                if m:
                    try:
                        data = json.loads(m.group(2))
                    except ValueError:
                        data = None
                    if data is not None:
                        kind = m.group(1)
                        if kind in ("STATUS", "READY"):
                            self.status = data
                            if kind == "READY":
                                self._connect(data.get("port"))
                                self.ready.set()
                        cb = self.listener
                        if cb and kind in ("PROGRESS", "RESULT", "ERROR"):
                            _safe(cb, kind, data)
                        continue
                self.log.append(line[:300])
                del self.log[:-60]
                cb = self.listener
                if cb:
                    _safe(cb, "LOG", line)
        self.ready.set()
        cb = self.listener
        if cb:
            _safe(cb, "EXIT", self.proc.poll())

    # ---------------------------------------------------------------- jobs
    def run(self, jid: str, task: dict, on_event, cancelled) -> dict:
        """Blocking. on_event(kind, data) gets PROGRESS / LOG; returns the RESULT dict."""
        with self.lock:
            self.ready.wait(120)
            if not self.alive():
                raise RuntimeError(f"AI worker '{self.family}' exited: " + " | ".join(self.log[-4:]))
            done = threading.Event()
            box: dict = {}

            def listen(kind, data):
                if kind in ("RESULT", "ERROR"):
                    if data.get("id") == jid:
                        box[kind] = data
                        done.set()
                elif kind == "EXIT":
                    box["EXIT"] = data
                    done.set()
                else:
                    on_event(kind, data)
            self.listener = listen
            self.last_used = time.time()
            self.status["busy"] = jid
            self.send({"op": "run", "id": jid, "task": task})
            sent_cancel = None
            try:
                while not done.wait(0.5):
                    if cancelled() and sent_cancel is None:
                        sent_cancel = time.time()
                        self.send({"op": "cancel", "id": jid})
                    if sent_cancel and time.time() - sent_cancel > 25:
                        kill_tree(self.pid)          # did not stop at a step boundary: lose the warm weights
                        raise Cancelled()
            finally:
                self.listener = None
                self.last_used = time.time()
                self.jobs_run += 1
                self.status["busy"] = None
            if "RESULT" in box:
                r = dict(box["RESULT"])
                r.pop("id", None)
                return r
            if "ERROR" in box:
                if box["ERROR"].get("cancelled"):
                    raise Cancelled()
                raise RuntimeError(box["ERROR"].get("error") or "worker error")
            if sent_cancel:
                raise Cancelled()
            raise RuntimeError(f"AI worker '{self.family}' exited (code {box.get('EXIT')}): " + " | ".join(
                x for x in self.log[-5:] if not re.match(r"^\s*\d+%\|", x)))

    def quit(self, wait: float = 15) -> None:
        if not self.alive():
            return
        self.send({"op": "quit"})
        try:
            self.proc.wait(wait)
        except subprocess.TimeoutExpired:
            kill_tree(self.pid)

    def real_pid(self) -> int:
        """The venv's python.exe is a launcher; the worker itself is its child (it reports its pid)."""
        pid = (self.status or {}).get("pid")
        if pid:
            return int(pid)
        try:                                   # still starting (no READY yet): the launcher's python child
            import psutil
            kids = [c for c in psutil.Process(self.pid).children() if "python" in (c.name() or "").lower()]
            if kids:
                return kids[0].pid
        except Exception:
            pass
        return self.pid

    def ram_gb(self) -> float:
        return _rss_gb(self.real_pid()) or 0.0

    def view(self) -> dict:
        st = self.status or {}
        models = [{**m, "label": m.get("key")} for m in st.get("models") or []]
        return {"family": self.family, "label": LABELS.get(self.family, self.family), "pid": self.pid,
                "alive": self.alive(), "busy": bool(st.get("busy")) or self.lock.locked(), "ready": self.ready.is_set(),
                "models": models, "ram_gb": self.ram_gb() if self.alive() else 0, "worker_pid": self.real_pid(),
                "models_gb": round(sum(m.get("gb") or 0 for m in models), 1), "vram_gb": st.get("vram_gb"),
                "started": self.started, "last_used": self.last_used, "idle_s": round(time.time() - self.last_used),
                "jobs": self.jobs_run, "warm": self.warm}


class Pool:
    def __init__(self):
        self.workers: dict[str, Worker] = {}
        self.lock = threading.RLock()
        self.settings = self._load()
        self.events: list[dict] = []          # recent evictions / idle exits / releases (for the UI)
        threading.Thread(target=self._idle_loop, name="ai-worker-idle", daemon=True).start()

    # ---------------------------------------------------------------- settings
    def _load(self) -> dict:
        try:
            d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        return {**DEFAULTS, **{k: d[k] for k in DEFAULTS if k in d}}

    def update(self, d: dict) -> dict:
        s = dict(self.settings)
        if "warm" in d:
            s["warm"] = bool(d["warm"])
        if d.get("idle_minutes") is not None:
            s["idle_minutes"] = max(1, min(24 * 60, int(float(d["idle_minutes"]))))
        if d.get("ram_budget_gb") is not None:
            s["ram_budget_gb"] = max(8, min(120, int(float(d["ram_budget_gb"]))))
        self.settings = s
        L.LIB.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=1), encoding="utf-8")
        if not s["warm"]:
            self.release_ram(reason="warm workers turned off")
        else:
            self._enforce(None, 0)
        return s

    def _event(self, text: str) -> None:
        self.events.append({"t": time.time(), "text": text})
        del self.events[:-30]

    # ---------------------------------------------------------------- jobs
    def ram_of(self, w: Worker) -> float:
        return (w.ram_gb() if w.alive() else 0.0) or 0.0

    def missing_gb(self, family: str, keys: list[str]) -> float:
        """RAM the job still has to load into its worker."""
        w = self.workers.get(family)
        have = {m["key"] for m in (w.status.get("models") or [])} if w and w.alive() else set()
        table = MODEL_RAM.get(family, {})
        miss = [k for k in keys if k not in have]
        if not w or not w.alive():
            return sum(table.get(k, 0) for k in keys) or FAMILY_RAM.get(family, 20)
        return sum(table.get(k, 0) for k in miss)

    def _enforce(self, family: str | None, extra_gb: float) -> None:
        """Stop least recently used idle workers (not `family`) until everything fits the RAM budget."""
        budget = float(self.settings["ram_budget_gb"])
        with self.lock:
            while True:
                live = [w for w in self.workers.values() if w.alive()]
                total = sum(self.ram_of(w) for w in live) + extra_gb
                if total <= budget:
                    return
                victims = sorted([w for w in live if w.family != family and not w.lock.locked()], key=lambda w: w.last_used)
                if not victims:
                    return                  # the job alone is over budget: run anyway, the worker exits afterwards
                v = victims[0]
                self._event(f"RAM budget {budget:.0f} GB: stopped the least recently used worker "
                            f"'{v.family}' ({self.ram_of(v):.1f} GB)")
                v.quit()
                self.workers.pop(v.family, None)

    def worker(self, family: str) -> Worker:
        with self.lock:
            w = self.workers.get(family)
            if w and w.alive():
                return w
            w = Worker(family, self.settings["warm"])
            self.workers[family] = w
            return w

    def run(self, job: dict, family: str, task: dict, keys: list[str], on_event, cancelled) -> dict:
        """Run one task in the family's worker. Returns the result + {"warm": bool, "worker_pid"}."""
        if not AI_PY.exists():
            raise RuntimeError(f"AI environment missing: {AI_PY} (see video/ai/README.md)")
        need = self.missing_gb(family, keys)
        self._enforce(family, need)
        w = self.worker(family)
        job["_worker_pid"] = w.pid
        try:
            res = w.run(job["id"], task, on_event, cancelled)
        finally:
            job.pop("_worker_pid", None)
        res["warm"] = bool(res.get("warm"))          # the worker knows whether it had to load anything
        res["worker_pid"] = w.pid
        res["worker_ram_gb"] = self.ram_of(w)
        budget = float(self.settings["ram_budget_gb"])
        if self.settings["warm"] and sum(self.ram_of(x) for x in self.workers.values() if x.alive()) > budget:
            self._enforce(family, 0)          # over budget now: stop least recently used *other* idle workers first
        over = self.ram_of(w) > budget        # this family alone is bigger than the budget
        if not self.settings["warm"] or over:
            if over and self.settings["warm"]:
                self._event(f"'{family}' alone needs more RAM than the budget ({self.settings['ram_budget_gb']} GB): "
                            "it exits after each job")
            w.quit()
            with self.lock:
                if self.workers.get(family) is w:
                    self.workers.pop(family, None)
        return res

    # ---------------------------------------------------------------- GPU / RAM control
    def release_gpu(self, wait: float = 20) -> list[str]:
        """Ask every idle worker to give back its GPU memory (weights stay in RAM). Returns the families asked."""
        asked = []
        for w in list(self.workers.values()):
            if w.alive() and not w.lock.locked():
                w.send({"op": "release_gpu"})
                asked.append(w.family)
        t = time.time()
        while asked and time.time() - t < wait:      # STATUS comes back once it is done
            if all((self.workers.get(f) is None) or (self.workers[f].status.get("vram_gb") or 0) < 0.2 for f in asked):
                break
            time.sleep(0.25)
        if asked:
            self._event("released GPU memory: " + ", ".join(asked))
        return asked

    def release_ram(self, family: str | None = None, reason: str = "Release RAM") -> list[str]:
        stopped = []
        with self.lock:
            for f, w in list(self.workers.items()):
                if family and f != family:
                    continue
                if w.lock.locked():
                    continue                     # busy: never kill a running job from here
                w.quit()
                self.workers.pop(f, None)
                stopped.append(f)
        if stopped:
            self._event(f"{reason}: stopped {', '.join(stopped)}")
        return stopped

    def _idle_loop(self) -> None:
        while True:
            time.sleep(20)
            limit = float(self.settings["idle_minutes"]) * 60
            for f, w in list(self.workers.items()):
                if not w.alive():
                    with self.lock:
                        if self.workers.get(f) is w and not w.lock.locked():
                            self.workers.pop(f, None)
                    continue
                if not w.lock.locked() and time.time() - w.last_used > limit:
                    self._event(f"'{f}' idle for {self.settings['idle_minutes']} min: stopped")
                    w.quit()
                    with self.lock:
                        if self.workers.get(f) is w:
                            self.workers.pop(f, None)
            for w in list(self.workers.values()):     # refresh model lists / VRAM
                if w.alive() and not w.lock.locked():
                    w.send({"op": "status"})

    def pids(self) -> dict[int, str]:
        out = {}
        for w in self.workers.values():
            if w.alive():
                out[w.pid] = w.family
                out[w.real_pid()] = w.family
        return out

    def view(self) -> dict:
        ws = [w.view() for w in self.workers.values() if w.alive()]
        return {"settings": self.settings, "workers": ws, "ram_gb": round(sum(w["ram_gb"] or 0 for w in ws), 1),
                "events": self.events[-10:], "families": LABELS}

    def shutdown(self) -> None:
        for w in list(self.workers.values()):
            try:
                w.quit(5)
            except Exception:
                pass


def kill_leftovers() -> int:
    """After a studio restart: stop worker.py processes the previous studio left behind."""
    n = 0
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            cl = " ".join(p.info.get("cmdline") or []).lower()
            if not ("worker.py" in cl and "--family" in cl):
                continue
            try:
                if Path(p.cwd()).resolve() != AI.resolve():
                    continue
                parent = p.parent()
                pcl = " ".join(parent.cmdline()).lower() if parent and parent.is_running() else ""
                if "worker.py" in pcl:
                    continue                # the real interpreter under the venv launcher: handled via the launcher
                if "uvicorn" in pcl or "server:app" in pcl:
                    continue                # belongs to another running studio instance
            except Exception:
                continue
            kill_tree(p.info["pid"])
            n += 1
    except Exception:
        pass
    return n


def _safe(cb, kind, data) -> None:
    """A failing listener must never kill the reader thread (the worker would block on a full stdout pipe)."""
    try:
        cb(kind, data)
    except Exception as e:  # noqa: BLE001
        print(f"worker listener error ({kind}): {e!r}")
