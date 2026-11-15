"""GPU job queue for the local generative-AI layers (separate from the render queue).

One GPU job at a time. Kinds:
  broll      text/image-to-video clip from a provider (providers.py) -> library/broll/<id>/
  portrait   Wan2.2 text-to-image portrait -> library/portraits/<id>/
  presenter  EchoMimicV3 talking head on a project's voice-over -> projects/<id>/presenter/
  music      ACE-Step v1.5 instrumental track -> library/music/<id>/ (music.add_track, source "generated")

Jobs run in warm model workers (workers.py -> video/ai/worker.py, video/ai/.venv, torch cu128) that keep
the weights in RAM between jobs. They print "@@PROGRESS {json}" per diffusion step and "@@RESULT {json}"
at the end; the rest of the output becomes the job's log tail. Before a job starts, free VRAM is checked with nvidia-smi; when it
is below what the job needs the job stays queued ("waiting for GPU memory") until memory frees
up or the user clicks "start anyway". Jobs are persisted in library/ai-jobs.json; after a server
restart, jobs that were queued or running show as "interrupted" (and can be retried).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

import build
import gpu
import library as L
import music as MU
import presenter as PR
import providers as P
import workers as WK
from brief import normalize
from store import Project, StoreError, now

VIDEO = Path(__file__).resolve().parent.parent
AI = VIDEO / "ai"
AI_PY = AI / ".venv" / "Scripts" / "python.exe"
JOBS_FILE = L.LIB / "ai-jobs.json"
TIMINGS_FILE = L.LIB / "ai-timings.json"
TMP = L.LIB / "tmp"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
KINDS = ("broll", "portrait", "presenter", "music")

# VRAM a job needs before it starts (GB, as nvidia-smi "free"): measured peak torch-reserved
# memory + ~0.5 GB CUDA context. Updated from real runs (ai-timings.json).
NEED_GB = {"broll/draft": 7.7, "broll/final": 11.5, "portrait/final": 7.7, "presenter/draft": 8.2, "presenter/final": 11.1,
           "music/final": 8.6, **P.NEED_GB}
# Time model per kind/preset: load + unit * per_unit + tail (seconds). units = steps (b-roll,
# portrait) or seconds of talking head to generate (presenter). Seeded with measured runs
# (video/ai/README.md + this studio's own draft runs); replaced by real job timings as they finish.
TIME_DEFAULTS = {
    "broll/draft": {"load": 24, "per_unit": 4.3, "tail": 36},
    "broll/final": {"load": 12, "per_unit": 23.5, "tail": 115},   # free GPU; 43 s/step when it spills
    "portrait/final": {"load": 25, "per_unit": 0.6, "tail": 5},
    "presenter/draft": {"load": 100, "per_unit": 23.5, "tail": 15},
    "presenter/final": {"load": 70, "per_unit": 55.0, "tail": 5},   # free GPU, model offload
    "music/final": {"load": 12, "per_unit": 0.25, "tail": 3},        # units = seconds of audio (ACE-Step turbo, 8 steps)
    **P.TIME_DEFAULTS,
}
WARM_LOAD = 2.0   # seconds to start a job whose models are already in RAM (the first estimate before it is measured)
LICENCES = [
    {"what": "Wan2.2-TI2V-5B weights + code (Wan-AI) — b-roll and generated portraits", "licence": "Apache-2.0",
     "commercial": True, "note": "You are responsible for generated content; no illegal or harmful use (model card)."},
    {"what": "UMT5-XXL text encoder (in the Wan repos)", "licence": "Apache-2.0", "commercial": True, "note": ""},
    {"what": "EchoMimicV3 code + Flash weights (Ant Group / BadToBest) — talking head", "licence": "Apache-2.0",
     "commercial": True, "note": "Used unmodified from github.com/antgroup/echomimic_v3."},
    {"what": "Wan2.1-Fun-V1.1-1.3B-InP (alibaba-pai) — EchoMimic base", "licence": "Apache-2.0", "commercial": True,
     "note": "Includes the open-clip XLM-RoBERTa ViT-H/14 image encoder; the original OpenCLIP was trained on LAION "
             "(dataset-provenance caveat)."},
    {"what": "chinese-wav2vec2-base (TencentGameMate) — audio features", "licence": "MIT", "commercial": True,
     "note": "Trained on Chinese speech (WenetSpeech); lip shapes for uz/ru/en/ar are plausible, not dubbing-grade."},
    {"what": "PyTorch / diffusers / transformers / accelerate", "licence": "BSD-3 / Apache-2.0", "commercial": True, "note": ""},
    {"what": "FastWan2.2-TI2V-5B-FullAttn (FastVideo / Hao AI Lab) — fast draft b-roll", "licence": "Apache-2.0",
     "commercial": True, "note": "DMD-distilled fine-tune of Wan2.2-TI2V-5B; shares its UMT5 encoder and VAE (Apache-2.0)."},
    {"what": "Wan2.2-T2V-A14B + Wan2.2-I2V-A14B (Wan-AI) — quality b-roll, animate an image", "licence": "Apache-2.0",
     "commercial": True, "note": "Uses the Wan2.1 VAE and the UMT5-XXL encoder (both Apache-2.0)."},
    {"what": "Wan2.2-Lightning 4-step LoRAs (lightx2v / ModelTC)", "licence": "Apache-2.0", "commercial": True,
     "note": "Distillation LoRAs fused into the A14B weights at load time."},
    {"what": "ACE-Step v1.5 XL turbo (ACE Studio / StepFun) — music", "licence": "MIT", "commercial": True,
     "note": "Model card: trained on licensed, royalty-free/public-domain and synthetic (MIDI) music; generated music may "
             "be used commercially. Its own 48 kHz Oobleck VAE (not Stable Audio's). Check outputs for accidental "
             "similarity to existing songs before publishing."},
    {"what": "Qwen3-Embedding-0.6B (Qwen) — ACE-Step text encoder", "licence": "Apache-2.0", "commercial": True, "note": ""},
    {"what": "ffmpeg (gyan.dev build, --enable-gpl)", "licence": "GPL-3", "commercial": True,
     "note": "Fine to run as a tool; don't ship it inside a closed product."},
    {"what": "nvidia-ml-py / psutil (system monitor)", "licence": "BSD-3", "commercial": True, "note": ""},
]
DISCLOSURE = ("A generated or animated presenter is synthetic media: most platforms require an "
              "\"AI-generated\" label. Only animate a real person with their permission.")


class Cancelled(Exception):
    pass


def _key(kind: str, preset: str | None) -> str:
    return f"{kind}/{preset or 'final'}"


class AIJobs:
    def __init__(self, sampler=None):
        self.jobs: dict[str, dict] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.current: str | None = None
        self.hold = None   # callable -> str | None: a reason why no GPU job may start now ("Make video" voice stage)
        self._saved = 0.0
        self.timings = self._load_timings()
        WK.kill_leftovers()                  # warm workers a previous studio process left behind
        self.pool = WK.Pool()
        self.monitor = gpu.Monitor(ai_pids=self._ai_pids, sampler=sampler)
        self._load()
        threading.Thread(target=self._loop, name="ai-gpu-queue", daemon=True).start()

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        for j in data:
            if j.get("status") in ("queued", "running"):
                was = j["status"]
                if was == "running" and j.get("pid"):
                    _kill_orphan(j["pid"])
                j.update(status="interrupted", stage="interrupted", waiting=False, finished=j.get("finished") or now(),
                         error=f"The studio server restarted while this job was {was}. Click Retry to run it again.")
            self.jobs[j["id"]] = j
        if data:
            self._save(force=True)

    def _save(self, force: bool = False) -> None:
        if not force and time.time() - self._saved < 2.0:
            return
        self._saved = time.time()
        with self.lock:
            data = sorted(self.jobs.values(), key=lambda j: j["created"])[-150:]
            L.LIB.mkdir(parents=True, exist_ok=True)
            tmp = JOBS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps([{k: v for k, v in j.items() if not k.startswith("_")} for j in data],
                                      ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(JOBS_FILE)

    def _load_timings(self) -> dict:
        t = {k: dict(v) for k, v in TIME_DEFAULTS.items()}
        try:
            saved = json.loads(TIMINGS_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                t.setdefault(k, {}).update(v)
        except (OSError, ValueError):
            pass
        return t

    def _learn(self, key: str, load: float | None, per_unit: float | None, tail: float | None, need_gb: float | None,
               warm: bool = False) -> None:
        cur = self.timings.setdefault(key, dict(TIME_DEFAULTS.get(key, {"load": 30, "per_unit": 10, "tail": 30})))
        measured = cur.get("runs", 0)
        if warm and load is not None:        # a warm start says nothing about the cold model load
            cur["load_warm"] = round(load if not cur.get("warm_runs") else 0.6 * cur["load_warm"] + 0.4 * load, 2)
            cur["warm_runs"] = cur.get("warm_runs", 0) + 1
            load = None
        for name, v in (("load", load), ("per_unit", per_unit), ("tail", tail)):
            if v is not None and v > 0:
                # first real measurement replaces the seed value, later ones are averaged
                cur[name] = round(v if not measured else 0.6 * cur[name] + 0.4 * v, 2)
        if need_gb:
            cur["need_gb"] = round(need_gb, 1)
        cur["runs"] = measured + 1
        cur["updated"] = now()
        try:
            TIMINGS_FILE.write_text(json.dumps({k: v for k, v in self.timings.items() if v.get("runs")}, indent=1),
                                    encoding="utf-8")
        except OSError:
            pass

    def estimate(self, kind: str, preset: str | None, units: float, key: str | None = None, warm: bool = False) -> float:
        t = self.timings.get(key or _key(kind, preset)) or {"load": 30, "per_unit": 10, "tail": 30}
        load = t.get("load_warm", WARM_LOAD) if warm else t["load"]
        return round(load + units * t["per_unit"] + t["tail"])

    def need_gb(self, kind: str, preset: str | None) -> float:
        return self.need_gb_key(_key(kind, preset))

    def need_gb_key(self, k: str) -> float:
        return float((self.timings.get(k) or {}).get("need_gb") or NEED_GB.get(k, 8.0))

    def is_warm(self, family: str, keys: list[str]) -> bool:
        w = self.pool.workers.get(family)
        if not w or not w.alive() or not self.pool.settings.get("warm"):
            return False
        have = {m["key"] for m in (w.status.get("models") or [])}
        return all(k in have for k in keys)

    def estimate_broll(self, provider: str, preset: str, i2v: bool = False) -> float:
        pv = P.get(provider)
        fam = pv["family"]["i2v" if i2v else "t2v"]
        return self.estimate("broll", preset, pv["presets"][preset]["steps"], key=P.timing_key(provider, preset),
                             warm=self.is_warm(fam, broll_keys(provider, fam)))

    # ------------------------------------------------------------ public API
    def submit(self, kind: str, params: dict, project: str | None = None, label: str = "", units: float = 0,
               preset: str | None = None) -> dict:
        tkey = P.timing_key(params.get("provider") or P.DEFAULT, preset or "draft") if kind == "broll" else _key(kind, preset)
        est = (self.estimate_broll(params.get("provider") or P.DEFAULT, preset or "draft", bool(params.get("image")))
               if kind == "broll" else self.estimate(kind, preset, units, warm=self.is_warm(*_family_keys(kind))))
        job = {"id": secrets.token_hex(6), "kind": kind, "project": project, "params": params, "preset": preset,
               "label": label, "status": "queued", "stage": "queued", "progress": 0.0, "message": "", "error": None,
               "result": None, "created": now(), "started": None, "finished": None, "eta": None, "elapsed": 0,
               "provider": params.get("provider") if kind == "broll" else None, "timing_key": tkey,
               "estimate": est, "units": units, "need_gb": self.need_gb_key(tkey),
               "force": False, "waiting": False, "log": [], "pid": None}
        with self.lock:
            self.jobs[job["id"]] = job
        self._save(force=True)
        self.wake.set()
        return self.view(job)

    def done_job(self, kind: str, params: dict, result: dict, project=None, label="", preset=None) -> dict:
        """A request that was answered from the cache (no GPU work): recorded as an instant done job."""
        job = {"id": secrets.token_hex(6), "kind": kind, "project": project, "params": params, "preset": preset,
               "label": label, "status": "done", "stage": "cached", "progress": 1.0, "message": "reused from the library",
               "error": None, "result": {**result, "cached": True}, "created": now(), "started": now(),
               "finished": now(), "eta": 0, "elapsed": 0, "estimate": 0, "units": 0, "need_gb": 0, "force": False,
               "waiting": False, "log": [], "pid": None}
        with self.lock:
            self.jobs[job["id"]] = job
        self._save(force=True)
        return self.view(job)

    def view(self, j: dict) -> dict:
        v = {k: val for k, val in j.items() if not k.startswith("_")}
        if j["status"] == "running" and j.get("started"):
            v["elapsed"] = round(time.time() - j["started"])
        if j["status"] == "queued":
            q = [x for x in self.queued()]
            v["position"] = q.index(j["id"]) + 1 if j["id"] in q else 0
        else:
            v["position"] = 0
        return v

    def queued(self) -> list[str]:
        return [j["id"] for j in sorted(self.jobs.values(), key=lambda j: j["created"]) if j["status"] == "queued"]

    def get(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        return self.view(j) if j else None

    def list(self, project: str | None = None, limit: int = 60) -> list[dict]:
        js = sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)
        if project:
            js = [j for j in js if j.get("project") == project]
        return [self.view(j) for j in js[:limit]]

    def active(self, project: str | None = None, kind: str | None = None) -> list[dict]:
        return [self.view(j) for j in self.jobs.values() if j["status"] in ("queued", "running")
                and (project is None or j.get("project") == project) and (kind is None or j["kind"] == kind)]

    def cancel(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if not j:
            return None
        if j["status"] == "queued":
            j.update(status="cancelled", stage="cancelled", waiting=False, finished=now())
        elif j["status"] == "running":
            j["_cancel"] = True             # the warm worker stops at the next step and keeps its weights
            j["message"] = "cancelling…"
            proc = self.procs.get(jid)
            if proc and proc.poll() is None:
                _kill_tree(proc.pid)
        self._save(force=True)
        self.wake.set()
        return self.view(j)

    def start_anyway(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if not j:
            return None
        if j["status"] == "queued":
            j["force"] = True
            j["message"] = "starting without the free-memory check…"
            self._save(force=True)
            self.wake.set()
        return self.view(j)

    def retry(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if not j or j["status"] not in ("error", "cancelled", "interrupted"):
            return None
        return self.submit(j["kind"], j["params"], j.get("project"), j.get("label", ""), j.get("units", 0), j.get("preset"))

    def remove(self, jid: str) -> bool:
        j = self.jobs.get(jid)
        if not j or j["status"] in ("queued", "running"):
            return False
        with self.lock:
            self.jobs.pop(jid, None)
        self._save(force=True)
        return True

    def gpu_status(self, fresh: bool = False) -> dict:
        s = dict(self.monitor.get(fresh=fresh))
        s["current_job"] = self.current
        return s

    def _ai_pids(self) -> set:
        return {p.pid for p in self.procs.values() if p.poll() is None} | set(self.pool.pids())

    # ------------------------------------------------------------ the GPU loop
    def _next(self) -> dict | None:
        q = self.queued()
        return self.jobs[q[0]] if q else None

    def _loop(self) -> None:
        while True:
            job = self._next()
            if not job:
                self.wake.wait(3.0)
                self.wake.clear()
                continue
            reason = self.hold() if self.hold else None
            if reason:
                job.update(waiting=True, stage="waiting_gpu", message=reason)
                self._save()
                self.wake.wait(3.0)
                self.wake.clear()
                continue
            if not job.get("force") and not self._no_gpu_work(job):
                snap = self.monitor.get(fresh=True)
                if snap.get("ok"):
                    free = snap["free_mb"] / 1024
                    if free < job["need_gb"]:
                        ov = snap.get("ovoz_mb")
                        who = (f"Ovoz is using {gpu.gb(ov)} GB" if ov else "other apps are using it") if ov is not None \
                            else f"{gpu.gb(snap['used_mb'])} GB in use"
                        job.update(waiting=True, stage="waiting_gpu",
                                   message=f"waiting for GPU memory: needs ~{job['need_gb']:.1f} GB, "
                                           f"{free:.1f} GB free ({who})")
                        self._save()
                        self.wake.wait(4.0)
                        self.wake.clear()
                        continue
            self._run(job)

    def _no_gpu_work(self, job: dict) -> bool:
        """A presenter job whose segments are all cached only assembles (CPU): no VRAM check."""
        if job["kind"] != "presenter":
            return False
        try:
            p = Project.get(job["project"])
            tl = build.timeline(p)
            pl = PR.plan(p, p.brief, tl, job["preset"])
            return bool(pl) and tl["voiced"] >= tl["voice_needed"] and pl.get("missing_seconds", 1) == 0
        except Exception:
            return False

    def _run(self, job: dict) -> None:
        job.update(status="running", started=now(), stage="starting", waiting=False, message="")
        self.current = job["id"]
        self._save(force=True)
        try:
            runner = {"broll": self._broll, "portrait": self._portrait, "presenter": self._presenter,
                      "music": self._music}[job["kind"]]
            job["result"] = runner(job)
            job.update(status="done", progress=1.0, stage="done", eta=0, message="")
        except Cancelled:
            job.update(status="cancelled", stage="cancelled", message="")
        except Exception as e:  # every failure is reported to the UI
            job.update(status="error", stage="failed", error=str(e) or type(e).__name__)
        finally:
            job["finished"] = now()
            job["elapsed"] = round(job["finished"] - job["started"])
            job["pid"] = None
            self.current = None
            self._save(force=True)

    # ------------------------------------------------------------ subprocess
    def _proc(self, job: dict, cmd: list[str], on_progress) -> dict:
        """Run an AI script, stream its output, return its @@RESULT dict."""
        if not AI_PY.exists():
            raise RuntimeError(f"AI environment missing: {AI_PY} (see video/ai/README.md)")
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([str(AI_PY), *cmd], cwd=str(AI), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                env=env, creationflags=NOWIN | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        self.procs[job["id"]] = proc
        job["pid"] = proc.pid
        self._save(force=True)
        result: dict = {}
        buf = b""
        tqdm_re = re.compile(r"^\s*\d+%\|")

        def handle(line: str) -> None:
            nonlocal result
            for m in re.finditer(r"@@(PROGRESS|RESULT) (\{.*?\})(?=@@|$)", line):
                try:
                    ev = json.loads(m.group(2))
                except ValueError:
                    continue
                if m.group(1) == "RESULT":
                    result = ev
                else:
                    on_progress(ev)
                    self._save()
            rest = re.sub(r"@@(PROGRESS|RESULT) \{.*?\}(?=@@|$)", "", line).strip()
            if rest:
                log = job["log"]
                if log and tqdm_re.match(rest) and tqdm_re.match(log[-1]):
                    log[-1] = rest[:300]
                else:
                    log.append(rest[:300])
                del log[:-40]

        try:
            while True:
                chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for raw in parts:
                    if raw.strip():
                        handle(raw.decode("utf-8", "replace"))
                if job.get("_cancel") and proc.poll() is None:
                    _kill_tree(proc.pid)
            if buf.strip():
                handle(buf.decode("utf-8", "replace"))
            rc = proc.wait()
        finally:
            self.procs.pop(job["id"], None)
        if job.get("_cancel"):
            raise Cancelled()
        if rc != 0 or not result:
            tail = [x for x in job["log"] if not tqdm_re.match(x)][-4:]
            raise RuntimeError(f"{Path(cmd[0]).name} failed (exit {rc}): " + " | ".join(tail))
        return result

    def _task(self, job: dict, family: str, task: dict, keys: list[str], on_progress) -> dict:
        """Run one task in the family's warm worker; PROGRESS -> on_progress, other output -> the log tail."""
        tqdm_re = re.compile(r"^\s*\d+%\|")

        def on_event(kind, data):
            if kind == "PROGRESS":
                on_progress(data)
                self._save()
            elif kind == "LOG":
                log = job["log"]
                if log and tqdm_re.match(data) and tqdm_re.match(log[-1]):
                    log[-1] = data[:300]
                else:
                    log.append(data[:300])
                del log[:-40]
        job["worker"] = family
        try:
            res = self.pool.run(job, family, task, keys, on_event, lambda: bool(job.get("_cancel")))
        except WK.Cancelled:
            raise Cancelled() from None
        job["warm"] = bool(res.get("warm"))
        return res

    def _stepper(self, job: dict, total_units: float, p0: float, p1: float, tail_est: float):
        """Progress callback for denoising steps: progress p0..p1, ETA from the measured step time."""
        st = {"t_first": None, "n_first": 0, "tail_t": None}

        def on(ev: dict) -> None:
            stage, done, total = ev.get("stage"), ev.get("done", 0), ev.get("total") or 1
            el = time.time() - job["started"]
            if stage == "load":
                warm = ev.get("warm")
                job["warm"] = bool(warm)
                job.update(stage="loading", message="warm model (already in RAM)…" if warm else "loading the model…",
                           progress=round(p0 * 0.5, 3))
                job["eta"] = max(0, round(job["estimate"] - el)) if job.get("estimate") else None
            elif stage == "denoise":
                if st["t_first"] is None:
                    st["t_first"], st["n_first"] = time.time(), done
                    job["_load_s"] = el
                f = done / total
                job.update(stage="denoise", progress=round(p0 + (p1 - p0) * f, 3),
                           message=f"step {done}/{total}" + (f" · segment {ev['segment']}/{ev['segments']}" if ev.get("segments") else ""))
                k = done - st["n_first"]
                if k > 0:
                    per = (time.time() - st["t_first"]) / k
                    job["_per_step"] = per
                    job["eta"] = round((total - done) * per + tail_est)
                elif job.get("estimate"):
                    job["eta"] = max(0, round(job["estimate"] - el))
            elif stage in ("decode", "encode"):
                if st["tail_t"] is None:
                    st["tail_t"] = time.time()
                job.update(stage=stage, progress=round(p1 + (0.97 - p1) * (0.3 if stage == "decode" else 0.8), 3),
                           message="decoding frames (VAE)…" if stage == "decode" else "writing the video…")
                job["eta"] = max(5, round(tail_est - (time.time() - st["tail_t"])))
        return on

    # ------------------------------------------------------------ runners
    def _broll(self, job: dict) -> dict:
        prm = job["params"]
        provider = prm.get("provider") or P.DEFAULT
        pv = P.get(provider)
        preset = job["preset"] if job["preset"] in pv["presets"] else "draft"
        pr = pv["presets"][preset]
        orient = prm.get("orientation", "portrait")
        w, h = P.size(provider, preset, orient)
        img = prm.get("image") or None              # {"path", "sha", "label"}: image-to-video ("animate this image")
        if img and not Path(img["path"]).is_file():
            raise RuntimeError(f"the image to animate is gone: {img.get('label') or img['path']}")
        cid = P.clip_key(provider, prm["prompt"], prm["seed"], preset, orient, img["sha"] if img else None)
        if L.broll_get(cid):
            self._assign_broll(job, cid)
            return {"clip": cid, "cached": True}
        TMP.mkdir(parents=True, exist_ok=True)
        tmp = TMP / f"{job['id']}.mp4"
        tkey = P.timing_key(provider, preset)
        tl = self.timings.get(tkey, {})
        fam = pv["family"]["i2v" if img else "t2v"]
        task = {"kind": "broll", "provider": provider, "prompt": prm["prompt"], "negative": prm.get("negative") or L.NEGATIVE,
                "seed": int(prm["seed"]), "steps": pr["steps"], "width": w, "height": h, "frames": pr["frames"],
                "fps": pr["fps"], "guidance": pr["guidance"], "out": str(tmp)}
        if img:
            task["image"] = img["path"]
        res = self._task(job, fam, task, broll_keys(provider, fam),
                         self._stepper(job, pr["steps"], 0.06, 0.82, tl.get("tail", 40)))
        job.update(stage="library", message="adding to the library (VP9 + thumbnail)…", progress=0.97)
        t = time.time()
        meta = L.broll_add(tmp, {"id": cid, "prompt": prm["prompt"], "negative": prm.get("negative") or L.NEGATIVE,
                                 "seed": prm["seed"], "steps": pr["steps"], "guidance": res.get("guidance", pr["guidance"]),
                                 "width": w, "height": h, "frames": pr["frames"], "fps": res.get("fps", pr["fps"]),
                                 "seconds": res.get("seconds"), "preset": preset, "source": "generated",
                                 "provider": provider, "model": pv["model"], "licence": pv["licence"],
                                 "mode": "i2v" if img else "t2v", "image": (img or {}).get("label"),
                                 "image_sha": (img or {}).get("sha"),
                                 "gen_seconds": res.get("gen_seconds"), "load_seconds": res.get("load_seconds"),
                                 "job_seconds": round(time.time() - job["started"]), "warm": res.get("warm"),
                                 "peak_vram_allocated_gb": res.get("peak_vram_allocated_gb"),
                                 "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb"),
                                 "offload": res.get("offload"), "full_gpu": res.get("full_gpu"),
                                 "denoise_seconds": res.get("denoise_seconds"), "decode_seconds": res.get("decode_seconds"),
                                 "worker_ram_gb": res.get("worker_ram_gb"),
                                 "free_vram_at_start_gb": res.get("free_vram_at_start_gb")}, move=True)
        post = time.time() - t
        self._learn(tkey, (res.get("load_seconds") or 0) + (res.get("encode_seconds") or 0),
                    (res.get("denoise_seconds") or 0) / pr["steps"], (res.get("decode_seconds") or 0) + post,
                    _need(res, res.get("full_gpu")), warm=bool(res.get("warm")))
        self._assign_broll(job, cid)
        return {"clip": cid, "provider": provider, "seconds": meta.get("duration"), "gen_seconds": res.get("gen_seconds"),
                "denoise_seconds": res.get("denoise_seconds"), "load_seconds": res.get("load_seconds"),
                "decode_seconds": res.get("decode_seconds"), "warm": res.get("warm"), "offload": res.get("offload"),
                "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb"), "worker_ram_gb": res.get("worker_ram_gb")}

    def _assign_broll(self, job: dict, cid: str) -> None:
        a = job["params"].get("assign")
        if not a or not a.get("project") or not a.get("scene"):
            return
        try:
            p = Project.get(a["project"])
        except StoreError:
            return
        b = p.brief
        for s in b["scenes"]:
            if s["id"] == a["scene"]:
                bg = s.get("background") if isinstance(s.get("background"), dict) else {}
                s["background"] = {**{k: bg[k] for k in ("prompt", "provider", "image") if bg.get(k)},
                                   "type": "broll", "clip": cid, "dim": a.get("dim", bg.get("dim", 0.55)),
                                   "blur": a.get("blur", bg.get("blur", 0))}
                p.save_brief(normalize(b, p.assets())[0])
                job.setdefault("result_note", f"assigned to scene '{a['scene']}'")
                return

    def _portrait(self, job: dict) -> dict:
        prm = job["params"]
        TMP.mkdir(parents=True, exist_ok=True)
        tmp = TMP / f"{job['id']}.png"
        tl = self.timings.get("portrait/final", {})
        steps = int(prm.get("steps", 40))
        res = self._task(job, "wan", {"kind": "portrait", "provider": "wan22-5b", "prompt": prm["prompt"],
                                      "seed": int(prm["seed"]), "steps": steps, "width": 704, "height": 1280,
                                      "out": str(tmp)}, ["umt5", "vae22", "wan22-5b"],
                         self._stepper(job, steps, 0.1, 0.9, tl.get("tail", 5)))
        m = L.portrait_add(tmp, {"source": "generated", "fictional": True, "prompt": res.get("prompt", prm["prompt"]),
                                 "seed": prm["seed"], "steps": res.get("steps"), "name": prm.get("name") or "Generated presenter",
                                 "gen_seconds": res.get("gen_seconds"), **L.WAN})
        tmp.unlink(missing_ok=True)
        self._learn("portrait/final", res.get("load_seconds"), (res.get("denoise_seconds") or 0) / max(1, res.get("steps") or 40),
                    (res.get("decode_seconds") or 0) + 2, (res.get("peak_vram_reserved_gb") or 0) + 0.5
                    if res.get("peak_vram_reserved_gb") else None, warm=bool(res.get("warm")))
        a = prm.get("assign_project")
        if a:
            try:
                p = Project.get(a)
                b = p.brief
                b["presenter"] = {**(b.get("presenter") or {"enabled": True, "mode": "corner", "scenes": "all"}), "portrait": m["id"]}
                p.save_brief(normalize(b, p.assets())[0])
            except StoreError:
                pass
        return {"portrait": m["id"]}

    def _presenter(self, job: dict) -> dict:
        p = Project.get(job["project"])
        quality = job["preset"]
        job.update(stage="voice", message="checking the voice-over…", progress=0.01)
        try:
            build.generate(p, langs=[p.brief.get("language")])   # the presenter speaks the main language
        except build.V.TTSError as e:
            tl0 = build.timeline(p)
            if tl0["voiced"] < tl0["voice_needed"]:
                raise RuntimeError(f"voice-over incomplete and Ovoz is not reachable ({e}). Generate the voice first.") from None
        brief = p.brief
        tl = build.timeline(p)
        wav = build.voiceover(p, tl)
        if not wav:
            raise RuntimeError("this project has no voice-over yet")
        pl = PR.plan(p, brief, tl, quality)
        if not pl:
            raise RuntimeError("the presenter is not enabled and no scene uses the 'presenter' layout")
        if pl.get("error"):
            raise RuntimeError(pl["error"])
        if pl["ready"]:
            return {"video": pl["video"], "key": pl["key"], "cached": True}
        d = PR.pdir(p)
        (d / "seg").mkdir(parents=True, exist_ok=True)
        portrait = PR.crop_portrait(Path(pl["portrait_path"]), pl["w"], pl["h"],
                                    d / f"portrait-{pl['portrait_sha']}-{pl['w']}x{pl['h']}.png")
        todo = [x for x in pl["pieces"] if x["type"] == "seg" and not Path(x["out"]).exists()]
        for x in todo:
            PR.slice_wav(wav, x["start"], x["dur"], Path(x["wav"]))
        st = PR.PRESETS[quality]
        res = {}
        if todo:
            tlm = self.timings.get(_key("presenter", quality), {})
            res = self._task(job, "echomimic", {"portrait": str(portrait), **{k: st[k] for k in ("width", "height", "steps",
                                                                                                    "teacache", "seed")},
                                                "segments": [{"wav": x["wav"], "out": x["out"], "min_dur": x["dur"]}
                                                             for x in todo]}, ["echomimic"],
                             self._stepper(job, len(todo) * st["steps"], 0.08, 0.92, tlm.get("tail", 20)))
        job.update(stage="assemble", message="assembling the presenter video…", progress=0.95)
        t = time.time()
        PR.assemble(pl, portrait, tl["duration"])
        post = time.time() - t
        if res.get("video_seconds"):
            self._learn(_key("presenter", quality), res.get("load_seconds"), res["gen_seconds"] / res["video_seconds"],
                        post, _need(res, res.get("gpu_mode") == "full"), warm=bool(res.get("warm")))
        p.touch()
        return {"video": Path(pl["file"]).name, "key": pl["key"], "segments": len(todo),
                "generated_seconds": res.get("video_seconds"), "gen_seconds": res.get("gen_seconds"),
                "load_seconds": res.get("load_seconds"), "gpu_mode": res.get("gpu_mode"),
                "per_segment": res.get("per_segment"), "assemble_seconds": round(post, 1), "warm": res.get("warm"),
                "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb")}

    def _music(self, job: dict) -> dict:
        """ACE-Step instrumental -> trimmed, faded, checked (duration, loudness, spectrum) -> music library."""
        prm = job["params"]
        TMP.mkdir(parents=True, exist_ok=True)
        raw = TMP / f"{job['id']}.wav"
        dur = float(prm["duration"])
        caption = music_caption(prm)
        tl = self.timings.get("music/final", {})
        # ask for a little more than needed: ACE-Step often fades out early; the silent tail is trimmed below
        gen_dur = min(240.0, dur + 6.0)
        res = self._task(job, "acestep", {"prompt": caption, "duration": gen_dur, "bpm": prm.get("bpm"),
                                         "key": prm.get("key"), "seed": int(prm["seed"]), "steps": 8,
                                         "instrumental": True, "out": str(raw)}, ["acestep"],
                         self._stepper(job, 8, 0.1, 0.85, tl.get("tail", 3)))
        job.update(stage="checking", message="trimming and checking the audio (loudness, spectrum)…", progress=0.92)
        t = time.time()
        ogg = TMP / f"{job['id']}.ogg"
        info = MU.finish_generated(raw, ogg, dur)
        check = MU.analyze(ogg)
        raw.unlink(missing_ok=True)
        if not check.get("ok"):
            ogg.unlink(missing_ok=True)
            raise RuntimeError(f"the generated audio failed the check ({check.get('problem')}): "
                               f"{json.dumps({k: check.get(k) for k in ('duration', 'lufs', 'flatness', 'flux')})}")
        title = (prm.get("title") or "").strip() or (", ".join(x for x in (prm.get("genre"), prm.get("mood")) if x)
                                                     or prm["prompt"])[:60]
        m = MU.add_track(ogg, {"title": title, "artist": "ACE-Step v1.5 (generated locally)", "licence": MU.GENERATED_LICENCE,
                               "source": "generated", "mood": prm.get("mood") or None, "bpm": prm.get("bpm"),
                               "prompt": caption, "genre": prm.get("genre") or None, "seed": int(prm["seed"]),
                               "model": "ACE-Step v1.5 XL turbo", "gen_seconds": res.get("gen_seconds"),
                               "check": check, "key": prm.get("cache_key")}, move=True)
        post = time.time() - t
        self._learn("music/final", res.get("load_seconds"), (res.get("gen_seconds") or 0) / max(1.0, gen_dur), post,
                    (res.get("peak_vram_reserved_gb") or 0) + 0.6 if res.get("peak_vram_reserved_gb") else None,
                    warm=bool(res.get("warm")))
        a = prm.get("assign_project")
        if a:
            try:
                p = Project.get(a)
                b = p.brief
                mu = b.get("music") if isinstance(b.get("music"), dict) else {}
                b["music"] = {**mu, "track": m["id"]}
                p.save_brief(normalize(b, p.assets())[0])
            except StoreError:
                pass
        return {"track": m["id"], "title": m["title"], "duration": m["duration"], "lufs": m["lufs"], "check": check,
                "trim": info, "gen_seconds": res.get("gen_seconds"), "load_seconds": res.get("load_seconds"),
                "warm": res.get("warm"), "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb"),
                "worker_ram_gb": res.get("worker_ram_gb")}


