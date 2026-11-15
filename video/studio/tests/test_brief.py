"""Brief parsing and normalisation: old briefs, multi-language, formats, Uzbek apostrophes."""
import json

import pytest

import brief as B
from conftest import EXAMPLE
from voicelib import display_text


def test_example_brief_normalizes_cleanly():
    raw = json.loads((EXAMPLE / "brief.json").read_text(encoding="utf-8"))
    b, warn = B.normalize(raw)
    assert b["product"] == "Nimbo" and b["language"] == "en" and b["languages"] == ["en"]
    assert b["format"] == "9:16" and b["formats"] == ["9:16"]
    assert [s["id"] for s in b["scenes"]][:3] == ["hook", "dashboard", "add"]
    assert b["voice"] == {"model": "edge", "voice": "en-US-AvaNeural", "rate": 0}
    assert warn == []
    # normalising twice changes nothing (the studio re-normalises on every save)
    assert B.normalize(b)[0] == b


def test_old_style_brief_is_upgraded():
    old = {"name": "Oldie", "language": "UZ", "format": "portrait",
           "scenes": [{"layout": "hero", "voiceover": "Salom!", "headline": "Hi"},
                      {"id": "Two Words", "layout": "LIST", "vo": "Bir ikki", "items": ["a", "b"]},
                      {"layout": "wobble", "say": "x"}, "not a scene"]}
    b, warn = B.normalize(old)
    assert b["product"] == "Oldie" and b["language"] == "uz" and b["format"] == "9:16"
    assert [s["id"] for s in b["scenes"]] == ["s1", "two-words", "s3"]
    assert b["scenes"][0]["say"] == "Salom!" and b["scenes"][1]["say"] == "Bir ikki"
    assert b["scenes"][1]["layout"] == "list" and b["scenes"][2]["layout"] == "hero"
    assert any("layout 'wobble' unknown" in w for w in warn)
    assert any("scene 4 is not an object" in w for w in warn)
    assert b["voice"]["voice"] == "uz-UZ-MadinaNeural"           # default voice of the language


@pytest.mark.parametrize("fmt,want", [("portrait", "9:16"), ("16x9", "16:9"), ("1080x1080", "1:1"), ("square", "1:1"),
                                      ("16:9", "16:9"), ("weird", "9:16")])
def test_format_aliases(fmt, want):
    b, warn = B.normalize({"product": "P", "language": "en", "format": fmt, "scenes": [{"layout": "hero"}]})
    assert b["format"] == want
    assert (want == "9:16" and fmt == "weird") == any("unknown" in w for w in warn)


def test_multi_language_and_formats():
    raw = {"product": "Nimbo", "language": "ru", "languages": "uz, ru ,xx", "formats": ["16:9", "9:16", "9:16", "4:3"],
           "voices": {"uz": {"model": "edge", "voice": "uz-UZ-SardorNeural"}},
           "scenes": [{"id": "a", "layout": "hero", "say": {"uz": "Salom", "ru": "Привет"}, "headline": {"ru": "Да"}},
                      {"id": "b", "layout": "chips", "say": "Только русский",
                       "items": {"uz": ["bir", "ikki"], "ru": ["один", "два"]}}]}
    b, warn = B.normalize(raw)
    assert b["languages"] == ["ru", "uz"]                        # main language first, duplicates/unknown dropped
    assert b["formats"] == ["16:9", "9:16"] and b["format"] == "16:9"
    assert b["voices"]["uz"]["voice"] == "uz-UZ-SardorNeural"
    assert any("'xx' not supported" in w for w in warn) and any("'4:3' unknown" in w for w in warn)
    assert b["scenes"][1]["items"] == [{"uz": "bir", "ru": "один"}, {"uz": "ikki", "ru": "два"}]
    miss = B.missing_translations(b)
    assert miss["a"] == {"uz": ["headline"]}
    assert miss["b"] == {"uz": ["say"]}                          # a plain-string say is missing for the 2nd language
    assert any(w.startswith("uz: missing translations in 2 scene(s)") for w in warn)
    uz = B.localize(b, "uz")
    assert uz["language"] == "uz" and uz["scenes"][0]["say"] == "Salom"
    assert uz["scenes"][0]["headline"] == "Да"                   # missing translation -> the main language
    assert uz["voice"]["voice"] == "uz-UZ-SardorNeural"
    assert B.localize(b, "xx")["language"] == "ru"               # unknown language -> the main one


