"""Regression tests for the code-review findings (warm workers, pipeline retry flags, b-roll variants, ComfyUI
flags, template install, render warnings, Ovoz 422s, alignment cache, localize per language). No GPU, network or
real subprocess: workers, sockets and Ovoz are faked."""
from __future__ import annotations

import json
import threading
import time

import pytest

import workers as W


# ------------------------------------------------------------------ 1. warm worker never hangs a job
class _Proc:
    def poll(self):
        return None                      # the worker process stays alive (so EXIT never fires)


class _Sock:
    def __init__(self, fail=False):
        self.fail, self.sent = fail, []

    def sendall(self, b):
        if self.fail:
            raise OSError("connection reset")
        self.sent.append(json.loads(b))


def _worker(monkeypatch, ready=True, sock=None):
    killed = []
    monkeypatch.setattr(W, "kill_tree", lambda pid: killed.append(pid))
    w = object.__new__(W.Worker)
    w.family, w.warm, w.proc, w.pid = "wan", True, _Proc(), 4242
    w.status, w.ready, w.lock, w.wlock = {"busy": None}, threading.Event(), threading.Lock(), threading.Lock()
    w.listener, w.sock, w.jobs_run, w.log, w.last_used, w.last_output = None, sock, 0, [], 0, time.time()
    if ready:
        w.ready.set()
    return w, killed


def _run_bounded(w, seconds=8.0):
    """w.run in a thread; returns ('ok', result) / ('error', exc) / ('hung', None)."""
    box = {}

    def go():
        try:
            box["ok"] = w.run("j1", {"kind": "broll"}, lambda k, d: None, lambda: False)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        return "hung", None
    return ("ok", box["ok"]) if "ok" in box else ("error", box["error"])


def test_worker_that_never_got_ready_fails_the_job(monkeypatch):
    monkeypatch.setattr(W, "READY_TIMEOUT", 0.2)
    w, killed = _worker(monkeypatch, ready=False)
    kind, e = _run_bounded(w)
    assert kind == "error" and "did not start" in str(e) and killed == [4242]


def test_worker_without_connection_fails_the_job(monkeypatch):
    w, killed = _worker(monkeypatch, sock=None)
    kind, e = _run_bounded(w)
    assert kind == "error" and "could not be connected" in str(e) and killed == [4242]


def test_send_reports_failure_and_a_lost_connection_fails_the_job(monkeypatch):
    w, killed = _worker(monkeypatch, sock=_Sock(fail=True))
    assert w.send({"op": "status"}) is False
    kind, e = _run_bounded(w)
    assert kind == "error" and "could not be sent" in str(e) and killed == [4242]
    assert w.status["busy"] is None and w.listener is None


def test_watchdog_fails_a_silent_job(monkeypatch):
    monkeypatch.setattr(W, "STALL_SECONDS", 0.8)
    w, killed = _worker(monkeypatch, sock=_Sock())
    kind, e = _run_bounded(w)
    assert kind == "error" and "stopped responding" in str(e) and killed == [4242]
    assert w.sock.sent[0]["op"] == "run"


def test_worker_job_with_output_still_returns_its_result(monkeypatch):
    monkeypatch.setattr(W, "STALL_SECONDS", 0.8)
    w, killed = _worker(monkeypatch, sock=_Sock())

    def feed():                          # progress lines keep the watchdog quiet, then the result
        for _ in range(6):
            time.sleep(0.25)
            w.last_output = time.time()
        while w.listener is None:
            time.sleep(0.01)
        w.listener("RESULT", {"id": "j1", "out": "x.mp4"})
    threading.Thread(target=feed, daemon=True).start()
    kind, r = _run_bounded(w)
    assert kind == "ok" and r == {"out": "x.mp4"} and not killed


# ------------------------------------------------------------------ 2. "continue without those lines" is one retry only
import brief as B          # noqa: E402
import build               # noqa: E402
import library as L        # noqa: E402
import pipeline as PL      # noqa: E402
from store import Project  # noqa: E402


class _RenderJobs:
    def __init__(self):
        self.submits = []

    def busy(self, pid, kind):
        return None

    def submit(self, kind, pid, **kw):
        self.submits.append(kw)
        return {"id": f"r{len(self.submits)}"}

    def get(self, jid):
        return {"id": jid, "status": "done", "result": {"render": "a.mp4", "duration": 1.0}}

    def cancel(self, jid):
        pass