def _need(res: dict, full_gpu) -> float | None:
    """VRAM a job of this kind needs before it may start = offload-mode peak + CUDA context. Full-GPU
    runs only happen when plenty is free, so their (higher) peak is not the minimum and is not learned."""
    if full_gpu or not res.get("peak_vram_reserved_gb"):
        return None
    return res["peak_vram_reserved_gb"] + 0.5


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)


def _kill_orphan(pid: int) -> None:
    """After a restart: kill a leftover AI subprocess, but only if it really is one of ours."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                           capture_output=True, text=True, timeout=20, creationflags=NOWIN)
        cl = (r.stdout or "").lower()
        if any(s in cl for s in ("broll.py", "portrait.py", "avatar.py", "worker.py")):
            _kill_tree(pid)
    except Exception:
        pass


def broll_keys(provider: str, family: str) -> list[str]:
    """Models a b-roll job of this provider needs in its worker (to tell warm from cold)."""
    if family == "wan":
        return ["umt5", "vae22-bf16" if provider == "fastwan-5b" else "vae22", provider]
    return ["umt5", family]


def _family_keys(kind: str) -> tuple[str, list[str]]:
    return {"portrait": ("wan", ["umt5", "vae22", "wan22-5b"]), "presenter": ("echomimic", ["echomimic"]),
            "music": ("acestep", ["acestep"])}.get(kind, ("wan", ["umt5"]))


def music_caption(prm: dict) -> str:
    """ACE-Step caption: genre, mood and the prompt; instrumental only."""
    parts = [prm.get("genre"), prm.get("mood"), prm.get("prompt"), "instrumental, no vocals"]
    return ", ".join(str(x).strip() for x in parts if x and str(x).strip())[:500]
