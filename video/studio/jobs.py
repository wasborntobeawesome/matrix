"""Background jobs: one queue for voice-over, one for renders (one render at a time).

A job is a dict the API returns as-is: {id, kind, project, status, stage,
progress (0..1), message, error, result, created, started, finished, position}.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import build
import exports
from brief import FORMATS
from store import Project, now

VIDEO = Path(__file__).resolve().parent.parent
PY = sys.executable
RENDER = VIDEO / "html" / "render.py"


class Cancelled(Exception):
    pass


class Queue:
    def __init__(self, name: str, runner):
        self.name, self.runner = name, runner
        self.q: queue.Queue = queue.Queue()
        self.pending: list[str] = []
        threading.Thread(target=self._loop, name=f"jobs-{name}", daemon=True).start()

    def put(self, job: dict) -> None:
        self.pending.append(job["id"])
        self.q.put(job)

    def _loop(self) -> None:
        while True:
            job = self.q.get()
            if job["id"] in self.pending:
                self.pending.remove(job["id"])
            if job["status"] == "cancelled":
                continue
            job.update(status="running", started=now(), stage="starting")
            try:
                job["result"] = self.runner(job)
                job.update(status="done", progress=1.0, stage="done")
            except Cancelled:
                job.update(status="cancelled", stage="cancelled")
            except Exception as e:  # report every failure to the UI
                job.update(status="error", error=str(e) or type(e).__name__, stage="failed")
            job["finished"] = now()


class Jobs:
    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.procs: dict[str, subprocess.Popen] = {}
        self.queues = {"voice": Queue("voice", self._voice), "render": Queue("render", self._render)}
        self.gate = None   # callable -> str | None: why voice synthesis must wait now ("Make video" AI stage)

    def _wait_gate(self, job: dict) -> None:
        """Hold voice synthesis while a "Make video" pipeline has the GPU for AI jobs."""
        while self.gate and (reason := self.gate()):
            if job.get("cancel"):
                raise Cancelled()
            job.update(stage="waiting", message=reason)
            time.sleep(2)

    # ---------- public ----------
    def submit(self, kind: str, project: str, **params) -> dict:
        job = {"id": secrets.token_hex(6), "kind": kind, "project": project, "params": params,
               "status": "queued", "stage": "queued", "progress": 0.0, "message": "", "error": None,
               "result": None, "created": now(), "started": None, "finished": None}
        self.jobs[job["id"]] = job
        self.queues[kind].put(job)
        self._prune()
        return self.view(job)

    def view(self, job: dict) -> dict:
        v = {k: val for k, val in job.items()}
        q = self.queues[job["kind"]]
        v["position"] = q.pending.index(job["id"]) + 1 if job["id"] in q.pending else 0
        return v

    def get(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        return self.view(j) if j else None

    def for_project(self, pid: str) -> list[dict]:
        return [self.view(j) for j in sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)
                if j["project"] == pid][:20]

    def cancel(self, jid: str) -> dict | None:
        j = self.jobs.get(jid)
        if not j:
            return None
        if j["status"] == "queued":
            j["status"] = "cancelled"
        elif j["status"] == "running":
            j["cancel"] = True
            proc = self.procs.get(jid)
            if proc and proc.poll() is None:
                proc.kill()
        return self.view(j)

    def busy(self, pid: str, kind: str) -> dict | None:
        for j in self.jobs.values():
            if j["project"] == pid and j["kind"] == kind and j["status"] in ("queued", "running"):
                return self.view(j)
        return None

    def _prune(self) -> None:
        done = [j for j in self.jobs.values() if j["status"] in ("done", "error", "cancelled")]
        for j in sorted(done, key=lambda j: j["created"])[:-200]:
            self.jobs.pop(j["id"], None)

    # ---------- runners ----------
    def _voice(self, job: dict) -> dict:
        p = Project.get(job["project"])
        prm = job["params"]
        ids = [prm["scene"]] if prm.get("scene") else None
        langs = _langs(p, prm)
        self._wait_gate(job)

        def prog(f, msg):
            if job.get("cancel"):
                raise Cancelled()
            job.update(progress=round(0.85 * f, 3), stage="voice", message=msg)

        res = build.generate(p, ids, force=bool(prm.get("force")), progress=prog, langs=langs)

        def aprog(f, msg):
            job.update(progress=round(0.85 + 0.15 * f, 3), stage="timing", message=msg)
        res["timing"] = build.align_all(p, langs, progress=aprog)
        voiced = needed = 0
        for lg in langs:
            tl = build.timeline(p, lang=lg)
            build.voiceover(p, tl)
            voiced += tl["voiced"]
            needed += tl["voice_needed"]
        return {**res, "voiced": voiced, "voice_needed": needed, "languages": langs}

    def _render(self, job: dict) -> dict:
        """One MP4 (+ .srt, .vtt, cover .png) per language x format: <lang>-<fmt>-<timestamp>.mp4."""
        p = Project.get(job["project"])
        prm = job["params"]
        brief = p.brief
        langs = _langs(p, prm)
        fmts = [f for f in (prm.get("formats") or []) if f in FORMATS] or build.formats_of(brief)
        warnings = []
        # 1. voice-over (only what is missing) + word timing
        if not prm.get("skip_voice"):
            job.update(stage="voice", message="voice-over", progress=0.0)
            if build.todo_lines(p, langs):
                self._wait_gate(job)

            def prog(f, msg):
                if job.get("cancel"):
                    raise Cancelled()
                job.update(progress=round(0.12 * f, 3), message=msg)
            try:
                build.generate(p, progress=prog, langs=langs)
            except build.V.TTSError as e:
                if not prm.get("allow_silent"):
                    raise RuntimeError(f"{e}. Fix the voice settings, or render without voice-over.") from None
                warnings.append(f"voice-over skipped: {e}")
        job.update(stage="timing", message="word timing", progress=0.12)
        build.align_all(p, langs, progress=lambda f, m: job.update(progress=round(0.12 + 0.03 * f, 3), message=m))
        # 2. timelines (+ voice-over joins and music mixes, shared by the formats of a language)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        p.renders_dir.mkdir(parents=True, exist_ok=True)
        outputs = [(lg, f) for lg in langs for f in fmts]
        tls = {}
        for lg, f in outputs:
            job.update(stage="timeline", message=f"timeline + audio mix: {lg} {f}")
            tls[(lg, f)] = build.write_render_timeline(p, lg, f)
            if job.get("cancel"):
                raise Cancelled()
        # 3. frames + encode per output in a subprocess (isolated, cancellable)
        total_frames = sum(t["frames"] for t in tls.values()) or 1
        done_frames, results = 0, []
        t_all = time.time()
        for k, (lg, f) in enumerate(outputs):
            tl = tls[(lg, f)]
            tag = f"{lg} {f}" + (f" ({k + 1}/{len(outputs)})" if len(outputs) > 1 else "")
            if tl["voice_needed"] and tl["voiced"] < tl["voice_needed"]:
                warnings.append(f"{lg}: {tl['voice_needed'] - tl['voiced']} scene(s) without voice-over (silence)")
            base = f"{lg}-{build.fmt_slug(f)}-{stamp}"
            out = p.build_dir / f"render-{job['id']}-{k}.mp4"   # moved into renders/ only when complete
            cover_png = p.build_dir / f"render-{job['id']}-{k}.png"
            cover_t = exports.cover_time(tl, brief.get("cover"))
            cmd = [PY, str(RENDER), "--project", str(p.dir), "--timeline", str(p.build_dir / tl["_file"]),
                   "--out", str(out), "--progress", "--workers", str(prm.get("workers") or 8),
                   "--cover", str(cover_t), "--cover-out", str(cover_png)]
            t0 = time.time()
            rc, tail = self._run_render(job, cmd, tag, done_frames, total_frames, t_all)
            if job.get("cancel"):
                out.unlink(missing_ok=True)
                cover_png.unlink(missing_ok=True)
                raise Cancelled()
            if rc != 0 or not out.exists():
                out.unlink(missing_ok=True)
                raise RuntimeError(f"render {lg} {f} failed: " + " | ".join(tail[-5:]))
            done_frames += tl["frames"]
            final = p.renders_dir / f"{base}.mp4"
            (p.renders_dir / f"{base}.srt").write_text(exports.srt(tl), encoding="utf-8")
            (p.renders_dir / f"{base}.vtt").write_text(exports.vtt(tl), encoding="utf-8")
            if cover_png.exists():
                cover_png.replace(p.renders_dir / f"{base}.png")
            meta = {"language": lg, "format": f, "duration": tl["duration"], "width": tl["width"], "height": tl["height"],
                    "voiced": tl["voiced"], "voice_needed": tl["voice_needed"], "warnings": warnings,
                    "seconds": round(time.time() - t0, 1), "cover_t": cover_t, "timing": tl.get("timing"),
                    "music": tl.get("music"), "srt": f"{base}.srt", "vtt": f"{base}.vtt",
                    "cover": f"{base}.png" if (p.renders_dir / f"{base}.png").exists() else None, "batch": stamp}
            final.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
            out.replace(final)
            results.append({"render": final.name, **meta})
        p.touch()
        first = results[0]
        return {"render": first["render"], "renders": [r["render"] for r in results], "outputs": results,
                "duration": first["duration"], "format": first["format"], "warnings": warnings,
                "seconds": round(time.time() - t_all, 1)}

    def _run_render(self, job, cmd, tag, done_frames, total_frames, t_all):
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        job.update(stage="frames", message=f"{tag}: starting browser")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.procs[job["id"]] = proc
        tail: list[str] = []
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith("@@PROGRESS "):
                    try:
                        ev = json.loads(line[11:])
                    except ValueError:
                        continue
                    if ev["stage"] == "frames" and ev["total"]:
                        f = (done_frames + ev["done"]) / total_frames
                        el = time.time() - t_all
                        eta = el / f * (1 - f) if f > 0.02 else None
                        job.update(stage="frames", progress=round(0.16 + 0.82 * f, 3),
                                   message=f"{tag}: frame {ev['done']}/{ev['total']}" + (f" · ~{eta:.0f}s left" if eta else ""))
                    elif ev["stage"] == "encoding":
                        job.update(stage="encoding", message=f"{tag}: finishing MP4")
                    elif ev["stage"] == "audio":
                        job.update(stage="audio", message=f"{tag}: analysing audio")
                elif line:
                    tail = (tail + [line])[-15:]
            rc = proc.wait()
        finally:
            self.procs.pop(job["id"], None)
        return rc, tail


def _langs(p: Project, prm: dict) -> list[str]:
    """The languages a job works on: params lang / langs (filtered to the project's), default all."""
    all_l = build.langs_of(p.brief)
    want = prm.get("langs") or ([prm["lang"]] if prm.get("lang") else None)
    if not want:
        return all_l
    return [lg for lg in want if lg in all_l] or all_l