def _bare_pipelines(jobs=None, ai=None):
    pl = PL.Pipelines.__new__(PL.Pipelines)            # no worker thread, no pipelines.json
    pl.ai, pl.jobs, pl.phase, pl.current = ai, jobs, None, None
    pl.items, pl._saved, pl.wake, pl.lock = {}, 0.0, threading.Event(), threading.RLock()
    pl._save = lambda force=False: None
    return pl


def test_skip_failed_applies_to_the_retry_it_was_chosen_for_only():
    jobs = _RenderJobs()
    pl = _bare_pipelines(jobs)
    it = {"id": "t1", "project": "p1", "quality": "final", "status": "paused", "notices": [], "created": 0,
          "stages": {s: PL._stage() for s in PL.STAGES}}
    it["stages"]["voice"].update(state="paused")
    pl.items["t1"] = it
    pl.retry("t1", skip_failed=True)                    # "Continue without those lines"
    assert it["skip_failed"] is True
    pl._render(it, it["stages"]["render"])
    assert jobs.submits[-1] == {"allow_silent": True, "skip_voice": True}
    # that attempt voiced the rest and skipped the failed lines; then the render broke and the user hits plain Retry
    it["stages"]["voice"].update(state="warning", detail={"failed": [{"lang": "ru", "scene": "x"}]})
    it["notices"] = ["voice-over: 1 line failed — continuing with it silent", "music: reused the generated track 'x'"]
    it["stages"]["render"].update(state="error")
    it["status"] = "error"
    pl.retry("t1")
    assert it["skip_failed"] is False
    assert it["stages"]["voice"]["state"] == "pending"            # the failed lines are tried again
    assert it["notices"] == ["music: reused the generated track 'x'"]
    pl._render(it, it["stages"]["render"])
    assert jobs.submits[-1] == {"allow_silent": False, "skip_voice": False}


# ------------------------------------------------------------------ 3./4. b-roll variants follow their main clip
def _clip(cid, w, h, prompt="ribbons", seed=42, image_sha=None, provider="fastwan-5b"):
    d = L.BROLL / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "clip.webm").write_bytes(b"x")
    (d / "meta.json").write_text(json.dumps({"id": cid, "w": w, "h": h, "duration": 5.0, "seed": seed, "prompt": prompt,
                                             "image_sha": image_sha, "provider": provider}))
    return cid


@pytest.fixture()
def broll_project():
    raw = {"product": "Ovoz", "language": "en", "format": "9:16", "formats": ["9:16", "16:9"],
           "scenes": [{"id": "hook", "layout": "hero", "headline": "Ovoz", "say": "Hi.",
                       "background": {"type": "broll", "prompt": "ribbons", "seed": 42, "both": True,
                                      "clip": _clip("b-rvport0001", 720, 1280),
                                      "clip_alt": _clip("b-rvland0001", 1280, 720)}}]}
    p = Project.create(B.normalize(raw)[0], "api")
    yield p
    p.delete()


def _bg(p):
    return p.brief["scenes"][0]["background"]


def test_new_main_clip_drops_a_variant_of_another_prompt(broll_project, server):
    ai, p = server.ai, broll_project
    # same b-roll (prompt + seed) at another quality: the variant still fits
    ai._assign_broll({"params": {"seed": 42, "assign": {"project": p.id, "scene": "hook"}}}, _clip("b-rvport0002", 720, 1280))
    assert _bg(p)["clip"] == "b-rvport0002" and _bg(p)["clip_alt"] == "b-rvland0001"
    # a clip of a new prompt: the old 16:9 variant must not stay attached (the pipeline would skip regenerating it)
    ai._assign_broll({"params": {"seed": 42, "assign": {"project": p.id, "scene": "hook"}}},
                     _clip("b-rvport0003", 720, 1280, prompt="city at night"))
    assert _bg(p)["clip"] == "b-rvport0003" and "clip_alt" not in _bg(p)


def test_new_main_clip_with_another_seed_or_image_drops_the_variant(broll_project, server):
    ai, p = server.ai, broll_project
    ai._assign_broll({"params": {"seed": 7, "assign": {"project": p.id, "scene": "hook"}}},
                     _clip("b-rvport0004", 720, 1280, seed=7))
    assert "clip_alt" not in _bg(p)
    b = p.brief
    b["scenes"][0]["background"]["clip_alt"] = _clip("b-rvland0002", 1280, 720, seed=7)
    p.save_brief(B.normalize(b)[0])
    ai._assign_broll({"params": {"seed": 7, "assign": {"project": p.id, "scene": "hook"}}},
                     _clip("b-rvport0005", 720, 1280, seed=7, image_sha="abc"))
    assert "clip_alt" not in _bg(p)


