"""Follow-up message for the source AI, version history (snapshots, coalescing, restore), i18n of messages."""
import json

import pytest

import brief as B
import followup
import history as HI
import i18n as I
from conftest import EXAMPLE
from store import Project

ASSETS = {"home.png": {"type": "image", "w": 1170, "h": 2532}, "wide.png": {"type": "image", "w": 1920, "h": 1080},
          "tiny.png": {"type": "image", "w": 400, "h": 800}, "clip.webm": {"type": "video", "w": 720, "h": 1280, "duration": 14.0}}


def brief_with_problems():
    raw = {"product": "Nimbo", "language": "en", "languages": ["en", "uz"],
           "brand": {"logo": "clip.webm"},
           "scenes": [
               {"id": "hook", "layout": "hero", "say": {"en": "Meet Nimbo"}, "headline": {"en": "Meet Nimbo", "uz": "Nimbo"}},
               {"id": "shot", "layout": "screen", "device": "phone", "asset": "wide.png", "say": {"en": "x", "uz": "y"}},
               {"id": "tiny", "layout": "screen", "device": "phone", "asset": "tiny.png", "say": {"en": "x", "uz": "y"}},
               {"id": "rec", "layout": "screen", "device": "phone", "asset": "clip.webm", "say": {"en": "x", "uz": "y"}},
               {"id": "gone", "layout": "screen", "device": "browser", "asset": "missing.png",
                "say": {"en": " ".join(["word"] * 25), "uz": "y"}},
               {"id": "cta", "layout": "cta", "say": {"en": "Try it", "uz": "Sinab koʻring"}}]}
    return B.normalize(raw, ASSETS)[0]


def test_followup_finds_every_problem():
    r = followup.build(brief_with_problems(), ASSETS)
    types = {i["type"] for i in r["issues"]}
    assert {"missing_file", "invalid_file", "orientation", "low_resolution", "long_recording", "translation",
            "too_long", "empty_text"} <= types
    assert not r["ok"] and r["lang"] == "en"
    msg = r["message"]
    assert msg.startswith("Hi! Thanks for the material for “Nimbo promo”.")
    assert "missing.png (scene “gone”) was not received — please send a browser screenshot" in msg
    assert "clip.webm is used as the logo but is a video" in msg
    assert "Uzbek (uz) is missing in 1 scene(s) — “hook”: voice-over (say)" in msg
    assert "the voice-over has 25 words" in msg
    assert "{\"en\": \"...\", \"uz\": \"...\"}" in msg               # multi-language reply format
    assert r["counts"]["missing_file"] == 1


def test_followup_all_good_and_uzbek_option():
    b, _ = B.normalize({"product": "Nimbo", "language": "en", "brand": {"logo": "home.png"},
                        "scenes": [{"id": "a", "layout": "hero", "say": "Hello", "headline": "Hi"}]})
    r = followup.build(b, ASSETS)
    assert r["ok"] and "Everything I need is here" in r["message"]
    uz = followup.build(brief_with_problems(), ASSETS, lang="uz")
    assert uz["lang"] == "uz" and uz["message"].startswith("Salom! “Nimbo promo” uchun materiallar uchun rahmat.")
    assert "missing.png (“gone” sahnasi) kelmadi" in uz["message"]
    assert "Qanday javob berish kerak:" in uz["message"]
    assert followup.build(b, ASSETS, lang="xx")["lang"] == "en"


# ---------------------------------------------------------------- history
@pytest.fixture()
def project():
    raw = json.loads((EXAMPLE / "brief.json").read_text(encoding="utf-8"))
    b, _ = B.normalize(raw)
    p = Project.create(b, "api")
    p.save_brief(b, kind="import", label="Imported")
    yield p
    p.delete()


def test_history_snapshots_coalesce_and_restore(project, monkeypatch):
    p = project
    first = HI.listing(p)
    assert len(first) == 1 and first[0]["kind"] == "import" and first[0]["summary"].startswith("Imported")
    b = p.brief
    b["title"] = "New title"
    m1 = p.save_brief(B.normalize(b)[0])
    assert m1["kind"] == "edit" and "title: Nimbo launch promo → New title" in m1["summary"]
    b["scenes"][0]["headline"] = "Changed"
    m2 = p.save_brief(B.normalize(b)[0])                         # rapid edits: same snapshot, updated in place
    assert m2["id"] == m1["id"] and "headline" in m2["summary"]
    assert p.save_brief(p.brief) is None                          # nothing changed: no snapshot
    monkeypatch.setattr(HI, "COALESCE", 0.0)
    b["scenes"].pop()
    m3 = p.save_brief(B.normalize(b)[0])
    assert m3["id"] != m1["id"] and "deleted scene" in m3["summary"]
    ids = [h["id"] for h in HI.listing(p)]
    assert ids == sorted(ids, reverse=True) and len(ids) == 3
    old = HI.get(p, first[0]["id"])
    p.save_brief(old["brief"], kind="restore", label="Restored the version from 1 Jan")
    assert p.brief["title"] == "Nimbo launch promo" and len(HI.listing(p)) == 4
    assert HI.listing(p)[0]["kind"] == "restore"


