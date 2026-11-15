"""ComfyUI as a b-roll generator: a managed (or external) ComfyUI server and its HTTP / WebSocket API.

ComfyUI (GPL-3.0) is never imported. The Studio starts it as a separate program from its own folder and
venv (video/comfyui/, installed by video/comfyui/setup.py) or uses one that is already running at a
configured URL, and only talks to it over HTTP:

    GET  /system_stats /queue /object_info /history/<id> /view       status, nodes, results
    POST /prompt  /upload/image  /interrupt  /queue {"delete"}  /free  run, inputs, cancel, unload
    WS   /ws?clientId=…                                             progress of the running prompt

Settings (library/comfy-settings.json):
    mode            "managed" (the Studio starts/stops video/comfyui) or "external" (a ComfyUI at `url`)
    url             the external ComfyUI (default http://127.0.0.1:8188); managed always uses 127.0.0.1:port
    port            port of the managed server (8188)
    autostart       start the managed server when a ComfyUI job needs it (default on)
    vram_mode       "auto" (ComfyUI's dynamic VRAM, default) | "lowvram" | "novram" | "highvram"
    reserve_vram_gb VRAM ComfyUI leaves for the desktop / Ovoz (--reserve-vram, default 1.0)
    custom_nodes    load custom nodes from ComfyUI/custom_nodes (default off: core nodes only)
    extra_args      extra command-line flags for the managed server
"""
from __future__ import annotations

import collections
import json
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import library as L

VIDEO = Path(__file__).resolve().parent.parent
HOME = VIDEO / "comfyui"
PY = HOME / ".venv" / "Scripts" / "python.exe"
MAIN = HOME / "ComfyUI" / "main.py"
LOG_FILE = HOME / "comfyui.log"
SETTINGS_FILE = L.LIB / "comfy-settings.json"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULTS = {"mode": "managed", "url": "http://127.0.0.1:8188", "port": 8188, "autostart": True, "vram_mode": "auto",
            "reserve_vram_gb": 1.0, "custom_nodes": False, "extra_args": ""}
VRAM_MODES = {"auto": [], "lowvram": ["--lowvram"], "novram": ["--novram"], "highvram": ["--highvram"]}
# An external ComfyUI must be on this PC unless the user explicitly allows other hosts (env, comma-separated):
#   STUDIO_COMFY_HOSTS=192.168.1.20,gpu-box.local   (or * for any host)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
REMOTE_HOSTS = {h.strip().lower() for h in os.environ.get("STUDIO_COMFY_HOSTS", "").split(",") if h.strip()}
# extra_args may tune ComfyUI (precision, attention, caching, VRAM), but not re-open it to the network, move its
# folders, load other code or write files. An ALLOWLIST, compared exactly: ComfyUI parses its flags with argparse,
# which also accepts any unambiguous prefix ("--liste 0.0.0.0" = --listen), so a blocklist of names leaks.
SAFE_FLAGS = frozenset("""
    --cuda-malloc --disable-cuda-malloc --force-fp32 --force-fp16 --fp32-unet --fp64-unet --bf16-unet --fp16-unet
    --fp8_e4m3fn-unet --fp8_e5m2-unet --fp8_e8m0fnu-unet --fp16-vae --fp32-vae --bf16-vae --cpu-vae
    --fp8_e4m3fn-text-enc --fp8_e5m2-text-enc --fp16-text-enc --fp32-text-enc --bf16-text-enc --fp16-intermediates
    --force-channels-last --supports-fp8-compute --enable-triton-backend --disable-triton-backend
    --cache-classic --cache-none --high-ram --use-split-cross-attention --use-quad-cross-attention
    --use-pytorch-cross-attention --use-sage-attention --use-flash-attention --use-ck-attention --disable-xformers
    --force-upcast-attention --dont-upcast-attention --gpu-only --highvram --lowvram --novram --cpu
    --disable-nvml-pressure --disable-async-offload --disable-dynamic-vram --enable-dynamic-vram --fast-disk
    --disable-fast-disk --disable-cuda-graphs --disable-comfy-compiler --force-non-blocking --disable-smart-memory
    --deterministic --disable-pinned-memory --mmap-torch-files --disable-mmap --disable-metadata
    --disable-all-custom-nodes --disable-partner-nodes --offline --disable-api-nodes --disable-product-tips
""".split())
# flags that take values: how many (None = any number, "?" = zero or one)
SAFE_VALUE_FLAGS = {"--cuda-device": 1, "--default-device": 1, "--cache-lru": 1, "--cache-ram": None,
                    "--vram-headroom": 1, "--async-offload": "?", "--preview-size": 1, "--max-upload-size": 1,
                    "--default-hashing-function": 1, "--fast": None}