def test_picking_another_clip_in_the_editor_drops_the_stale_variant(broll_project, client):
    p = broll_project
    b = p.brief
    b["scenes"][0]["background"]["clip"] = _clip("b-rvport0006", 720, 1280, prompt="something else")
    r = client.put(f"/api/projects/{p.id}", json={"brief": b})
    assert r.status_code == 200 and "clip_alt" not in r.json()["brief"]["scenes"][0]["background"]
    assert "clip_alt" not in _bg(p)
    b = p.brief                     # a clip of the same b-roll keeps its variant
    b["scenes"][0]["background"].update(clip="b-rvport0001", clip_alt="b-rvland0001")
    assert client.put(f"/api/projects/{p.id}", json={"brief": b}).status_code == 200
    assert _bg(p)["clip_alt"] == "b-rvland0001"


class _QueueAI:
    def __init__(self):
        self.submitted = []

    class monitor:
        @staticmethod
        def refresh():
            return {}

    def submit(self, kind, params, **kw):
        self.submitted.append((kind, params))
        return {"id": f"j{len(self.submitted)}"}

    def _assign_broll(self, job, cid):
        pass


def test_variant_of_an_image_scene_animates_the_same_image(broll_project, tmp_path):
    from conftest import png_bytes
    p = broll_project
    src = tmp_path / "photo.png"
    src.write_bytes(png_bytes(64, 64))
    name, _ = p.add_asset(src, "photo.png")
    b = p.brief
    bg = b["scenes"][0]["background"]
    bg.update(image=name, prompt="the photo, slowly zooming")
    bg.pop("clip", None)
    bg.pop("clip_alt", None)
    p.save_brief(B.normalize(b, p.assets())[0])
    ai = _QueueAI()
    pl = _bare_pipelines(ai=ai)
    pl._wait_jobs = lambda it, st, ids, base: []
    it = {"id": "t1", "project": p.id, "quality": "draft", "notices": [], "stages": {s: PL._stage() for s in PL.STAGES}}
    pl._ai(it, it["stages"]["ai"])
    [(k1, main), (k2, var)] = ai.submitted
    assert k1 == k2 == "broll" and main["orientation"] == "portrait" and var["orientation"] == "landscape"
    assert var["assign"]["variant"] is True
    assert var.get("image") and var["image"]["sha"] == main["image"]["sha"] and var["image"]["path"] == main["image"]["path"]
    # an existing variant made from text only (an earlier run) is not this scene's variant: it is made again
    b = p.brief
    b["scenes"][0]["background"]["clip_alt"] = _clip("b-rvland0003", 1280, 720, prompt="the photo, slowly zooming")
    p.save_brief(B.normalize(b, p.assets())[0])
    ai.submitted.clear()
    pl._ai(it, it["stages"]["ai"])
    assert [prm["orientation"] for _, prm in ai.submitted] == ["portrait", "landscape"]


# ------------------------------------------------------------------ 5. ComfyUI extra_args: allowlist, studio flags last
import comfy as C          # noqa: E402


@pytest.mark.parametrize("args", [
    "--liste 0.0.0.0", "--list 0.0.0.0", "--outp D:/x", "--output-dir=D:/x", "--po=9", "--enable-cors *",
    "--listen=0.0.0.0", "-listen 0.0.0.0", "-l", "--LISTEN 0.0.0.0", "--lowvr", "--extra-model-paths-config x.yaml",
    "--whitelist-custom-nodes evil", "--verbose DEBUG log.txt", "--auto-launch", "--lowvram 0.0.0.0",
    "--cache-lru 10 0.0.0.0", "0.0.0.0", "--base-directory D:/x", "--models-directory D:/x",
])
def test_comfy_extra_args_refuses_abbreviations_and_unknown_flags(args):
    with pytest.raises(ValueError):
        C.check_args(args)


@pytest.mark.parametrize("args", [
    "--lowvram", "--cache-lru 10", "--cache-lru=10", "--fast", "--fast fp16_accumulation cublas_ops",
    "--fp8_e4m3fn-unet --bf16-vae", "--async-offload", "--async-offload 2", "--cuda-device 0 --use-sage-attention",
])
def test_comfy_extra_args_allows_known_tuning_flags(args):
    assert C.check_args(args) == args


