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
        self._wait_gate(job)

        def prog(f, msg):
            if job.get("cancel"):
                raise Cancelled()
            job.update(progress=round(f, 3), stage="voice", message=msg)

        res = build.generate(p, ids, force=bool(prm.get("force")), progress=prog)
        tl = build.timeline(p)
        build.voiceover(p, tl)
        return {**res, "voiced": tl["voiced"], "voice_needed": tl["voice_needed"]}

    def _render(self, job: dict) -> dict:
        p = Project.get(job["project"])
        prm = job["params"]
        warnings = []
        # 1. voice-over (only what is missing)
        if not prm.get("skip_voice"):
            job.update(stage="voice", message="voice-over", progress=0.0)
            tl0 = build.timeline(p)
            if tl0["voiced"] < tl0["voice_needed"]:
                self._wait_gate(job)

            def prog(f, msg):
                if job.get("cancel"):
                    raise Cancelled()
                job.update(progress=round(0.15 * f, 3), message=msg)
            try:
                build.generate(p, progress=prog)
            except build.V.TTSError as e:
                if not prm.get("allow_silent"):
                    raise RuntimeError(f"{e}. Fix the voice settings, or render without voice-over.") from None
                warnings.append(f"voice-over skipped: {e}")
        # 2. timeline + voiceover.wav
        job.update(stage="timeline", message="timeline", progress=0.15)
        tl = build.write_render_timeline(p)
        if tl["voice_needed"] and tl["voiced"] < tl["voice_needed"]:
            warnings.append(f"{tl['voice_needed'] - tl['voiced']} scene(s) without voice-over (silence)")
        # 3. frames + encode in a subprocess (isolated, cancellable)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        final = p.renders_dir / f"{stamp}-{tl['format'].replace(':', 'x')}.mp4"
        out = p.build_dir / f"render-{job['id']}.mp4"   # moved into renders/ only when complete
        p.renders_dir.mkdir(parents=True, exist_ok=True)
        cmd = [PY, str(RENDER), "--project", str(p.dir), "--out", str(out), "--progress",
               "--workers", str(prm.get("workers") or 8)]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        job.update(stage="frames", message="starting browser", progress=0.16)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.procs[job["id"]] = proc
        tail: list[str] = []
        t0 = time.time()
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if line.startswith("@@PROGRESS "):
                    try:
                        ev = json.loads(line[11:])
                    except ValueError:
                        continue
                    if ev["stage"] == "frames" and ev["total"]:
                        f = ev["done"] / ev["total"]
                        el = time.time() - t0
                        eta = el / f * (1 - f) if f > 0.02 else None
                        job.update(stage="frames", progress=round(0.17 + 0.78 * f, 3),
                                   message=f"frame {ev['done']}/{ev['total']}" + (f" · ~{eta:.0f}s left" if eta else ""))
                    elif ev["stage"] == "encoding":
                        job.update(stage="encoding", progress=0.96, message="finishing MP4")
                    elif ev["stage"] == "audio":
                        job.update(stage="audio", message="analysing audio")
                elif line:
                    tail = (tail + [line])[-15:]
            rc = proc.wait()
        finally:
            self.procs.pop(job["id"], None)
        if job.get("cancel"):
            out.unlink(missing_ok=True)
            raise Cancelled()
        if rc != 0 or not out.exists():
            out.unlink(missing_ok=True)
            raise RuntimeError("render failed: " + " | ".join(tail[-5:]))
        meta = {"format": tl["format"], "duration": tl["duration"], "width": tl["width"], "height": tl["height"],
                "voiced": tl["voiced"], "voice_needed": tl["voice_needed"], "warnings": warnings,
                "seconds": round(time.time() - t0, 1)}
        final.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        out.replace(final)
        p.touch()
        return {"render": final.name, **meta}