ARG_RE = re.compile(r"^(--[a-z0-9][a-z0-9_-]*(=[\w.:,+-]*)?|[\w.:,+-]+)$", re.I)


def check_url(u: str) -> str:
    """http(s)://host[:port] of an external ComfyUI; loopback only unless STUDIO_COMFY_HOSTS allows the host."""
    u = str(u or "").strip().rstrip("/")
    p = urllib.parse.urlparse(u)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host or p.username or p.password or p.query or p.fragment \
            or (p.path not in ("", "/")):
        raise ValueError("url must look like http://127.0.0.1:8188")
    try:
        p.port
    except ValueError:
        raise ValueError("url: bad port") from None
    if host not in LOOPBACK and not host.startswith("127.") and "*" not in REMOTE_HOSTS and host not in REMOTE_HOSTS:
        raise ValueError(f"only a ComfyUI on this PC (127.0.0.1 / localhost) is allowed; to use {host}, start the "
                         "studio with STUDIO_COMFY_HOSTS=" + host)
    return u


def check_args(extra: str) -> str:
    """Only SAFE_FLAGS / SAFE_VALUE_FLAGS, written out in full (no argparse abbreviations, no single-dash forms),
    and values only after a flag that takes them."""
    extra = str(extra or "").strip()[:300]
    try:
        toks = shlex.split(extra, posix=False)
    except ValueError:
        raise ValueError("extra_args: unbalanced quotes") from None
    room = 0                                   # values the previous flag can still take (None = any number)
    for t in toks:
        if not ARG_RE.match(t):
            raise ValueError(f"extra_args: '{t}' is not allowed (plain --flags and values only)")
        if t.startswith("-"):
            name, eq, _ = t.partition("=")
            if name in SAFE_FLAGS and not eq:
                room = 0
            elif name in SAFE_VALUE_FLAGS:
                n = SAFE_VALUE_FLAGS[name]
                room = 0 if eq else (1 if n == "?" else n)
            else:
                raise ValueError(f"extra_args: {name} is not allowed (managed by the studio, or not a known safe "
                                 "ComfyUI flag — write flags out in full)")
        else:
            if room == 0:
                raise ValueError(f"extra_args: '{t}' is a value without a flag that takes it")
            if room is not None:
                room -= 1
    return extra
SAMPLERS = ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced")


class ComfyError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)