def test_comfy_studio_flags_come_last_and_win():
    c = object.__new__(C.Comfy)
    c.settings = {**C.DEFAULTS, "extra_args": "--use-sage-attention --cache-lru 10"}
    cmd = c.command()
    assert cmd.index("--use-sage-attention") < cmd.index("--listen") and cmd.index("--cache-lru") < cmd.index("--port")
    assert cmd[cmd.index("--listen") + 1] == "127.0.0.1"
    for f in ("--listen", "--port", "--output-directory", "--input-directory", "--user-directory"):
        assert cmd.index(f) > cmd.index("--cache-lru") + 1


# ------------------------------------------------------------------ 6. a failed template swap keeps the installed one
import pathlib             # noqa: E402

import templates as TP     # noqa: E402
from conftest import make_zip, manifest   # noqa: E402


def test_failed_template_replace_rolls_back(tmp_path, monkeypatch):
    files = {"keep-me/manifest.json": manifest("keep-me"), "keep-me/template.css": "#stage{outline:0} /* v1 */"}
    z = tmp_path / "t.zip"
    z.write_bytes(make_zip(files))
    TP.import_zip(z)
    z.write_bytes(make_zip({**files, "keep-me/template.css": "#stage{outline:0} /* v2 */"}))
    real = pathlib.Path.rename

    def flaky(self, target):
        if self.name.startswith(".new-"):
            raise PermissionError("file in use")          # e.g. antivirus / an open preview on Windows
        return real(self, target)
    monkeypatch.setattr(pathlib.Path, "rename", flaky)
    with pytest.raises(OSError):
        TP.import_zip(z, replace=True)
    monkeypatch.undo()
    assert "v1" in (TP.ROOT / "keep-me" / "template.css").read_text()
    assert not [p for p in TP.ROOT.iterdir() if p.name.startswith((".new-", ".old-"))]
    TP.import_zip(z, replace=True)                        # works once the file is free again
    assert "v2" in (TP.ROOT / "keep-me" / "template.css").read_text()
    TP.delete("keep-me")


# ------------------------------------------------------------------ 7. each output's sidecar has its own warnings
import jobs as J           # noqa: E402


def test_render_sidecars_do_not_share_warnings(monkeypatch):
    raw = {"product": "Ovoz", "language": "uz", "languages": ["uz", "ru"], "formats": ["9:16", "16:9"],
           "scenes": [{"id": "hook", "layout": "hero", "headline": "Ovoz", "say": {"uz": "Salom.", "ru": "Привет."}}]}
    p = Project.create(B.normalize(raw)[0], "api")
    try:
        voiced = {"uz": 0, "ru": 1}               # uz has a silent scene, ru is complete

        def tl_of(proj, lg, f):
            return {"_file": f"timeline-{lg}-{build.fmt_slug(f)}.json", "frames": 30, "duration": 1.0, "width": 10,
                    "height": 10, "voiced": voiced[lg], "voice_needed": 1, "scenes": [], "captions": []}
        monkeypatch.setattr(build, "align_all", lambda *a, **k: {})
        monkeypatch.setattr(build, "write_render_timeline", tl_of)
        monkeypatch.setattr(J.exports, "srt", lambda tl: "")
        monkeypatch.setattr(J.exports, "vtt", lambda tl: "")
        monkeypatch.setattr(J.exports, "cover_time", lambda tl, c: 0.5)
        jobs = J.Jobs.__new__(J.Jobs)
        jobs.procs = {}

        def fake_render(job, cmd, *a):
            pathlib.Path(cmd[cmd.index("--out") + 1]).write_bytes(b"mp4")
            return 0, []
        monkeypatch.setattr(jobs, "_run_render", fake_render, raising=False)
        res = jobs._render({"id": "rj1", "project": p.id, "params": {"skip_voice": True}})
        by = {(o["language"], o["format"]): o["warnings"] for o in res["outputs"]}
        assert by[("uz", "9:16")] == by[("uz", "16:9")] == ["uz: 1 scene(s) without voice-over (silence)"]
        assert by[("ru", "9:16")] == by[("ru", "16:9")] == []
        assert by[("uz", "9:16")] is not by[("uz", "16:9")]
        side = json.loads((p.renders_dir / res["renders"][3]).with_suffix(".json").read_text(encoding="utf-8"))
        assert side["language"] == "ru" and side["warnings"] == []
        assert res["warnings"] == ["uz: 1 scene(s) without voice-over (silence)"]
    finally:
        p.delete()


