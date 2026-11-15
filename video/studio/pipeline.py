""""Make video": one button / one API call from brief to MP4, each step with the whole GPU.

Stages, strictly in order, one pipeline at a time:
  1. voice   the warm AI workers first give back their GPU memory (weights stay in RAM) and ComfyUI unloads
             its models (POST /free, when it is running), then every
             missing/changed voice-over line is generated (Ovoz may use the whole GPU; no AI GPU job may
             start meanwhile)
  2. unload  POST {OVOZ}/api/unload, then confirm with the GPU counters that Ovoz's VRAM dropped
             (404 = old Ovoz: a notice, and the AI queue's normal VRAM wait / "start anyway" applies)
  3. ai      every AI job the project still needs: generated music (music.generate without a track),
             a presenter portrait (from portrait_prompt), b-roll for scenes that ask for one (each with
             its provider: background.provider or the project's broll_provider), the presenter video;
             quality = the pipeline's (default "final"). Jobs run one after another in the warm workers
             or, for `comfy:<workflow>` providers, in ComfyUI (the AI queue frees ComfyUI's VRAM before a
             worker job and the workers' VRAM before a ComfyUI job). After the stage ComfyUI frees its
             memory again. The studio's own voice requests wait during this stage.
  4. render  the MP4 (the normal render queue)

Pipelines are stored in library/pipelines.json. A failed or cancelled pipeline can be retried:
it resumes at the first stage that is not finished. After a server restart, running pipelines
show as "interrupted".

Voice lines Ovoz refuses (after build.generate's retries) do not fail the pipeline: every other line is
generated, then the pipeline PAUSES (status "paused", `voice_failures`) so the person can fix those lines
(edit the text, pick another voice) and Retry, or Continue with them silent (retry with skip_failed).

Timing: `elapsed` is this attempt's time, `total_elapsed` all attempts together; `eta` is the sum of the
remaining stages' estimates (stage `estimate`: voice lines, AI job estimates of the AI queue, render =
measured seconds per video second of earlier renders, else 1.2 x the video length, per output).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.request

import build
import gpu
import library as L
import music as MU
import presenter as PR
import providers as P
from brief import localize as normalize_l
from brief import normalize
from store import Project, StoreError, now

STAGES = ["voice", "unload", "ai", "render"]
FILE = L.LIB / "pipelines.json"
OUTDATED = "Ovoz is outdated — update it to free GPU memory automatically"


class Cancelled(Exception):
    pass


class Paused(Exception):
    """The pipeline stops and waits for a decision (some voice lines failed)."""


VOICE_LINE_S = {"online": 1.5, "local": 3.5}   # seconds per generated line (Edge measured 1.4 s, navoiy ~3 s)
ALIGN_LINE_S = 0.7                              # faster-whisper small on the CPU, per line
RENDER_FACTOR = 1.2                             # render seconds per video second per output, until measured


def _stage() -> dict:
    return {"state": "pending", "progress": 0.0, "message": "", "started": None, "finished": None,
            "eta": None, "error": None, "detail": {}, "estimate": None}


class Pipelines:
    def __init__(self, jobs, ai):
        self.jobs, self.ai = jobs, ai
        self.items: dict[str, dict] = {}
        self.phase: str | None = None      # "voice" | "ai" while those stages hold the GPU
        self.current: str | None = None
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self._saved = 0.0
        self._load()
        jobs.gate = self.voice_gate
        ai.hold = self.ai_gate
        threading.Thread(target=self._loop, name="make-video", daemon=True).start()

    # ------------------------------------------------------------ gates used by the queues
    def voice_gate(self) -> str | None:
        if self.phase == "ai":
            return "waiting: “Make video” is running AI jobs on the GPU — voice-over continues afterwards"
        return None

    def ai_gate(self) -> str | None:
        if self.phase == "voice":
            return "waiting: “Make video” is generating the voice-over (Ovoz has the GPU)"
        return None

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        for p in data:
            if p.get("status") in ("queued", "running"):
                p["status"] = "interrupted"
                p["error"] = "The studio server restarted during this pipeline. Retry resumes it."
                for st in p["stages"].values():
                    if st["state"] == "running":
                        st.update(state="error", error="interrupted by a server restart")
            self.items[p["id"]] = p

    def _save(self, force: bool = False) -> None:
        if not force and time.time() - self._saved < 1.5:
            return
        self._saved = time.time()
        with self.lock:
            data = sorted(self.items.values(), key=lambda p: p["created"])[-60:]
            L.LIB.mkdir(parents=True, exist_ok=True)
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps([{k: v for k, v in p.items() if not k.startswith("_")} for p in data],
                                      ensure_ascii=False, indent=0), encoding="utf-8")
            tmp.replace(FILE)

    # ------------------------------------------------------------ public
    def submit(self, pid: str, quality: str = "final", allow_silent: bool = False) -> dict:
        p = Project.get(pid)
        act = self.active(pid)
        if act:
            raise StoreError(json.dumps(act), 409)
        item = {"id": secrets.token_hex(6), "project": pid, "title": p.brief.get("title"), "quality": quality,
                "allow_silent": allow_silent, "status": "queued", "current": None, "created": now(),
                "started": None, "finished": None, "error": None, "notices": [], "result": None,
                "stages": {s: _stage() for s in STAGES}, "ai_jobs": [], "render_job": None}
        with self.lock:
            self.items[item["id"]] = item
        self._save(force=True)
        self.wake.set()
        return self.view(item)

    def view(self, it: dict) -> dict:
        v = {k: val for k, val in it.items() if not k.startswith("_")}
        v["stages"] = {k: dict(st) for k, st in it["stages"].items()}
        t = time.time()
        spent = it.get("spent") or 0.0
        if it["status"] == "running" and (it.get("attempt_started") or it.get("started")):
            v["elapsed"] = round(t - (it.get("attempt_started") or it["started"]))        # this attempt only
            v["total_elapsed"] = round(spent + v["elapsed"])
            for st in v["stages"].values():
                if st["state"] == "running" and st.get("started"):
                    st["elapsed"] = round(t - st["started"])
        elif spent:
            v["total_elapsed"] = round(spent)
        q = self.queued()
        v["position"] = q.index(it["id"]) + 1 if it["id"] in q else 0
        v["eta"] = self.eta(v) if it["status"] == "running" else None
        return v

    @staticmethod
    def eta(v: dict) -> int | None:
        """Seconds left = the running stage's own ETA (or its estimate minus its elapsed time) + the estimates of
        the stages still pending."""
        total, known = 0.0, False
        for name in STAGES:
            st = v["stages"][name]
            if st["state"] == "running":
                if st.get("eta") is not None:
                    total += st["eta"]
                    known = True
                elif st.get("estimate") is not None:
                    total += max(5, st["estimate"] - (st.get("elapsed") or 0))
                    known = True
            elif st["state"] == "pending" and st.get("estimate") is not None:
                total += st["estimate"]
                known = True
        return round(total) if known else None

    def queued(self) -> list[str]:
        return [p["id"] for p in sorted(self.items.values(), key=lambda p: p["created"]) if p["status"] == "queued"]

    def get(self, ident: str) -> dict | None:
        it = self.items.get(ident)
        return self.view(it) if it else None

    def latest(self, pid: str) -> dict | None:
        its = [p for p in self.items.values() if p["project"] == pid]
        return self.view(max(its, key=lambda p: p["created"])) if its else None

    def active(self, pid: str | None = None) -> dict | None:
        for p in self.items.values():
            if p["status"] in ("queued", "running") and (pid is None or p["project"] == pid):
                return self.view(p)
        return None

    def list(self) -> list[dict]:
        return [self.view(p) for p in sorted(self.items.values(), key=lambda p: p["created"], reverse=True)[:30]]

    def activity(self) -> list[tuple]:
        """(key, label, category) of the running pipeline stage, for the monitor's timeline markers."""
        it = self.items.get(self.current) if self.current else None
        if not it or it.get("status") != "running" or not it.get("current"):
            return []
        names = {"voice": "① Voice-over", "unload": "② Free GPU", "ai": "③ AI clips", "render": "④ Render"}
        return [(f"make:{it['id']}:{it['current']}", f"Make video · {names.get(it['current'], it['current'])} · "
                 f"{(it.get('title') or it['project'])[:30]}", f"stage-{it['current']}")]

    def cancel(self, ident: str) -> dict | None:
        it = self.items.get(ident)
        if not it:
            return None
        if it["status"] == "queued":
            it.update(status="cancelled", finished=now())
        elif it["status"] == "running":
            it["_cancel"] = True
            for jid in it.get("ai_jobs") or []:
                self.ai.cancel(jid)
            if it.get("render_job"):
                self.jobs.cancel(it["render_job"])
        self._save(force=True)
        return self.view(it)

    def retry(self, ident: str, skip_failed: bool = False) -> dict | None:
        """Resume at the first unfinished stage. skip_failed: voice lines that fail again are rendered silent
        (the "Continue without those lines" choice of a paused pipeline)."""
        it = self.items.get(ident)
        if not it or it["status"] not in ("error", "cancelled", "interrupted", "paused"):
            return None
        if self.active(it["project"]):
            return None
        for st in it["stages"].values():
            if st["state"] in ("error", "cancelled", "running", "paused"):
                st.update(_stage())
        # the choice applies to this retry only: a later plain Retry tries the failed lines again (and pauses
        # again if they still fail) instead of silently rendering them silent forever
        it["skip_failed"] = bool(skip_failed)
        vst = it["stages"]["voice"]
        if not skip_failed and not it.get("allow_silent") and vst["state"] == "warning" and vst["detail"].get("failed"):
            vst.update(_stage())         # lines skipped by an earlier "continue without them": voice them again
            it["notices"] = [n for n in it.get("notices") or [] if not n.startswith("voice-over:")]
        it.pop("voice_failures", None)
        it.update(status="queued", error=None, finished=None)
        it.pop("_cancel", None)
        self._save(force=True)
        self.wake.set()
        return self.view(it)

    # ------------------------------------------------------------ worker
    def _loop(self) -> None:
        while True:
            q = self.queued()
            if not q:
                self.wake.wait(3.0)
                self.wake.clear()
                continue
            self._run(self.items[q[0]])

    def _run(self, it: dict) -> None:
        t0 = now()
        it.update(status="running", started=it.get("started") or t0, attempt_started=t0, error=None,
                  attempts=(it.get("attempts") or 0) + 1)
        self.current = it["id"]
        self._estimate(it)
        self._save(force=True)
        try:
            for name in STAGES:
                st = it["stages"][name]
                if st["state"] in ("done", "skipped", "warning"):
                    continue
                it["current"] = name
                st.update(state="running", started=now(), finished=None, error=None, progress=0.0, eta=None)
                self._save(force=True)
                try:
                    getattr(self, "_" + name)(it, st)
                    if st["state"] == "running":
                        st["state"] = "done"
                    st["progress"] = 1.0
                    st["eta"] = 0
                except Cancelled:
                    st.update(state="cancelled", message="cancelled")
                    raise
                except Paused as e:
                    st.update(state="paused", error=str(e), eta=None)
                    raise
                except Exception as e:
                    st.update(state="error", error=str(e) or type(e).__name__)
                    raise
                finally:
                    st["finished"] = now()
                    self.phase = None
                    self._save(force=True)
                self._estimate(it)          # e.g. the voice-over fixes the video length for the render estimate
            it.update(status="done", current=None)
        except Cancelled:
            it["status"] = "cancelled"
        except Paused as e:
            it.update(status="paused", error=str(e))
        except Exception as e:
            it.update(status="error", error=str(e) or type(e).__name__)
        finally:
            it["finished"] = now()
            it["spent"] = round((it.get("spent") or 0.0) + (it["finished"] - t0), 1)
            it.pop("_cancel", None)
            self.current = None
            self.phase = None
            self._save(force=True)

    # ------------------------------------------------------------ estimates
    def _estimate(self, it: dict) -> None:
        """Fill stage["estimate"] (seconds) for every stage that is not finished. Never fails the pipeline."""
        try:
            p = Project.get(it["project"])
        except StoreError:
            return
        for name in STAGES:
            st = it["stages"][name]
            if st["state"] in ("done", "skipped", "warning"):
                continue
            try:
                st["estimate"] = round(getattr(self, "_est_" + name)(it, p))
            except Exception as e:     # an estimate is a nicety
                print(f"[make] estimate {name} failed: {e}", flush=True)
                st["estimate"] = None

    def _est_voice(self, it: dict, p: Project) -> float:
        todo = build.todo_lines(p)
        sec = 0.0
        for _lg, lb, s in todo:
            m = (build.line_voice(lb, s).get("model") or "").lower()
            sec += VOICE_LINE_S["online" if m in build.ONLINE_MODELS else "local"]
        lines = sum(1 for lg in build.langs_of(p.brief) for s in normalize_l(p.brief, lg)["scenes"] if build.say_of(s))
        return 3 + sec + ALIGN_LINE_S * (len(todo) or 0) + (0.05 * lines)

    def _est_unload(self, it: dict, p: Project) -> float:
        return 6.0

    def _est_ai(self, it: dict, p: Project) -> float:
        return sum(e for _k, e in self.ai_plan(p, it.get("quality") or "final"))

    def _est_render(self, it: dict, p: Project) -> float:
        return render_estimate(p)

    def ai_plan(self, p: Project, q: str) -> list[tuple[str, float]]:
        """[(kind, estimated seconds)] of the AI jobs "Make video" would still run for this project (read-only)."""
        brief = p.brief
        out: list[tuple[str, float]] = []
        mu = brief.get("music") if isinstance(brief.get("music"), dict) else {}
        spec = mu.get("generate")
        if spec and not (mu.get("track") and MU.get(mu["track"])):
            dur = spec.get("duration") or min(240.0, round(build.timeline(p)["duration"] + 3, 1))
            full = {**spec, "duration": dur, "seed": spec.get("seed", 7)}
            if not MU.generated_for(full):
                out.append(("music", self.ai.estimate("music", "final", dur)))
        pres = brief.get("presenter") if isinstance(brief.get("presenter"), dict) else None
        bubble, fullp, _ = PR.needed(brief)
        if (bubble or fullp) and pres is not None and not pres.get("portrait") and pres.get("portrait_prompt"):
            out.append(("portrait", self.ai.estimate("portrait", "final", 40)))
        fmt = brief.get("format", "9:16")
        comfy = False
        for s in brief["scenes"]:
            bg = s.get("background") or {}
            if bg.get("type") != "broll" or (bg.get("clip") and L.broll_get(bg["clip"])) or not bg.get("prompt"):
                continue
            provider = bg.get("provider") or brief.get("broll_provider") or P.DEFAULT
            if not P.exists(provider):
                provider = P.DEFAULT
            orient = "landscape" if fmt == "16:9" else "portrait"
            img = image_ref(p, bg["image"]) if bg.get("image") else None
            cid = P.clip_key(provider, bg["prompt"], broll_seed(bg), q, orient, img["sha"] if img else None)
            if L.broll_get(cid):
                continue
            comfy = comfy or P.is_comfy(provider)
            out.append(("broll", self.ai.estimate_broll(provider, q, bool(img))))
            if bg.get("both") and other_orientation(brief) and not (bg.get("clip_alt") and L.broll_get(bg["clip_alt"])):
                out.append(("broll", self.ai.estimate_broll(provider, q, bool(img))))
        if comfy and not self.ai.comfy.reachable(1.0):
            out.append(("comfy-start", 25.0))         # ComfyUI starts (measured ~21 s) before its first job
        if bubble or fullp:
            try:
                pl = PR.plan(p, normalize_l(brief), build.timeline(p), q)
                if pl and not pl.get("error") and not pl["ready"]:
                    out.append(("presenter", self.ai.estimate("presenter", q, pl["missing_seconds"])))
            except Exception:
                pass
        return out

    def _check(self, it: dict) -> None:
        if it.get("_cancel"):
            raise Cancelled()

    def _notice(self, it: dict, text: str) -> None:
        if text not in it["notices"]:
            it["notices"].append(text)

    # ------------------------------------------------------------ 1. voice
    def _voice(self, it: dict, st: dict) -> None:
        p = Project.get(it["project"])
        # no AI GPU job may run while Ovoz synthesises: wait for a running one, block new ones
        self.phase = "voice"
        while self.ai.current:
            self._check(it)
            j = self.ai.jobs.get(self.ai.current) or {}
            st["message"] = f"waiting for the running AI job ({j.get('kind', '?')}) to finish"
            self._save()
            time.sleep(2)
        # warm AI workers give back their GPU memory (their weights stay in RAM) so Ovoz gets the whole GPU
        st["message"] = "warm AI workers: releasing GPU memory (weights stay in RAM)…"
        self._save(force=True)
        before = self.ai.monitor.refresh()
        released = self.ai.pool.release_gpu()
        comfy_free = None
        if self.ai.comfy.reachable(1.5):            # ComfyUI unloads its models too (POST /free)
            st["message"] = "ComfyUI: unloading its models (POST /free)…"
            self._save(force=True)
            comfy_free = self.ai.comfy.free(why="before the voice-over")
        after = self.ai.monitor.refresh() if released or comfy_free else before
        st["detail"]["released"] = {"workers": released, "ai_mb_before": before.get("ai_mb"),
                                    "ai_mb_after": after.get("ai_mb"), "warm_ram_gb": self.ai.pool.view()["ram_gb"],
                                    "comfy": comfy_free}
        brief = p.brief
        langs = build.langs_of(brief)
        todo = build.todo_lines(p)
        rel = st["detail"]["released"]
        st["detail"] = {"released": rel}
        total = sum(1 for lg in langs for s in normalize_l(brief, lg)["scenes"] if build.say_of(s))
        st["detail"] = {**st["detail"], "lines": total, "to_generate": len(todo), "languages": langs,
                        "model": brief["voice"].get("model"), "voice": brief["voice"].get("voice"),
                        "voices": {lg: f"{v.get('model')}/{v.get('voice')}" for lg, v in (brief.get("voices") or {}).items()}}

        def finish():
            st["message"] = "word timing (faster-whisper, CPU)…"
            self._save()
            tim = build.align_all(p, langs)
            st["detail"]["timing"] = tim
            for lg in langs:
                build.voiceover(p, build.timeline(p, lang=lg))
            return tim
        if not todo:
            tim = finish()
            st["message"] = (f"all {total} lines already voiced ({len(langs)} language(s)); "
                             f"timing aligned for {tim['aligned']}/{tim['lines']}")
            return
        t0 = time.time()
        done = [0]

        def prog(f, msg):
            self._check(it)
            n = round(f * len(todo))
            if n > done[0]:
                done[0] = n
            el = time.time() - t0
            st.update(progress=round(f, 3), message=f"{msg} ({done[0]}/{len(todo)})",
                      eta=round(el / done[0] * (len(todo) - done[0])) if done[0] else None)
            self._save()
        failed = []
        try:
            res = build.generate(p, progress=prog, skip_failed=bool(it.get("skip_failed")))
            failed = res["failed"]
        except build.V.TTSError as e:
            if not it.get("allow_silent"):
                raise RuntimeError(f"voice-over failed: {e}") from None
            st["state"] = "warning"
            self._notice(it, f"voice-over skipped (silent scenes): {e}")
        st["detail"]["seconds"] = round(time.time() - t0, 1)
        tim = finish()
        st["message"] = (f"{len(todo) - len(failed)} line(s) generated in {st['detail']['seconds']} s "
                         f"({len(langs)} language(s)); timing aligned for {tim['aligned']}/{tim['lines']}")
        if failed:
            st["detail"]["failed"] = failed
            summary = build.failed_summary(failed)
            if it.get("allow_silent") or it.get("skip_failed"):
                st["state"] = "warning"
                n = len(failed)
                self._notice(it, f"voice-over: {n} line{'s' if n != 1 else ''} failed — continuing with "
                                 f"{'them' if n != 1 else 'it'} silent")
                return
            # every other line is done: stop here and let the person decide (fix / other voice / silent)
            it["voice_failures"] = failed
            raise Paused(f"voice-over: {summary} — choose what to do with {'them' if len(failed) != 1 else 'it'} below")

    # ------------------------------------------------------------ 2. free the GPU
    def _unload(self, it: dict, st: dict) -> None:
        mon = self.ai.monitor
        before = mon.refresh()
        st["detail"]["before"] = _snap(before)
        st["message"] = "asking Ovoz to unload its models…"
        self._save(force=True)
        try:
            req = urllib.request.Request(build.OVOZ.rstrip("/") + "/api/unload", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=900) as r:
                res = json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                st["state"] = "warning"
                st["message"] = OUTDATED
                self._notice(it, OUTDATED)
                st["detail"]["after"] = _snap(mon.refresh())
                return
            raise RuntimeError(f"Ovoz /api/unload failed: HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            st["state"] = "warning"
            st["message"] = f"Ovoz is not reachable ({getattr(e, 'reason', e)}) — nothing to unload"
            st["detail"]["after"] = _snap(mon.refresh())
            return
        st["detail"]["unloaded"] = res.get("unloaded") or []
        # confirm with the per-process counters (worker processes take a moment to exit)
        after = before
        for _ in range(20):
            self._check(it)
            time.sleep(1.0)
            after = mon.refresh()
            ov = after.get("ovoz_mb")
            if ov is None or ov < 600:
                break
        st["detail"]["after"] = _snap(after)
        b, a = st["detail"]["before"], st["detail"]["after"]
        st["message"] = (f"unloaded {', '.join(st['detail']['unloaded']) or 'nothing (no local model was loaded)'}; "
                         f"Ovoz {gpu.gb(b.get('ovoz_mb'))} → {gpu.gb(a.get('ovoz_mb'))} GB, "
                         f"free {gpu.gb(b.get('free_mb'))} → {gpu.gb(a.get('free_mb'))} GB")

    # ------------------------------------------------------------ 3. AI jobs
    def _ai(self, it: dict, st: dict) -> None:
        p = Project.get(it["project"])
        q = it["quality"]
        self.phase = "ai"
        base = (self.ai.monitor.refresh() or {}).get("ovoz_mb") or 0
        st["detail"]["ovoz_mb_at_start"] = base
        brief = p.brief
        pres = brief.get("presenter") if isinstance(brief.get("presenter"), dict) else None
        bubble, full, _ = PR.needed(brief)
        wants_presenter = bool(bubble or full)
        jobs_done: list[dict] = []
        # 0) generated music when the project asks for one and has no (existing) track yet
        mu = brief.get("music") if isinstance(brief.get("music"), dict) else {}
        spec = mu.get("generate")
        if spec and not (mu.get("track") and MU.get(mu["track"])):
            spec = {**spec, "duration": spec.get("duration") or min(240.0, round(build.timeline(p)["duration"] + 3, 1)),
                    "seed": spec.get("seed", 7)}
            have = MU.generated_for(spec)
            if have:
                b2 = p.brief
                b2["music"] = {**b2.get("music", {}), "track": have["id"]}
                p.save_brief(normalize(b2, p.assets())[0])
                self._notice(it, f"music: reused the generated track '{have['title']}'")
            else:
                j = self.ai.submit("music", {**spec, "cache_key": MU.generate_key(spec), "assign_project": p.id,
                                             "title": f"{brief.get('product') or brief.get('title') or 'Video'} — "
                                                      f"{spec.get('mood') or spec.get('genre') or 'music'}"[:60]},
                                   project=p.id, label=(spec.get("prompt") or spec.get("genre") or "music")[:70],
                                   units=spec["duration"], preset="final")
                jobs_done += self._wait_jobs(it, st, [j["id"]], base)
            brief = p.brief
        # a) portrait from portrait_prompt when the presenter has none yet
        if wants_presenter and pres is not None and not pres.get("portrait") and pres.get("portrait_prompt"):
            j = self.ai.submit("portrait", {"prompt": pres["portrait_prompt"], "seed": 1, "steps": 40,
                                            "assign_project": p.id},
                               project=p.id, label=pres["portrait_prompt"][:70], units=40, preset="final")
            jobs_done += self._wait_jobs(it, st, [j["id"]], base)
            brief = p.brief
        # b) b-roll for scenes that ask for one, c) the presenter
        ids = []
        fmt = brief.get("format", "9:16")
        for s in brief["scenes"]:
            bg = s.get("background") or {}
            if bg.get("type") != "broll" or (bg.get("clip") and L.broll_get(bg["clip"])):
                continue
            if not bg.get("prompt"):
                if not bg.get("clip"):
                    continue   # "AI clip" picked in the editor but no prompt: nothing to generate
                self._notice(it, f"scene '{s['id']}': clip {bg['clip']} is missing and there is no prompt — brand gradient")
                continue
            provider = bg.get("provider") or brief.get("broll_provider") or P.DEFAULT
            if not P.exists(provider):
                self._notice(it, f"scene '{s['id']}': video generator '{provider}' is not installed — using {P.DEFAULT}")
                provider = P.DEFAULT
            orient = "landscape" if fmt == "16:9" else "portrait"
            img = None
            if bg.get("image"):
                img = image_ref(p, bg["image"])
                if not img:
                    self._notice(it, f"scene '{s['id']}': image '{bg['image']}' to animate is missing — text to video instead")
            seed = broll_seed(bg)
            params = {"prompt": bg["prompt"], "negative": None, "seed": seed, "orientation": orient, "provider": provider,
                      "assign": {"project": p.id, "scene": s["id"], "dim": bg.get("dim", 0.55), "blur": bg.get("blur", 0)}}
            if img:
                params["image"] = img
            cid = P.clip_key(provider, bg["prompt"], seed, q, orient, img["sha"] if img else None)
            if L.broll_get(cid):
                self.ai._assign_broll({"params": params}, cid)
                continue
            pv = P.get(provider)
            ids.append(self.ai.submit("broll", params, project=p.id, label=f"{pv['name']} · {bg['prompt'][:56]}",
                                      units=pv["presets"][q]["steps"], preset=q)["id"])
        brief = p.brief          # the loop above may have assigned cached clips (and dropped stale variants)
        for s in brief["scenes"]:
            # b) the other orientation (e.g. 16:9 next to a 9:16 main format) instead of a hard crop
            bg = s.get("background") or {}
            oth = other_orientation(brief)
            if bg.get("type") != "broll" or not bg.get("both") or not bg.get("prompt") or not oth:
                continue
            provider = bg.get("provider") or brief.get("broll_provider") or P.DEFAULT
            provider = provider if P.exists(provider) else P.DEFAULT
            seed = broll_seed(bg)
            # the same image as the main clip (an image-to-video scene must not get unrelated text-to-video footage)
            img = image_ref(p, bg["image"]) if bg.get("image") else None
            alt = L.broll_get(bg["clip_alt"]) if bg.get("clip_alt") else None
            if alt and L.spec_matches(alt, (bg["prompt"].strip(), str(seed), img["sha"] if img else None, provider)):
                continue            # a variant of this very b-roll exists already
            params = {"prompt": bg["prompt"], "negative": None, "seed": seed, "orientation": oth, "provider": provider,
                      "assign": {"project": p.id, "scene": s["id"], "dim": bg.get("dim", 0.55), "blur": bg.get("blur", 0),
                                 "variant": True}}
            if img:
                params["image"] = img
            cid = P.clip_key(provider, bg["prompt"], seed, q, oth, img["sha"] if img else None)
            if L.broll_get(cid):
                self.ai._assign_broll({"params": params}, cid)
                continue
            pv = P.get(provider)
            ids.append(self.ai.submit("broll", params, project=p.id, label=f"{pv['name']} · {oth} · {bg['prompt'][:46]}",
                                      units=pv["presets"][q]["steps"], preset=q)["id"])
        if wants_presenter:
            brief = p.brief
            if (brief.get("presenter") or {}).get("quality") != q and isinstance(brief.get("presenter"), dict):
                brief["presenter"]["quality"] = q          # the renderer looks for the video of this quality
                p.save_brief(normalize(brief, p.assets())[0])
            tl = build.timeline(p)
            pl = PR.plan(p, normalize_l(p.brief), tl, q)
            if pl and pl.get("error"):
                self._notice(it, f"presenter skipped: {pl['error']}")
            elif pl and not pl["ready"]:
                ids.append(self.ai.submit("presenter", {"quality": q, "gpu": "auto"}, project=p.id,
                                          label=f"{brief.get('title')} · {q}", units=pl["missing_seconds"], preset=q)["id"])
        if ids:
            jobs_done += self._wait_jobs(it, st, ids, base)
        if any(P.is_comfy(j.get("provider")) for j in jobs_done) and self.ai.comfy.reachable(1.5):
            st["message"] = "ComfyUI: freeing its GPU memory after the AI stage…"
            self._save(force=True)
            st["detail"]["comfy_free"] = self.ai.comfy.free(why="after the AI stage")
        st["detail"]["jobs"] = [{"id": j["id"], "kind": j["kind"], "status": j["status"], "elapsed": j.get("elapsed"),
                                 "result": j.get("result")} for j in jobs_done]
        st["message"] = (f"{len(jobs_done)} AI job(s) done" if jobs_done else "nothing to generate — everything is cached")

    def _wait_jobs(self, it: dict, st: dict, ids: list[str], ovoz_base: int) -> list[dict]:
        it["ai_jobs"] = list(dict.fromkeys((it.get("ai_jobs") or []) + ids))
        while True:
            if it.get("_cancel"):
                for jid in ids:
                    self.ai.cancel(jid)
                raise Cancelled()
            js = [self.ai.get(j) for j in ids]
            tot = sum(max(1, j.get("estimate") or 60) for j in js)
            done = sum(max(1, j.get("estimate") or 60) * (j.get("progress") or 0) for j in js)
            eta = 0
            for j in js:
                if j["status"] == "running":
                    eta += j.get("eta") if j.get("eta") is not None else max(0, (j.get("estimate") or 0) - (j.get("elapsed") or 0))
                elif j["status"] == "queued":
                    eta += j.get("estimate") or 0
            run = next((j for j in js if j["status"] in ("running", "queued")), None)
            st.update(progress=round(done / tot, 3), eta=round(eta),
                      message=(f"{run['kind']}: {run.get('message') or run.get('stage')}" if run else "finishing…"))
            snap = self.ai.monitor.get()
            ov = snap.get("ovoz_mb")
            if ov is not None and ov > ovoz_base + 700:
                self._notice(it, f"Ovoz loaded models again during the AI stage ({gpu.gb(ov)} GB) — AI jobs may wait "
                                 "for memory or run slower. Avoid using the Ovoz page until the video is done.")
            bad = [j for j in js if j["status"] in ("error", "cancelled", "interrupted")]
            if bad:
                j = bad[0]
                raise RuntimeError(f"{j['kind']} job {j['status']}: {j.get('error') or ''}".strip())
            if all(j["status"] == "done" for j in js):
                return js
            self._save()
            time.sleep(2)

    # ------------------------------------------------------------ 4. render
    def _render(self, it: dict, st: dict) -> None:
        pid = it["project"]
        skip = bool(it.get("skip_failed"))
        j = self.jobs.busy(pid, "render") or self.jobs.submit("render", pid, allow_silent=bool(it.get("allow_silent")) or skip,
                                                              skip_voice=skip)
        it["render_job"] = j["id"]
        t0 = time.time()
        est = st.get("estimate")
        while True:
            if it.get("_cancel"):
                self.jobs.cancel(j["id"])
                raise Cancelled()
            j = self.jobs.get(j["id"])
            f = j.get("progress") or 0
            m = re.search(r"~(\d+)s left", j.get("message") or "")
            el = time.time() - t0
            eta = (int(m.group(1)) + 5 if m else round(el / f * (1 - f)) if f > 0.2
                   else max(5, round(est - el)) if est else None)
            st.update(progress=round(f, 3), eta=eta, message=f"{j.get('stage')}: {j.get('message') or ''}".strip(": "))
            if j["status"] == "done":
                r = j["result"]
                rs = r.get("renders") or [r["render"]]
                it["result"] = {"render": r["render"], "renders": rs,
                                **{k: r.get(k) for k in ("duration", "format", "seconds")}}
                st["detail"] = {"render": r["render"], "renders": rs, "seconds": r.get("seconds")}
                st["message"] = (f"{len(rs)} videos ({', '.join(rs)})" if len(rs) > 1
                                 else f"{r['render']} ({r.get('duration', 0):.1f} s video)")
                return
            if j["status"] in ("error", "cancelled"):
                raise RuntimeError(f"render {j['status']}: {j.get('error') or ''}")
            self._save()
            time.sleep(1.5)


def other_orientation(brief: dict) -> str | None:
    """'landscape' / 'portrait' when the project also renders formats of the other orientation than its main one."""
    main_land = (brief.get("format") or "9:16") == "16:9"
    fmts = brief.get("formats") or [brief.get("format") or "9:16"]
    if any((f == "16:9") != main_land for f in fmts):
        return "portrait" if main_land else "landscape"
    return None


def broll_seed(bg: dict) -> int:
    """The seed a scene's b-roll is generated with: background.seed (the editor's seed field), else 42."""
    try:
        return int(bg.get("seed")) if bg.get("seed") is not None else 42
    except (TypeError, ValueError):
        return 42


def render_estimate(p: Project) -> float:
    """Seconds the render stage will take: per language x format output, the video length times the measured
    render seconds per video second (earlier renders of this project, same format, preferably same template),
    else RENDER_FACTOR; plus a few seconds per output for the timeline, audio mix and browser start."""
    brief = p.brief
    tpl = brief.get("template") or "aurora"
    ratios: dict[tuple, list[float]] = {}
    try:
        for f in sorted(p.renders_dir.glob("*.json"))[-40:]:
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
                if m.get("seconds") and m.get("duration"):
                    r = float(m["seconds"]) / float(m["duration"])
                    ratios.setdefault((m.get("format"), m.get("template") or "?"), []).append(r)
                    ratios.setdefault((m.get("format"), None), []).append(r)
            except (OSError, ValueError, TypeError):
                continue
    except OSError:
        pass
    total = 0.0
    for lg in build.langs_of(brief):
        dur = build.timeline(p, lang=lg)["duration"]
        for fmt in build.formats_of(brief):
            rs = ratios.get((fmt, tpl)) or ratios.get((fmt, None))
            r = sorted(rs)[len(rs) // 2] if rs else RENDER_FACTOR
            total += dur * r + 4
    return total + 5


def _snap(s: dict) -> dict:
    return {k: s.get(k) for k in ("ovoz_mb", "free_mb", "used_mb", "ai_mb")}


def image_ref(p: Project, name: str) -> dict | None:
    """An image asset of the project (or a library portrait id) to animate: {path, sha, label}."""
    try:
        f = p.asset_path(name)
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return {"path": str(f), "sha": L.file_sha(f), "label": name}
    except StoreError:
        pass
    m = L.portrait_get(name)
    if m:
        f = L.PORTRAITS / name / "portrait.png"
        return {"path": str(f), "sha": L.file_sha(f), "label": f"portrait {name}"}
    return None