class Comfy:
    def __init__(self, sampler=None):
        self.sampler = sampler                   # sysmon.Sampler: VRAM of the ComfyUI process (owner "comfy")
        self.settings = self._load()
        self.proc: subprocess.Popen | None = None
        self.state = "stopped"                   # stopped | starting | running | error (managed server)
        self.error: str | None = None
        self.log: collections.deque = collections.deque(maxlen=300)
        self.lock = threading.RLock()
        self._st: dict = {}
        self._st_t = 0.0
        self._oi: tuple[float, dict] | None = None
        self._listen: tuple[float, set] = (0.0, set())
        self.last_free: dict | None = None
        self.last_workflow: str | None = None    # workflow of the last finished job (its models are still loaded)
        self.started_at: float | None = None
        self.events: list[dict] = []
        threading.Thread(target=self._tail, name="comfyui-log", daemon=True).start()
        self._adopt()

    # ------------------------------------------------------------ settings
    def _load(self) -> dict:
        try:
            d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        s = {**DEFAULTS, **{k: d[k] for k in DEFAULTS if k in d}}
        for k, chk in (("url", check_url), ("extra_args", check_args)):   # a hand-edited file obeys the same rules
            try:
                s[k] = chk(s[k])
            except (ValueError, TypeError):
                s[k] = DEFAULTS[k]
        return s

    def update(self, d: dict) -> dict:
        s = dict(self.settings)
        if d.get("mode") in ("managed", "external"):
            s["mode"] = d["mode"]
        if d.get("url"):
            s["url"] = check_url(d["url"])
        if d.get("port") not in (None, ""):
            s["port"] = max(1024, min(65535, int(d["port"])))
        for k in ("autostart", "custom_nodes"):
            if k in d:
                s[k] = bool(d[k])
        if d.get("vram_mode") in VRAM_MODES:
            s["vram_mode"] = d["vram_mode"]
        if d.get("reserve_vram_gb") not in (None, ""):
            s["reserve_vram_gb"] = round(max(0.0, min(8.0, float(d["reserve_vram_gb"]))), 2)
        if "extra_args" in d:
            s["extra_args"] = check_args(d["extra_args"])
        self.settings = s
        L.LIB.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=1), encoding="utf-8")
        self._st_t = 0
        return s

    @property
    def managed(self) -> bool:
        return self.settings["mode"] == "managed"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.settings['port']}" if self.managed else self.settings["url"].rstrip("/")

    def installed(self) -> bool:
        return PY.exists() and MAIN.exists()

    def _event(self, text: str) -> None:
        self.events.append({"t": time.time(), "text": text})
        del self.events[:-30]

    # ------------------------------------------------------------ HTTP
    def req(self, method: str, path: str, body=None, timeout: float = 30, raw: bool = False):
        data, headers = None, {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                b = resp.read()
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            try:
                j = json.loads(txt)
            except ValueError:
                j = None
            err = ComfyError(f"ComfyUI {method} {path}: HTTP {e.code} {txt[:400]}")
            err.status, err.data = e.code, j
            raise err from None
        except (urllib.error.URLError, OSError) as e:
            raise ComfyError(f"ComfyUI is not reachable at {self.url} ({getattr(e, 'reason', e)})") from None
        if raw:
            return b
        return json.loads(b) if b.strip() else {}

    def reachable(self, timeout: float = 2.0) -> bool:
        try:
            self.req("GET", "/system_stats", timeout=timeout)
            return True
        except ComfyError:
            return False

    # ------------------------------------------------------------ managed server
    def command(self) -> list[str]:
        s = self.settings
        # the user's extra flags first: the studio's own (listen address, port, folders) come last and win
        extra = shlex.split(s["extra_args"], posix=False) if s["extra_args"].strip() else []
        cmd = [str(PY), str(MAIN), *extra, "--listen", "127.0.0.1", "--port", str(s["port"]),
               "--extra-model-paths-config", str(HOME / "extra_model_paths.yaml"),
               "--output-directory", str(HOME / "output"), "--input-directory", str(HOME / "input"),
               "--temp-directory", str(HOME / "temp"), "--user-directory", str(HOME / "user"),
               "--disable-auto-launch", "--preview-method", "none", "--disable-partner-nodes",
               "--reserve-vram", str(s["reserve_vram_gb"]), *VRAM_MODES.get(s["vram_mode"], [])]
        if not s["custom_nodes"]:
            cmd.append("--disable-all-custom-nodes")
        return cmd

    def _adopt(self) -> None:
        """After a studio restart: a managed ComfyUI we started earlier may still run — use it."""
        if self.managed and self.installed() and self.reachable(1.0):
            pid = self._port_pid()
            if pid and self._is_ours(pid):
                self.state, self.started_at = "running", time.time()
                self.log.append(f"[studio] adopted the running managed ComfyUI (pid {pid})")

    def _is_ours(self, pid: int) -> bool:
        try:
            import psutil
            cl = " ".join(psutil.Process(pid).cmdline()).lower()
            return str(HOME).lower() in cl or "comfyui\\main.py" in cl
        except Exception:
            return False

    def _port_pid(self) -> int | None:
        try:
            import psutil
            port = urllib.parse.urlparse(self.url).port or 8188
            for c in psutil.net_connections("tcp4"):
                if c.status == "LISTEN" and c.laddr and c.laddr.port == port and c.pid:
                    return c.pid
        except Exception:
            pass
        return None

    def start(self, wait: bool = False, timeout: float = 240) -> dict:
        if not self.managed:
            raise ComfyError("ComfyUI is set to 'external': start it yourself at " + self.url)
        if not self.installed():
            raise ComfyError("ComfyUI is not installed: run  py -3.12 video/comfyui/setup.py  (see video/comfy-workflows/README.md)")
        with self.lock:
            if self.proc and self.proc.poll() is None:
                pass
            elif self.reachable(1.0):
                pid = self._port_pid()
                if not (pid and self._is_ours(pid)):
                    raise ComfyError(f"port {self.settings['port']} is used by another program (pid {pid}); "
                                     "switch to 'external' mode to use it, or change the port")
                self.state = "running"
            else:
                self.error = None
                self.state = "starting"
                self.started_at = time.time()
                for d in ("input", "output", "temp", "user"):
                    (HOME / d).mkdir(exist_ok=True)
                env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                       "COMFYUI_DISABLE_PRODUCT_TIPS": "1"}
                env.pop("PYTHONPATH", None)
                self.log.append("[studio] $ " + " ".join(self.command()))
                # output goes to a file, not a pipe: ComfyUI keeps running (and logging) when the studio restarts
                LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                with open(LOG_FILE, "ab") as lf:
                    lf.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start: {' '.join(self.command())}\n".encode())
                    self.proc = subprocess.Popen(self.command(), cwd=str(HOME / "ComfyUI"), stdin=subprocess.DEVNULL,
                                                 stdout=lf, stderr=subprocess.STDOUT, env=env,
                                                 creationflags=NOWIN | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                self._event(f"started ComfyUI (pid {self.proc.pid})")
                threading.Thread(target=self._wait_up, args=(self.proc, timeout), name="comfyui-start", daemon=True).start()
        if wait:
            self.wait_running(timeout)
        return self.status(fresh=True)

    def wait_running(self, timeout: float = 240) -> None:
        t = time.time()
        while time.time() - t < timeout:
            if self.state == "running":
                return
            if self.state == "error":
                raise ComfyError(f"ComfyUI did not start: {self.error}")
            time.sleep(0.5)
        raise ComfyError(f"ComfyUI did not answer within {timeout:.0f} s (see its log on the AI page)")

    def _wait_up(self, proc, timeout) -> None:
        t = time.time()
        while time.time() - t < timeout:
            if proc.poll() is not None:
                self.state, self.error = "error", f"exited with code {proc.returncode}: " + " | ".join(list(self.log)[-4:])
                return
            if self.reachable(1.0):
                self.state = "running"
                self.log.append(f"[studio] ComfyUI answered after {time.time() - t:.1f} s")
                self._event(f"ComfyUI ready after {time.time() - t:.0f} s")
                self._oi = None
                return
            time.sleep(1.0)
        self.state, self.error = "error", f"no answer within {timeout:.0f} s"

    def _tail(self) -> None:
        """Follow video/comfyui/comfyui.log (the managed server writes there) into the in-memory log tail."""
        pos = LOG_FILE.stat().st_size if LOG_FILE.exists() else 0
        buf = b""
        while True:
            time.sleep(1.0)
            try:
                size = LOG_FILE.stat().st_size
            except OSError:
                continue
            if size < pos:
                pos = 0
            if size == pos:
                if self.proc is not None and self.proc.poll() is not None and self.state in ("running", "starting"):
                    self.state, self.error = "error", f"ComfyUI exited (code {self.proc.returncode})"
                    self._event(self.error)
                continue
            with open(LOG_FILE, "rb") as f:
                f.seek(pos)
                buf += f.read(size - pos)
            pos = size
            *lines, buf = buf.split(b"\n")
            for raw in lines:
                line = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode("utf-8", "replace"))
                for part in line.split("\r"):
                    if part.strip():
                        self.log.append(part.strip()[:400])

    def stop(self) -> dict:
        if not self.managed:
            raise ComfyError("ComfyUI is 'external': the Studio does not stop it")
        with self.lock:
            pid = None
            if self.proc and self.proc.poll() is None:
                pid = self.proc.pid
            else:
                p = self._port_pid()
                if p and self._is_ours(p):
                    pid = p
            self.state = "stopped"
            if pid:
                try:
                    self.req("POST", "/interrupt", {}, timeout=3)
                except ComfyError:
                    pass
                _kill_tree(pid)
                self._event(f"stopped ComfyUI (pid {pid})")
            self.proc = None
            self._oi = None
            self.last_workflow = None
        return self.status(fresh=True)

    def ensure_running(self, timeout: float = 240) -> None:
        """Before a job: managed + autostart -> start and wait; otherwise it must answer."""
        if self.reachable(3.0):
            if self.managed and self.state != "running":
                self.state = "running"
            return
        if self.managed and self.settings["autostart"]:
            self.start()
            self.wait_running(timeout)
            return
        raise ComfyError(f"ComfyUI is not running at {self.url}" + (" — start it on the AI page" if self.managed else ""))

    # ------------------------------------------------------------ status
    def pids(self) -> set:
        """ComfyUI's processes (for the monitor's 'ComfyUI' owner): the managed process tree or the
        process listening on a local ComfyUI port."""
        out = set()
        if self.proc and self.proc.poll() is None:
            out.add(self.proc.pid)
        host = urllib.parse.urlparse(self.url).hostname or ""
        if host in ("127.0.0.1", "localhost", "::1") and (self.state in ("running", "starting") or not self.managed):
            t, cached = self._listen
            if time.time() - t > 10:
                p = self._port_pid()
                cached = {p} if p else set()
                self._listen = (time.time(), cached)
            out |= cached
        return out

    def vram_mb(self) -> int | None:
        if self.sampler is None or not self.sampler.latest:
            return None
        return self.sampler.latest.get("vc")

    def status(self, fresh: bool = False) -> dict:
        if not fresh and time.time() - self._st_t < 1.5 and self._st:
            return self._st
        st = {"installed": self.installed(), "mode": self.settings["mode"], "url": self.url, "settings": self.settings,
              "state": self.state if self.managed else "external", "error": self.error, "reachable": False,
              "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
              "queue_running": 0, "queue_pending": 0, "busy": False, "version": None, "devices": [],
              "vram_mb": self.vram_mb(), "last_free": self.last_free, "events": self.events[-8:],
              "uptime_s": round(time.time() - self.started_at) if self.started_at and self.state == "running" else None,
              "command": " ".join(self.command()) if self.managed else None}
        try:
            ss = self.req("GET", "/system_stats", timeout=2)
            q = self.req("GET", "/queue", timeout=2)
            st["reachable"] = True
            st["version"] = (ss.get("system") or {}).get("comfyui_version")
            st["pytorch"] = (ss.get("system") or {}).get("pytorch_version")
            st["devices"] = [{k: d.get(k) for k in ("name", "type", "vram_total", "vram_free", "torch_vram_total",
                                                    "torch_vram_free")} for d in ss.get("devices") or []]
            st["queue_running"] = len(q.get("queue_running") or [])
            st["queue_pending"] = len(q.get("queue_pending") or [])
            st["busy"] = st["queue_running"] > 0
            if self.managed and self.state in ("stopped", "error"):
                pid = self._port_pid()
                if pid and self._is_ours(pid):
                    self.state = "running"
                    st["state"] = "running"
        except ComfyError as e:
            if not self.managed:
                st["error"] = str(e)
        if self.managed and self.state == "running" and not st["reachable"]:
            st["state"] = "starting" if self.proc and self.proc.poll() is None else "stopped"
            if st["state"] == "stopped":
                self.state = "stopped"
        st["log"] = list(self.log)[-40:]
        self._st, self._st_t = st, time.time()
        return st

    # ------------------------------------------------------------ memory
    def holds_vram(self, min_mb: int = 400) -> bool:
        v = self.vram_mb()
        return bool(v and v > min_mb)

    def free(self, why: str = "", wait: float = 20.0) -> dict:
        """POST /free {unload_models, free_memory}; then wait until ComfyUI's VRAM (monitor) drops."""
        before = self.vram_mb()
        nv0 = (self.sampler.latest or {}).get("vu") if self.sampler else None
        t = time.time()
        try:
            self.req("POST", "/free", {"unload_models": True, "free_memory": True}, timeout=10)
        except ComfyError as e:
            r = {"ok": False, "error": str(e), "why": why, "t": time.time()}
            self.last_free = r
            return r
        after, nv1 = before, nv0
        if self.sampler is not None:
            while time.time() - t < wait:
                s = self.sampler.wait_next(time.time(), timeout=2.0) or {}
                after, nv1 = s.get("vc"), s.get("vu")
                if after is None or after < 600 or (before and after < before * 0.3):
                    break
        r = {"ok": True, "why": why, "t": time.time(), "seconds": round(time.time() - t, 1), "comfy_mb_before": before,
             "comfy_mb_after": after, "gpu_used_mb_before": nv0, "gpu_used_mb_after": nv1}
        self.last_free = r
        self.last_workflow = None
        self._event(f"/free ({why or 'manual'}): ComfyUI VRAM {_gb(before)} → {_gb(after)} GB")
        return r

    # ------------------------------------------------------------ nodes / files
    def object_info(self, force: bool = False) -> dict:
        if not force and self._oi and time.time() - self._oi[0] < 120:
            return self._oi[1]
        oi = self.req("GET", "/object_info", timeout=60)
        self._oi = (time.time(), oi)
        return oi

    def object_info_cached(self) -> dict | None:
        """object_info if ComfyUI answers (cached), else None — for validation in listings."""
        if self._oi and time.time() - self._oi[0] < 300:
            return self._oi[1]
        try:
            if self.reachable(1.5):
                return self.object_info()
        except ComfyError:
            pass
        return None

    def upload_image(self, path: Path, name: str | None = None) -> str:
        """POST /upload/image (multipart) -> the name to put into LoadImage."""
        boundary = "----studio" + secrets.token_hex(8)
        fname = name or Path(path).name
        ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(
            Path(fname).suffix.lower(), "application/octet-stream")
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + Path(path).read_bytes() + \
            (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\nstudio\r\n"
             f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
        r = urllib.request.Request(self.url + "/upload/image", data=body, method="POST",
                                   headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                d = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ComfyError(f"image upload to ComfyUI failed: {e}") from None
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    def download(self, f: dict, dest: Path) -> Path:
        q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder") or "",
                                    "type": f.get("type") or "output"})
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.req("GET", "/view?" + q, timeout=300, raw=True))
        return dest

    # ------------------------------------------------------------ running a prompt
    def run(self, graph: dict, on_event, cancelled, job: dict | None = None) -> dict:
        """Queue `graph`, follow it over the WebSocket (or /history polling), return
        {prompt_id, outputs, files: [...], seconds, timeline}. on_event(dict) gets
        {"stage": "queued"|"load"|"denoise"|"decode"|"encode", ...}."""
        cid = "studio-" + secrets.token_hex(6)
        ws = None
        try:
            from websockets.sync.client import connect
            ws = connect(self.url.replace("http", "ws", 1) + f"/ws?clientId={cid}", open_timeout=10, max_size=None)
        except Exception as e:  # noqa: BLE001 - progress falls back to polling
            if job is not None:
                job["log"].append(f"websocket unavailable ({e!r}); polling /history")
        try:
            try:
                res = self.req("POST", "/prompt", {"prompt": graph, "client_id": cid}, timeout=60)
            except ComfyError as e:
                raise ComfyError(_prompt_error(getattr(e, "data", None)) or str(e)) from None
            pid = res["prompt_id"]
            if job is not None:
                job["comfy_prompt_id"] = pid
            if res.get("node_errors"):
                raise ComfyError(_prompt_error(res) or "ComfyUI rejected the workflow")
            tr = _Tracker(graph, on_event)
            t0 = time.time()
            if ws is not None:
                done = self._follow_ws(ws, pid, tr, cancelled)
            else:
                done = self._follow_poll(pid, tr, cancelled)
            if done == "cancelled":
                raise Cancelled()
            hist = {}
            for _ in range(20):
                h = self.req("GET", f"/history/{pid}", timeout=30)
                hist = h.get(pid) or {}
                if hist.get("status", {}).get("completed") is not None or hist.get("outputs"):
                    break
                time.sleep(0.5)
            stat = hist.get("status") or {}
            if stat.get("status_str") == "error" or done == "error":
                raise ComfyError(tr.error or _status_error(stat) or "ComfyUI reported an error")
            return {"prompt_id": pid, "outputs": hist.get("outputs") or {}, "seconds": round(time.time() - t0, 1),
                    "timeline": tr.timeline(), "steps_seen": tr.steps_total, "cached_nodes": tr.cached}
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001
                    pass

    def cancel_prompt(self, pid: str | None) -> None:
        if not pid:
            return
        for path, body in (("/queue", {"delete": [pid]}), ("/interrupt", {"prompt_id": pid})):
            try:
                self.req("POST", path, body, timeout=10)
            except ComfyError:
                pass

    def _follow_ws(self, ws, pid: str, tr, cancelled) -> str:
        sent = None
        while True:
            if cancelled() and sent is None:
                sent = time.time()
                self.cancel_prompt(pid)
            if sent and time.time() - sent > 30:
                return "cancelled"
            try:
                msg = ws.recv(timeout=1.0)
            except TimeoutError:
                if sent and not self._queued(pid):              # deleted before it started: no event will come
                    return "cancelled"
                if not tr.started and self._finished(pid):      # missed the messages (e.g. everything cached)
                    return "done"
                continue
            except Exception:  # noqa: BLE001 - connection lost: continue by polling
                return self._follow_poll(pid, tr, cancelled)
            if isinstance(msg, bytes):
                continue                                    # preview images
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            typ, data = m.get("type"), m.get("data") or {}
            if data.get("prompt_id") not in (None, pid):
                continue
            r = tr.handle(typ, data)
            if r == "done":
                return "done"
            if r == "error":
                return "error"
            if r == "interrupted":
                return "cancelled" if sent else "error"

    def _queued(self, pid: str) -> bool:
        try:
            q = self.req("GET", "/queue", timeout=5)
        except ComfyError:
            return True
        return any(len(x) > 1 and x[1] == pid for x in (q.get("queue_running") or []) + (q.get("queue_pending") or []))

    def _finished(self, pid: str) -> bool:
        try:
            h = self.req("GET", f"/history/{pid}", timeout=10)
            return pid in h
        except ComfyError:
            return False

    def _follow_poll(self, pid: str, tr, cancelled) -> str:
        sent = None
        while True:
            if cancelled() and sent is None:
                sent = time.time()
                self.cancel_prompt(pid)
            if sent and time.time() - sent > 30:
                return "cancelled"
            try:
                h = self.req("GET", f"/history/{pid}", timeout=10)
            except ComfyError:
                h = {}
            if pid in h:
                st = h[pid].get("status") or {}
                if sent:
                    return "cancelled"
                return "error" if st.get("status_str") == "error" else "done"
            tr.handle("poll", {})
            time.sleep(1.5)