# ------------------------------------------------------------------ 8. Ovoz validation 422 vs "no audio" 422
import fake_ovoz           # noqa: E402
import i18n as I           # noqa: E402

V = build.V


@pytest.fixture()
def ovoz(monkeypatch):
    srv = fake_ovoz.serve(0, refuse="арабском")
    sleeps = []
    monkeypatch.setattr(V, "_sleep", lambda s: sleeps.append(s))
    fake_ovoz.State.calls.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}", sleeps
    srv.shutdown()


def test_ovoz_validation_error_is_not_retried_nor_called_no_audio(ovoz):
    api, sleeps = ovoz
    with pytest.raises(V.TTSError) as e:
        V.tts("x" * 5001, "edge", "ru-RU-SvetlanaNeural", "ru", api=api)
    err = e.value
    assert err.status == 422 and err.invalid and err.attempts == 1 and not sleeps and len(fake_ovoz.State.calls) == 1
    assert err.detail == "text: String should have at most 5000 characters"
    assert str(err) == "Ovoz rejected the request: text: String should have at most 5000 characters"
    assert build.fail_code(err, "edge") == "invalid"
    assert I.tr(str(err), "uz").startswith("Ovoz soʻrovni rad etdi: ")
    # the engine's own "no audio" 422 (a string detail) is still retried and still means no_audio_online
    with pytest.raises(V.TTSError) as e:
        V.tts("Говорит на арабском.", "edge", "ru-RU-SvetlanaNeural", "ru", api=api)
    assert not e.value.invalid and e.value.attempts == 3 and build.fail_code(e.value, "edge") == "no_audio_online"


def test_invalid_fail_code_has_ui_strings():
    from pathlib import Path
    static = Path(build.__file__).parent / "static"
    for f in ("strings.js", "strings-uz.js"):
        txt = (static / f).read_text(encoding="utf-8")
        assert "      invalid: " in txt and "{detail}" in txt.split("      invalid: ", 1)[1].split("\n", 1)[0]


# ------------------------------------------------------------------ 9. alignment cache vs the product name
import align as A          # noqa: E402
from conftest import tone_wav   # noqa: E402


def test_alignment_cache_reapplies_the_product_spelling(tmp_path, monkeypatch):
    wav = tone_wav(tmp_path / "l.wav", seconds=2.0)
    text = "Go'da ishlaydi."
    asr = [{"w": "Goda", "start": 0.1, "end": 0.6}, {"w": "ishlaydi", "start": 0.7, "end": 1.5}]
    monkeypatch.setattr(A, "transcribe", lambda w, lang, prompt=None: asr)
    first = A.timing(wav, text, "uz", product="Go")          # computed + cached: product Go -> tutuq belgisi ʼ
    assert [w["w"] for w in first["words"]] == ["Goʼda", "ishlaydi."]
    monkeypatch.setattr(A, "transcribe", lambda *a, **k: pytest.fail("cached: no second transcription"))
    other = A.timing(wav, text, "uz", product="")            # same audio + text, no product: the letter oʻ
    assert [w["w"] for w in other["words"]] == ["Goʻda", "ishlaydi."]
    assert [(w["start"], w["end"]) for w in other["words"]] == [(w["start"], w["end"]) for w in first["words"]]
    assert [w["w"] for w in A.timing(wav, text, "uz", product="Go", run=False)["words"]] == ["Goʼda", "ishlaydi."]


# ------------------------------------------------------------------ 10. localize once per language in generate()
def test_generate_localizes_once_per_language(monkeypatch):
    raw = {"product": "Ovoz", "language": "uz", "languages": ["uz", "ru"],
           "scenes": [{"id": f"s{i}", "layout": "hero", "headline": "Ovoz", "say": {"uz": f"Salom {i}.", "ru": f"Привет {i}."}}
                      for i in range(6)]}
    p = Project.create(B.normalize(raw)[0], "api")
    try:
        calls = []
        real = build.localize
        monkeypatch.setattr(build, "localize", lambda b, lg=None: calls.append(lg) or real(b, lg))

        def refuse(*a, **k):
            raise V.TTSError("Ovoz error 400: Noma'lum ovoz", status=400)
        monkeypatch.setattr(V, "tts", refuse)
        res = build.generate(p, skip_failed=True)
        assert res["total"] == 12 and len(res["failed"]) == 12 and res["generated"] == []
        # todo_lines, voice_failures and the line count: once per language each (was 2 per scene per language)
        assert len(calls) <= 3 * 2, calls
    finally:
        p.delete()
