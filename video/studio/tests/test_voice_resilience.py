"""QA bugs #1/#2/#3/#6: a voice line Ovoz refuses is retried, then skipped (the other lines are still generated),
reported with a reason code and per-line actions; "Make video" pauses instead of failing; spoken-text overrides;
pipeline ETA. Ovoz is the in-process fake (tests/fake_ovoz.py); nothing here needs the network or a GPU."""
from __future__ import annotations

import json
import time

import pytest

import align
import brief as B
import build
import fake_ovoz
import jobs as J
import pipeline as PL
from store import Project

V = build.V
REFUSED = "Говорит на узбекском, русском, английском и арабском."


@pytest.fixture(scope="module")
def ovoz():
    srv = fake_ovoz.serve(0, refuse="арабском")
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def fast(monkeypatch, ovoz):
    sleeps = []
    monkeypatch.setattr(V, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(build, "OVOZ", ovoz)
    fake_ovoz.State.calls.clear()
    return sleeps


def _brief(**over) -> dict:
    raw = {"product": "Ovoz", "language": "uz", "languages": ["uz", "ru"],
           "voices": {"uz": {"model": "edge", "voice": "uz-UZ-MadinaNeural"},
                      "ru": {"model": "edge", "voice": "ru-RU-SvetlanaNeural"}},
           "scenes": [{"id": "hook", "layout": "hero", "headline": "Ovoz",
                       "say": {"uz": "Ovoz matnni ovozga aylantiradi.", "ru": "Ovoz превращает текст в речь."}},
                      {"id": "languages", "layout": "chips", "headline": "4 til", "items": ["uz", "ru"],
                       "say": {"uz": "Oʻzbek, rus, ingliz va arab tillarida gapiradi.", "ru": REFUSED}},
                      {"id": "cta", "layout": "cta", "headline": "Ovoz",
                       "say": {"uz": "Bugun sinab koʻring.", "ru": "Попробуйте сегодня."}}]}
    raw.update(over)
    return B.normalize(raw)[0]


@pytest.fixture()
def project():
    p = Project.create(_brief(), "api")
    yield p
    p.delete()


# ------------------------------------------------------------------ voicelib.tts retries
def test_tts_retries_a_refusal_with_backoff_then_gives_up(ovoz, fast):
    with pytest.raises(V.TTSError) as e:
        V.tts(REFUSED, "edge", "ru-RU-SvetlanaNeural", "ru", api=ovoz)
    assert e.value.status == 422 and e.value.attempts == 3 and len(fake_ovoz.State.calls) == 3
    assert len(fast) == 2 and 1.0 <= fast[0] <= 2.0 and 2.8 <= fast[1] <= 5.2     # backoff with 0.7..1.3 jitter
    assert "Matnni tekshiring" in e.value.detail


def test_tts_does_not_retry_bad_requests_or_a_dead_ovoz(monkeypatch, fast):
    def bad(*a, **k):
        raise V.TTSError("Ovoz error 400: Noma'lum ovoz", status=400)
    monkeypatch.setattr(V, "_tts_once", bad)
    with pytest.raises(V.TTSError) as e:
        V.tts("x", "edge", "nope", "ru")
    assert e.value.attempts == 1 and not fast
    monkeypatch.undo()
    monkeypatch.setattr(V, "_sleep", lambda s: fast.append(s))
    with pytest.raises(V.TTSError) as e:
        V.tts("x", "edge", "v", "ru", api="http://127.0.0.1:9")
    assert e.value.unreachable and e.value.attempts == 1


def test_tts_succeeds_on_a_later_attempt(monkeypatch, fast):
    n = []

    def flaky(*a, **k):
        n.append(1)
        if len(n) < 3:
            raise V.TTSError("Ovoz error 502: edge: network", status=502)
        return b"RIFF"
    monkeypatch.setattr(V, "_tts_once", flaky)
    retries = []
    assert V.tts("x", "edge", "v", "ru", on_retry=lambda i, e: retries.append(i)) == b"RIFF"
    assert retries == [1, 2] and len(fast) == 2


# ------------------------------------------------------------------ build.generate keeps going
def test_generate_skips_the_refused_line_and_finishes_the_rest(project):
    res = build.generate(project)
    assert len(res["generated"]) == 5 and res["total"] == 6
    [f] = res["failed"]
    assert (f["lang"], f["scene"], f["code"], f["status"], f["attempts"]) == ("ru", "languages", "no_audio_online", 422, 3)
    assert build.failed_summary(res["failed"]) == "1 line failed (ru · languages)"
    vm = build.voiced_map(project)
    assert all(vm["uz"].values()) and vm["ru"] == {"hook": True, "languages": False, "cta": True}
    # remembered (survives a reload) until the line changes
    assert [(x["lang"], x["scene"], x["code"]) for x in build.voice_failures(project)] == [("ru", "languages", "no_audio_online")]
    b = project.brief
    b["scenes"][1]["say"]["ru"] = "Говорит на четырёх языках: узбекском, русском, английском и арабском."
    project.save_brief(B.normalize(b)[0])
    assert build.voice_failures(project) == []          # edited text = a new line; the old failure no longer applies


def test_another_voice_for_one_line(project):
    b = project.brief
    b["scenes"][1]["voices"] = {"ru": {"model": "edge", "voice": "ru-RU-DmitryNeural"}}
    project.save_brief(B.normalize(b)[0])
    res = build.generate(project)
    assert res["failed"] == [] and len(res["generated"]) == 6
    ru = [c for c in fake_ovoz.State.calls if c["language"] == "ru"]
    assert {c["voice"] for c in ru} == {"ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"}
    assert [c["voice"] for c in ru if c["text"] == REFUSED] == ["ru-RU-DmitryNeural"]


def test_silent_line_is_not_needed_and_keeps_its_captions(project):
    b = project.brief
    b["scenes"][1]["silent"] = ["ru"]
    project.save_brief(B.normalize(b)[0])
    assert project.brief["scenes"][1]["silent"] == ["ru"]
    res = build.generate(project)
    assert res["failed"] == [] and res["total"] == 5
    tl = build.timeline(project, lang="ru")
    sc = next(s for s in tl["scenes"] if s["id"] == "languages")
    assert sc["silent"] is True and not sc["voiced"] and tl["voiced"] == tl["voice_needed"] == 2
    assert any("арабском" in c["text"] for c in tl["captions"])         # captions still show the line
    uz = build.timeline(project, lang="uz")
    assert not next(s for s in uz["scenes"] if s["id"] == "languages")["silent"]


def test_unreachable_ovoz_still_stops_the_run(project, monkeypatch):
    monkeypatch.setattr(build, "OVOZ", "http://127.0.0.1:9")
    with pytest.raises(V.TTSError) as e:
        build.generate(project)
    assert e.value.unreachable


def test_fail_codes():
    E = V.TTSError
    assert build.fail_code(E("x", status=422), "edge") == "no_audio_online"
    assert build.fail_code(E("x", status=422), "navoiy") == "no_audio"
    assert build.fail_code(E("x", status=502), "edge") == "service_error"
    assert build.fail_code(E("x", status=400), "edge") == "rejected"
    assert build.fail_code(E("x", status=503), "f5") == "model_missing"
    assert build.fail_code(E("x", unreachable=True), "edge") == "unreachable"


# ------------------------------------------------------------------ the voice job (API)
def test_voice_job_ends_with_n_lines_failed_and_lists_them(client, server, project, monkeypatch):
    monkeypatch.setattr(build, "align_all", lambda p, langs=None, progress=None: {"aligned": 0, "estimated": 0, "lines": 0})
    j = client.post(f"/api/projects/{project.id}/voice", json={}).json()
    for _ in range(200):
        j = client.get(f"/api/jobs/{j['id']}").json()
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    assert j["status"] == "error" and j["error"] == "1 line failed (ru · languages)"
    assert j["result"]["failed"][0]["code"] == "no_audio_online" and len(j["result"]["generated"]) == 5
    assert j["result"]["voiced"] == 5 and j["result"]["voice_needed"] == 6
    d = client.get(f"/api/projects/{project.id}").json()
    assert [(f["lang"], f["scene"]) for f in d["voice_failures"]] == [("ru", "languages")]
    assert d["voiced_by_lang"]["ru"]["hook"] is True               # the editor sees what was generated (bug #2)
    uz = client.get(f"/api/jobs/{j['id']}", headers={"X-UI-Lang": "uz"}).json()
    assert uz["error"] == "1 ta satr ovozlanmadi (ru · languages)"


# ------------------------------------------------------------------ "Make video" pauses
class _FakeAI:
    current = None
    jobs: dict = {}

    class pool:
        @staticmethod
        def release_gpu():
            return []

        @staticmethod
        def view():
            return {"ram_gb": 0}

    class monitor:
        @staticmethod
        def refresh():
            return {}

    class comfy:
        @staticmethod
        def reachable(t=1.0):
            return False

    @staticmethod
    def estimate(kind, preset, units, key=None, warm=False):
        return 100.0

    @staticmethod
    def estimate_broll(provider, preset, i2v=False):
        return 300.0


def _pl():
    pl = PL.Pipelines.__new__(PL.Pipelines)            # no worker thread, no pipelines.json
    pl.ai, pl.jobs, pl.phase = _FakeAI, None, None
    pl.items, pl._saved = {}, 0.0
    pl._save = lambda force=False: None
    return pl


def _item(pid):
    return {"id": "t1", "project": pid, "quality": "final", "status": "running", "notices": [],
            "stages": {s: PL._stage() for s in PL.STAGES}, "created": 0}


def test_make_video_pauses_on_failed_lines_and_continues_silent(project, monkeypatch):
    monkeypatch.setattr(build, "align_all", lambda p, langs=None, progress=None: {"aligned": 0, "estimated": 0, "lines": 0})
    pl = _pl()
    it = _item(project.id)
    st = it["stages"]["voice"]
    with pytest.raises(PL.Paused) as e:
        pl._voice(it, st)
    assert "1 line failed (ru · languages)" in str(e.value) and it["voice_failures"][0]["scene"] == "languages"
    assert "5 line(s) generated" in st["message"]
    # "Continue without those lines": the stage passes with a warning
    it2 = {**_item(project.id), "skip_failed": True}
    st2 = it2["stages"]["voice"]
    n = len(fake_ovoz.State.calls)
    pl._voice(it2, st2)
    assert len(fake_ovoz.State.calls) == n                 # no second round of retries for the known failure
    assert st2["state"] == "warning" and any("continuing with it silent" in n for n in it2["notices"])


# ------------------------------------------------------------------ ETA / elapsed
def test_pipeline_eta_sums_the_remaining_stages():
    v = {"stages": {s: PL._stage() for s in PL.STAGES}}
    v["stages"]["voice"].update(state="done", estimate=30)
    v["stages"]["unload"].update(state="done", estimate=6)
    v["stages"]["ai"].update(state="running", eta=7, estimate=400)     # the music job's own ETA
    v["stages"]["render"].update(state="pending", estimate=240)
    assert PL.Pipelines.eta(v) == 247                                   # not just the 7 s of the running job
    v["stages"]["ai"].update(eta=None, elapsed=100)
    assert PL.Pipelines.eta(v) == 300 + 240


def test_render_estimate_uses_measured_speed(project):
    tl = build.timeline(project)
    base = PL.render_estimate(project)
    n = len(build.formats_of(project.brief)) * len(build.langs_of(project.brief))
    assert base == pytest.approx(sum(build.timeline(project, lang=lg)["duration"] for lg in build.langs_of(project.brief))
                                 * PL.RENDER_FACTOR + 4 * n + 5, abs=0.5)
    project.renders_dir.mkdir(parents=True, exist_ok=True)
    (project.renders_dir / "uz-9x16-1.json").write_text(json.dumps({"format": "9:16", "duration": 40, "seconds": 80,
                                                                     "template": "aurora"}))
    assert PL.render_estimate(project) > base and tl["duration"] > 0


def test_ai_plan_estimates_broll_and_music(project):
    b = project.brief
    b["scenes"][0]["background"] = {"type": "broll", "prompt": "teal ribbons", "seed": 7}
    project.save_brief(B.normalize(b)[0])
    plan = _pl().ai_plan(project, "final")
    assert ("broll", 300.0) in plan
    assert PL.broll_seed(project.brief["scenes"][0]["background"]) == 7 and PL.broll_seed({}) == 42


def test_elapsed_resets_per_attempt():
    pl = _pl()
    pl.queued = lambda: []
    now = time.time()
    it = {"id": "x", "status": "running", "started": now - 600, "attempt_started": now - 30, "spent": 300.0,
          "stages": {s: PL._stage() for s in PL.STAGES}}
    v = pl.view(it)
    assert 29 <= v["elapsed"] <= 32 and 329 <= v["total_elapsed"] <= 332


# ------------------------------------------------------------------ spoken text vs captions (#6)
def test_say_tts_and_pronunciation_table(project):
    b = project.brief
    b["pronounce"] = {"ru": {"Ovoz": "Овоз"}}
    b["scenes"][2]["say_tts"] = {"ru": "Попробуйте Овоз сегодня."}
    project.save_brief(B.normalize(b)[0])
    lb = B.localize(project.brief, "ru")
    assert B.tts_text(lb, lb["scenes"][0]) == "Овоз превращает текст в речь."
    assert lb["scenes"][0]["say"] == "Ovoz превращает текст в речь."          # captions keep the brand spelling
    assert B.tts_text(lb, lb["scenes"][2]) == "Попробуйте Овоз сегодня."
    uz = B.localize(project.brief, "uz")
    assert B.tts_text(uz, uz["scenes"][0]) == uz["scenes"][0]["say"]           # no table for uz, no say_tts fallback
    assert uz["scenes"][2]["say_tts"] == ""
    build.generate(project, langs=["ru"])
    spoken = {c["text"] for c in fake_ovoz.State.calls}
    assert "Овоз превращает текст в речь." in spoken and "Попробуйте Овоз сегодня." in spoken


def test_remap_words_maps_spoken_times_onto_caption_words():
    spoken = [{"w": "Попробуйте", "start": 0.1, "end": 0.7}, {"w": "Овоз", "start": 0.8, "end": 1.2},
              {"w": "сегодня.", "start": 1.3, "end": 1.9}]
    out = align.remap_words(spoken, ["Попробуйте", "Ovoz", "сегодня."], "ru")
    assert [(w["w"], w["start"], w["end"]) for w in out] == [("Попробуйте", 0.1, 0.7), ("Ovoz", 0.8, 1.2),
                                                             ("сегодня.", 1.3, 1.9)]
    out = align.remap_words([{"w": "эй-ай", "start": 0.0, "end": 0.6}, {"w": "помогает", "start": 0.7, "end": 1.2}],
                            ["AI", "(beta)", "помогает"], "ru")
    assert [w["w"] for w in out] == ["AI", "(beta)", "помогает"]
    assert out[0]["start"] == 0.0 and out[1]["end"] <= 0.6 + 1e-6 and out[2]["start"] == 0.7