class _Tracker:
    """Maps ComfyUI events to Studio job stages: loading -> denoise (sampler steps) -> decode -> encode."""

    def __init__(self, graph: dict, on_event):
        self.graph, self.on = graph, on_event
        self.samplers = [nid for nid, n in graph.items() if n.get("class_type") in SAMPLERS]
        self.expected = {nid: _sampler_steps(graph[nid]) for nid in self.samplers}
        self.steps_total = sum(v for v in self.expected.values() if v) or None
        self.done_steps: dict[str, int] = {}
        self.current = None
        self.started = False
        self.error = None
        self.cached: list[str] = []
        self.t: dict[str, float] = {"start": time.time()}
        self.node_t: list[tuple[str, str, float]] = []

    def total(self) -> int:
        tot = 0
        for nid in self.samplers:
            tot += self.expected.get(nid) or self.done_steps.get(nid, 0) or 0
        return max(tot, sum(self.done_steps.values()), 1)

    def handle(self, typ: str, d: dict):
        if typ == "execution_start":
            self.started = True
            self.t["exec"] = time.time()
            self.on({"stage": "load"})
        elif typ == "execution_cached":
            self.cached = [str(x) for x in d.get("nodes") or []]
            for nid in self.cached:
                if nid in self.samplers:
                    self.done_steps[nid] = self.expected.get(nid) or 0
        elif typ == "executing":
            node = d.get("node")
            if node is None:
                return "done"
            node = str(node)
            self.current = node
            self.node_t.append((node, (self.graph.get(node) or {}).get("class_type", "?"), time.time()))
            ct = (self.graph.get(node) or {}).get("class_type", "")
            if ct in SAMPLERS:
                self.t.setdefault("denoise", time.time())
            elif self.t.get("denoise") and all(s in self.done_steps for s in self.samplers):
                if "Decode" in ct:
                    self.t.setdefault("decode", time.time())
                    self.on({"stage": "decode"})
                elif ct.startswith("Save") or ct in ("CreateVideo", "VHS_VideoCombine"):
                    self.t.setdefault("encode", time.time())
                    self.on({"stage": "encode"})
            elif not self.t.get("denoise"):
                self.on({"stage": "load", "node": ct})
        elif typ == "progress":
            node = str(d.get("node") or self.current or "")
            if node in self.samplers:
                v, mx = int(d.get("value") or 0), int(d.get("max") or 1)
                if not self.expected.get(node):
                    self.expected[node] = mx
                self.done_steps[node] = v
                done = sum(self.done_steps.values())
                self.on({"stage": "denoise", "done": done, "total": self.total()})
        elif typ == "executed":
            node = str(d.get("node") or "")
            if node in self.samplers:
                self.done_steps[node] = self.expected.get(node) or self.done_steps.get(node, 0)
        elif typ == "execution_success":
            return "done"
        elif typ == "execution_error":
            self.error = (f"{d.get('node_type')} (node {d.get('node_id')}): {d.get('exception_type', '')}: "
                          f"{(d.get('exception_message') or '').strip()}")[:800]
            return "error"
        elif typ == "execution_interrupted":
            return "interrupted"
        return None

    def timeline(self) -> dict:
        t = self.t
        end = time.time()
        out = {"load_s": round((t.get("denoise") or end) - (t.get("exec") or t["start"]), 1),
               "denoise_s": round((t.get("decode") or t.get("encode") or end) - t["denoise"], 1) if t.get("denoise") else None,
               "decode_s": round((t.get("encode") or end) - t["decode"], 1) if t.get("decode") else None,
               "encode_s": round(end - t["encode"], 1) if t.get("encode") else None,
               "queue_s": round((t.get("exec") or t["start"]) - t["start"], 1)}
        return out


