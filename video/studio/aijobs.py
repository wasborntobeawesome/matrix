"""GPU job queue for the local generative-AI layers (separate from the render queue).

One GPU job at a time. Kinds:
  broll      Wan2.2 text-to-video clip -> library/broll/<id>/   (video/ai/broll.py)
  portrait   Wan2.2 text-to-image portrait -> library/portraits/<id>/   (video/ai/portrait.py)
  presenter  EchoMimicV3 talking head on a project's voice-over -> projects/<id>/presenter/   (video/ai/avatar.py)

The AI scripts run as subprocesses with video/ai/.venv (torch cu128). They print
"@@PROGRESS {json}" per diffusion step and "@@RESULT {json}" at the end; the rest of the output
becomes the job's log tail. Before a job starts, free VRAM is checked with nvidia-smi; when it
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
import presenter as PR
from brief import normalize
from store import Project, StoreError, now

VIDEO = Path(__file__).resolve().parent.parent
AI = VIDEO / "ai"
AI_PY = AI / ".venv" / "Scripts" / "python.exe"
JOBS_FILE = L.LIB / "ai-jobs.json"
TIMINGS_FILE = L.LIB / "ai-timings.json"
TMP = L.LIB / "tmp"
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
KINDS = ("broll", "portrait", "presenter")

# VRAM a job needs before it starts (GB, as nvidia-smi "free"): measured peak torch-reserved
# memory + ~0.5 GB CUDA context. Updated from real runs (ai-timings.json).
NEED_GB = {"broll/draft": 7.7, "broll/final": 11.5, "portrait/final": 7.7, "presenter/draft": 8.2, "presenter/final": 11.1}
# Time model per kind/preset: load + unit * per_unit + tail (seconds). units = steps (b-roll,
# portrait) or seconds of talking head to generate (presenter). Seeded with measured runs
# (video/ai/README.md + this studio's own draft runs); replaced by real job timings as they finish.
TIME_DEFAULTS = {
    "broll/draft": {"load": 24, "per_unit": 4.3, "tail": 36},
    "broll/final": {"load": 15, "per_unit": 43.0, "tail": 70},
    "portrait/final": {"load": 25, "per_unit": 0.6, "tail": 5},
    "presenter/draft": {"load": 100, "per_unit": 23.5, "tail": 15},
    "presenter/final": {"load": 100, "per_unit": 66.0, "tail": 20},
}
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
    {"what": "ffmpeg (gyan.dev build, --enable-gpl)", "licence": "GPL-3", "commercial": True,
     "note": "Fine to run as a tool; don't ship it inside a closed product."},
]
DISCLOSURE = ("A generated or animated presenter is synthetic media: most platforms require an "
              "\"AI-generated\" label. Only animate a real person with their permission.")


class Cancelled(Exception):
    pass


def _key(kind: str, preset: str | None) -> str:
    return f"{kind}/{preset or 'final'}"


class AIJobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.current: str | None = None
        self._saved = 0.0
        self.timings = self._load_timings()
        self.monitor = gpu.Monitor(ai_pids=self._ai_pids)
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

    def _learn(self, key: str, load: float | None, per_unit: float | None, tail: float | None, need_gb: float | None) -> None:
        cur = self.timings.setdefault(key, dict(TIME_DEFAULTS.get(key, {"load": 30, "per_unit": 10, "tail": 30})))
        measured = cur.get("runs", 0)
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

    def estimate(self, kind: str, preset: str | None, units: float) -> float:
        t = self.timings.get(_key(kind, preset)) or {"load": 30, "per_unit": 10, "tail": 30}
        return round(t["load"] + units * t["per_unit"] + t["tail"])

    def need_gb(self, kind: str, preset: str | None) -> float:
        k = _key(kind, preset)
        return float((self.timings.get(k) or {}).get("need_gb") or NEED_GB.get(k, 8.0))

    # ------------------------------------------------------------ public API
    def submit(self, kind: str, params: dict, project: str | None = None, label: str = "", units: float = 0,
               preset: str | None = None) -> dict:
        job = {"id": secrets.token_hex(6), "kind": kind, "project": project, "params": params, "preset": preset,
               "label": label, "status": "queued", "stage": "queued", "progress": 0.0, "message": "", "error": None,
               "result": None, "created": now(), "started": None, "finished": None, "eta": None, "elapsed": 0,
               "estimate": self.estimate(kind, preset, units), "units": units, "need_gb": self.need_gb(kind, preset),
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
            j["_cancel"] = True
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
        return {p.pid for p in self.procs.values() if p.poll() is None}

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
            runner = {"broll": self._broll, "portrait": self._portrait, "presenter": self._presenter}[job["kind"]]
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

    def _stepper(self, job: dict, total_units: float, p0: float, p1: float, tail_est: float):
        """Progress callback for denoising steps: progress p0..p1, ETA from the measured step time."""
        st = {"t_first": None, "n_first": 0, "tail_t": None}

        def on(ev: dict) -> None:
            stage, done, total = ev.get("stage"), ev.get("done", 0), ev.get("total") or 1
            el = time.time() - job["started"]
            if stage == "load":
                job.update(stage="loading", message="loading the model…", progress=round(p0 * 0.5, 3))
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
        pr = L.BROLL_PRESETS[job["preset"]]
        w, h = pr[prm.get("orientation", "portrait")]
        cid = L.broll_key(prm["prompt"], prm["seed"], pr["steps"], w, h, pr["frames"])
        if L.broll_get(cid):
            self._assign_broll(job, cid)
            return {"clip": cid, "cached": True}
        TMP.mkdir(parents=True, exist_ok=True)
        tmp = TMP / f"{job['id']}.mp4"
        tl = self.timings.get(_key("broll", job["preset"]), {})
        res = self._proc(job, ["broll.py", "--prompt", prm["prompt"], "--negative", prm.get("negative") or L.NEGATIVE,
                               "--seed", str(prm["seed"]), "--steps", str(pr["steps"]), "--width", str(w),
                               "--height", str(h), "--frames", str(pr["frames"]), "--out-file", str(tmp), "--progress"],
                         self._stepper(job, pr["steps"], 0.06, 0.82, tl.get("tail", 40)))
        job.update(stage="library", message="adding to the library (VP9 + thumbnail)…", progress=0.97)
        t = time.time()
        meta = L.broll_add(tmp, {"id": cid, "prompt": prm["prompt"], "negative": prm.get("negative") or L.NEGATIVE,
                                 "seed": prm["seed"], "steps": pr["steps"], "guidance": res.get("guidance", 5.0),
                                 "width": w, "height": h, "frames": pr["frames"], "fps": res.get("fps", 24),
                                 "seconds": res.get("seconds"), "preset": job["preset"], "source": "generated",
                                 "gen_seconds": res.get("gen_seconds"), "load_seconds": res.get("load_seconds"),
                                 "job_seconds": round(time.time() - job["started"]),
                                 "peak_vram_allocated_gb": res.get("peak_vram_allocated_gb"),
                                 "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb")}, move=True)
        post = time.time() - t
        self._learn(_key("broll", job["preset"]), res.get("load_seconds"),
                    (res.get("denoise_seconds") or 0) / pr["steps"], (res.get("decode_seconds") or 0) + post,
                    (res.get("peak_vram_reserved_gb") or 0) + 0.5 if res.get("peak_vram_reserved_gb") else None)
        self._assign_broll(job, cid)
        return {"clip": cid, "seconds": meta.get("duration"), "gen_seconds": res.get("gen_seconds"),
                "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb")}

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
                s["background"] = {"type": "broll", "clip": cid, "dim": a.get("dim", bg.get("dim", 0.55)),
                                   "blur": a.get("blur", bg.get("blur", 0))}
                p.save_brief(normalize(b, p.assets())[0])
                job.setdefault("result_note", f"assigned to scene '{a['scene']}'")
                return

    def _portrait(self, job: dict) -> dict:
        prm = job["params"]
        TMP.mkdir(parents=True, exist_ok=True)
        tmp = TMP / f"{job['id']}.png"
        tl = self.timings.get("portrait/final", {})
        res = self._proc(job, ["portrait.py", "--out", str(tmp), "--seed", str(prm["seed"]), "--prompt", prm["prompt"],
                               "--steps", str(prm.get("steps", 40)), "--progress"],
                         self._stepper(job, 40, 0.1, 0.9, tl.get("tail", 5)))
        m = L.portrait_add(tmp, {"source": "generated", "fictional": True, "prompt": res.get("prompt", prm["prompt"]),
                                 "seed": prm["seed"], "steps": res.get("steps"), "name": prm.get("name") or "Generated presenter",
                                 "gen_seconds": res.get("gen_seconds"), **L.WAN})
        tmp.unlink(missing_ok=True)
        self._learn("portrait/final", res.get("load_seconds"), (res.get("gen_seconds") or 0) / max(1, res.get("steps") or 40),
                    5, (res.get("peak_vram_reserved_gb") or 0) + 0.5 if res.get("peak_vram_reserved_gb") else None)
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
            build.generate(p)
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
            jf = d / f"job-{job['id']}.json"
            jf.write_text(json.dumps({"portrait": str(portrait), **{k: st[k] for k in ("width", "height", "steps", "teacache", "seed")},
                                      "segments": [{"wav": x["wav"], "out": x["out"], "min_dur": x["dur"]} for x in todo]}),
                          encoding="utf-8")
            tlm = self.timings.get(_key("presenter", quality), {})
            try:
                res = self._proc(job, ["avatar.py", "--job", str(jf)],
                                 self._stepper(job, len(todo) * st["steps"], 0.08, 0.92, tlm.get("tail", 20)))
            finally:
                jf.unlink(missing_ok=True)
        job.update(stage="assemble", message="assembling the presenter video…", progress=0.95)
        t = time.time()
        PR.assemble(pl, portrait, tl["duration"])
        post = time.time() - t
        if res.get("video_seconds"):
            self._learn(_key("presenter", quality), res.get("load_seconds"), res["gen_seconds"] / res["video_seconds"],
                        post, (res.get("peak_vram_reserved_gb") or 0) + 0.5 if res.get("peak_vram_reserved_gb") else None)
        p.touch()
        return {"video": Path(pl["file"]).name, "key": pl["key"], "segments": len(todo),
                "generated_seconds": res.get("video_seconds"), "gen_seconds": res.get("gen_seconds"),
                "peak_vram_reserved_gb": res.get("peak_vram_reserved_gb")}


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NOWIN)


def _kill_orphan(pid: int) -> None:
    """After a restart: kill a leftover AI subprocess, but only if it really is one of ours."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}').CommandLine"],
                           capture_output=True, text=True, timeout=20, creationflags=NOWIN)
        cl = (r.stdout or "").lower()
        if any(s in cl for s in ("broll.py", "portrait.py", "avatar.py")):
            _kill_tree(pid)
    except Exception:
        pass