def test_history_ids_are_ascii_digits(project):
    assert HI.get(project, "00001") is not None
    for bad in ("²", "../project", "1.json", "", "1" * 20):
        assert HI.get(project, bad) is None


def test_history_keeps_max(project, monkeypatch):
    monkeypatch.setattr(HI, "COALESCE", 0.0)
    monkeypatch.setattr(HI, "MAX_SNAPSHOTS", 5)
    b = project.brief
    for i in range(8):
        b["title"] = f"t{i}"
        project.save_brief(B.normalize(b)[0])
    assert len(HI.listing(project)) == 5
    assert len(list((project.dir / "history").glob("0*.json"))) == 5


# ---------------------------------------------------------------- i18n
@pytest.mark.parametrize("en,uz", [
    ("product name missing — using 'Product'", "mahsulot nomi yoʻq — 'Product' ishlatiladi"),
    ("scene 'hook': voice-over has 25 words (aim for <= 18)", "'hook' sahnasi: ovoz matnida 25 ta soʻz bor (18 tadan oshmasin)"),
    ("shot.gif: unsupported file type (images: png jpg webp; recordings: mp4 mov webm)",
     "shot.gif: bu fayl turi qoʻllab-quvvatlanmaydi (rasmlar: png jpg webp; yozuvlar: mp4 mov webm)"),
    ("Imported — 7 scenes", "Import qilindi — 7 ta sahna"),
    ("title: A → B · added scene “x” · reordered scenes", "sarlavha: A → B · sahna qoʻshildi: “x” · sahnalar tartibi oʻzgardi"),
    ("uz 9:16: frame 30/300 · ~12s left", "uz 9:16: kadr 30/300 · ~12 s qoldi"),
    ("project not found", "loyiha topilmadi"),
])
def test_tr_uzbek(en, uz):
    assert I.tr(en, "uz") == uz
    assert I.tr(en, "en") == en


def test_tr_unknown_passes_through_and_job_views():
    assert I.tr("something new happened", "uz") == "something new happened"
    j = {"message": "word timing", "error": "render uz 9:16 failed: boom", "result": {"warnings": ["no brief given — created a blank project"]}}
    t = I.tr_job(j, "uz")
    assert t["message"] == "soʻz vaqtlari" and t["error"].startswith("render uz 9:16 muvaffaqiyatsiz")
    assert t["result"]["warnings"] == ["brif berilmadi — boʻsh loyiha yaratildi"]
    assert j["message"] == "word timing"                           # the stored job is not changed


def test_history_names_templates():
    """QA #8: "template: — → bold" / bare "template" -> both template names (no template = Aurora)."""
    base = {"title": "x", "scenes": []}
    assert HI.summarize({**base, "template": "clean"}, base) == "template: Clean → Aurora"
    assert HI.summarize(base, {**base, "template": "bold"}) == "template: Aurora → Bold"
    assert HI.summarize({**base, "template": "clean"}, {**base, "template": "bold"}) == "template: Clean → Bold"
    assert HI.summarize(base, {**base, "template": "gone-tpl"}) == "template: Aurora → Gone-tpl"   # not installed
    assert I.tr("template: Clean → Aurora", "uz") == "shablon: Clean → Aurora"


def test_old_template_summaries_are_relabelled(project, monkeypatch):
    # versions saved before template names were shown said "edit template" / "template: — → bold"
    monkeypatch.setattr(HI, "COALESCE", 0.0)
    b = project.brief
    b["template"] = "clean"
    project.save_brief(B.normalize(b)[0])
    b.pop("template")
    project.save_brief(B.normalize(b)[0])
    idx = HI._index(project)
    idx[-1]["summary"] = "template"
    idx[-2]["summary"] = "template: — → clean"
    HI._write_index(project, idx)
    top = HI.listing(project)[:2]
    assert top[0]["summary"] == "template: Clean → Aurora" and top[1]["summary"] == "template: Aurora → Clean"