def _sampler_steps(n: dict) -> int | None:
    i = n.get("inputs") or {}
    ct = n.get("class_type")
    steps = i.get("steps")
    if not isinstance(steps, int):
        return None
    if ct == "KSamplerAdvanced":
        s, e = i.get("start_at_step", 0), i.get("end_at_step", steps)
        if isinstance(s, int) and isinstance(e, int):
            return max(0, min(e, steps) - s)
    return steps


def _prompt_error(d) -> str | None:
    if not isinstance(d, dict):
        return None
    parts = []
    e = d.get("error")
    if isinstance(e, dict):
        parts.append(f"{e.get('message', '')} {e.get('details', '')}".strip())
    elif e:
        parts.append(str(e))
    for nid, ne in (d.get("node_errors") or {}).items():
        for er in ne.get("errors") or []:
            parts.append(f"node {nid} ({ne.get('class_type')}): {er.get('message')} — {er.get('details')}")
    return "; ".join(p for p in parts if p)[:1200] or None


def _status_error(st: dict) -> str | None:
    for m in st.get("messages") or []:
        if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error":
            d = m[1]
            return f"{d.get('node_type')} (node {d.get('node_id')}): {d.get('exception_message', '').strip()}"[:800]
    return None


def _gb(mb) -> str:
    return "?" if mb is None else f"{mb / 1024:.1f}"


def output_files(outputs: dict, nodes: list[str] | None = None) -> list[dict]:
    """Files in a /history outputs dict (SaveVideo -> images+animated, VHS -> gifs, SaveImage -> images …)."""
    out = []
    for nid, o in (outputs or {}).items():
        if nodes and str(nid) not in nodes:
            continue
        for key, val in (o or {}).items():
            if isinstance(val, list):
                for f in val:
                    if isinstance(f, dict) and f.get("filename"):
                        out.append({**f, "node": str(nid), "key": key})
    return out
