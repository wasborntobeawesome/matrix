"""Caption chunking and the word-timing (alignment) fallback. faster-whisper is mocked: no model is loaded."""
from pathlib import Path

import pytest

import align
import voicelib as V
from conftest import tone_wav


def test_caption_chunks_cover_the_line():
    ch = V.caption_chunks("Meet Nimbo, the budgeting app that finally makes money feel calm.", 1.0, 4.0, lang="en")
    assert [c["text"] for c in ch] == ["Meet Nimbo,", "the budgeting app", "that finally makes", "money feel calm."]
    assert ch[0]["start"] == 1.0 and ch[-1]["end"] == pytest.approx(5.0, abs=0.01)
    assert all(a["end"] == pytest.approx(b["start"], abs=0.002) for a, b in zip(ch, ch[1:]))   # back to back
    assert all(len(c["text"].split()) <= 3 for c in ch)
    assert V.caption_chunks("", 0, 2) == []


def test_caption_chunks_fix_uzbek_on_screen():
    ch = V.caption_chunks("Endi to'rt tilda", 0, 2, lang="uz")
    assert ch[0]["text"] == "Endi toʻrt tilda"


def test_chunks_from_words_bridges_short_gaps_keeps_pauses():
    words = [{"w": "One", "start": 0.0, "end": 0.3}, {"w": "two", "start": 0.35, "end": 0.6},
             {"w": "three.", "start": 0.65, "end": 1.0},
             {"w": "Four", "start": 2.0, "end": 2.3}]              # a real 1 s pause before "Four"
    ch = V.chunks_from_words(words, max_words=3, end=2.6)
    assert [c["text"] for c in ch] == ["One two three.", "Four"]
    assert ch[0]["end"] == pytest.approx(1.15)                    # pause kept: last word + 0.15
    assert ch[1]["end"] == 2.6 and ch[0]["words"][1] == [0.35, 0.6]


def test_align_words_interpolates_unmatched():
    script = ["Meet", "Nimbo,", "the", "budgeting", "app."]
    asr = [{"w": "Meet", "start": 0.1, "end": 0.4}, {"w": "Nimble", "start": 0.45, "end": 0.9},
           {"w": "budgeting", "start": 1.2, "end": 1.8}, {"w": "app", "start": 1.85, "end": 2.1}]
    out, frac = align.align_words(script, asr, "en", 2.5)
    assert frac >= 0.6
    assert [o["w"] for o in out] == script
    starts = [o["start"] for o in out]
    assert starts == sorted(starts)                               # monotonic
    assert out[0]["start"] == pytest.approx(0.1, abs=0.05) and out[3]["start"] == pytest.approx(1.2, abs=0.05)
    assert out[2]["start"] >= out[1]["start"]                     # "the" (unmatched) placed between neighbours


@pytest.fixture()
def wav(tmp_path) -> Path:
    return tone_wav(tmp_path / "line.wav", 2.0)


def test_alignment_fallback_when_model_missing(monkeypatch, wav):
    monkeypatch.setattr(align, "transcribe", lambda *a, **k: None)
    monkeypatch.setattr(align, "_model_err", "faster-whisper not installed")
    r = align.compute(wav, "Hello there world", "en")
    assert r["method"] == "estimated" and r["error"]
    assert align.cached(wav, "Hello there world", "en") is None   # not cached: may work later


def test_alignment_fallback_when_asr_disagrees(monkeypatch, wav):
    monkeypatch.setattr(align, "transcribe", lambda *a, **k: [{"w": "zzz", "start": 0.0, "end": 1.0}])
    r = align.compute(wav, "Hello there wonderful world", "en")
    assert r["method"] == "estimated" and r["matched"] < align.MIN_MATCHED
    assert align.cached(wav, "Hello there wonderful world", "en")["method"] == "estimated"


def test_alignment_used_when_asr_matches(monkeypatch, wav):
    asr = [{"w": "Hello", "start": 0.2, "end": 0.5}, {"w": "there", "start": 0.6, "end": 0.9},
           {"w": "world", "start": 1.0, "end": 1.6}]
    monkeypatch.setattr(align, "transcribe", lambda *a, **k: asr)
    r = align.compute(wav, "Hello there world", "en")
    assert r["method"] == "aligned" and r["matched"] == 1.0
    ch = V.chunks_from_words([{"w": w["w"], "start": w["start"], "end": w["end"]} for w in r["words"]], end=2.0)
    assert ch[0]["text"] == "Hello there world" and ch[0]["start"] == pytest.approx(0.2, abs=0.02)


def test_estimate_speech():
    assert V.estimate_speech("", "en") == 0.0
    assert V.estimate_speech("a" * 145, "en") == pytest.approx(10.25, abs=0.01)
    assert V.estimate_speech("a" * 145, "en", rate=100) < V.estimate_speech("a" * 145, "en")