def test_internal_keys_cannot_be_injected():
    raw = {"product": "P", "language": "en", "_lang": "en", "_parts": [1],
           "scenes": [{"id": "a", "layout": "hero", "say": {"en": "hi"}, "start": 99, "voiced": True, "_x": 1, "note": "kept"}]}
    b, _ = B.normalize(raw)
    assert "_lang" not in b and "_parts" not in b
    sc = b["scenes"][0]
    assert "start" not in sc and "voiced" not in sc and "_x" not in sc and sc["note"] == "kept"
    assert B.localize(b, "en")["scenes"][0]["say"] == "hi"       # a forged _lang no longer skips localisation


def test_limits_and_errors():
    with pytest.raises(B.BriefError):
        B.normalize([])
    with pytest.raises(B.BriefError):
        B.normalize({"product": "P", "scenes": []})
    b, warn = B.normalize({"product": "P", "scenes": [{"layout": "hero"}] * 20})
    assert len(b["scenes"]) == B.MAX_SCENES and any("keeping the first 12" in w for w in warn)
    b, _ = B.normalize({"product": "x" * 500, "title": "t" * 900, "scenes": [{"layout": "hero", "say": "w " * 400}]})
    assert len(b["product"]) == 60 and len(b["title"]) == 120 and len(b["scenes"][0]["say"]) <= 400


def test_broll_provider_default_and_unknown():
    b, warn = B.normalize({"product": "P", "broll_provider": "nope", "scenes": [{"layout": "hero"}]})
    assert "broll_provider" not in b and any("using fastwan-5b" in w for w in warn)
    b, _ = B.normalize({"product": "P", "broll_provider": "wan22-5b", "scenes": [{"layout": "hero"}]})
    assert b["broll_provider"] == "wan22-5b"                     # legacy, still selectable
    assert B.SCHEMA["properties"]["broll_provider"]["default"] == "fastwan-5b"


# ---------------------------------------------------------------- Uzbek apostrophes
@pytest.mark.parametrize("src,want", [
    ("o'zbek tili", "oʻzbek tili"),
    ("O'ZBEKISTON", "OʻZBEKISTON"),
    ("g'oya", "gʻoya"),
    ("ma'no", "maʼno"),                                          # tutuq belgisi between letters
    ("san`at", "sanʼat"),
    ("bo‘lim va bo’sh", "boʻlim va boʻsh"),                    # curly / backtick variants
    ("Nimbo'da", "Nimboʼda"),                                    # product + case suffix: tutuq, not oʻ
    ("Payme'ga", "Paymeʼga"),
    ("to'rt tilda", "toʻrt tilda"),
])
def test_uzbek_display_text(src, want):
    assert display_text(src, "uz", "Nimbo") == want


def test_uzbek_only_on_screen_and_only_for_uz():
    assert display_text("o'clock", "en") == "o'clock"
    b, _ = B.normalize({"product": "Nimbo", "language": "uz",
                        "scenes": [{"id": "a", "layout": "hero", "say": "Nimbo'da o'zbekcha", "headline": "Nimbo'da o'zbekcha"}]})
    loc = B.localize(b, "uz")["scenes"][0]
    assert loc["headline"] == "Nimboʼda oʻzbekcha"
    assert loc["say"] == "Nimbo'da o'zbekcha"                   # the TTS gets the text as written
