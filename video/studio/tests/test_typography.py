"""Typography: Russian no-break spaces after one-letter prepositions (QA #9) and subtitle line rules
(<= 42 characters, a dash stays at the end of a line; QA #7)."""
import align
import exports
import voicelib as V
from brief import localize, normalize

NB = " "


def test_ru_nbsp_after_one_letter_words():
    assert V.ru_nbsp("Превратите текст в голос") == f"Превратите текст в{NB}голос"
    assert V.ru_nbsp("В два клика и готово") == f"В{NB}два клика и{NB}готово"
    assert V.ru_nbsp("(с любовью), а также") == f"(с{NB}любовью), а{NB}также"
    assert V.ru_nbsp("кот в") == "кот в"                         # nothing to tie to at the end
    assert V.ru_nbsp("Привет") == "Привет" and V.ru_nbsp("") == ""
    assert V.display_text("Текст в голос", "ru") == f"Текст в{NB}голос"
    assert V.display_text("a b c", "en") == "a b c"              # other languages untouched


def test_localize_ties_ru_on_screen_text_only():
    b, _ = normalize({"product": "Ovoz", "language": "uz", "languages": ["uz", "ru"], "scenes": [
        {"id": "hook", "layout": "hero", "headline": {"uz": "Matnni ovozga", "ru": "Превратите текст в голос"},
         "say": {"uz": "Salom", "ru": "Текст в живую речь"}},
        {"id": "l", "layout": "list", "headline": "x", "items": [{"uz": "a", "ru": "Записи у вас"}, "b"]}]})
    ru = localize(b, "ru")
    assert ru["scenes"][0]["headline"] == f"Превратите текст в{NB}голос"
    assert ru["scenes"][0]["say"] == "Текст в живую речь"         # the TTS text is unchanged
    assert ru["scenes"][1]["items"][0] == f"Записи у{NB}вас"


def test_word_lists_unchanged_and_chunks_keep_the_nbsp():
    line = "Превратите текст в голос"
    # Python's split() still splits at U+00A0: alignment sees the same 4 words
    assert V.display_text(line, "ru").split() == line.split()
    ch = V.caption_chunks(line, 0, 2, lang="ru")
    # "в" does not end the first 3-word chunk: it moves on with "голос", tied by a no-break space
    assert [c["text"] for c in ch] == ["Превратите текст", f"в{NB}голос"]
    words = [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(line.split())]
    ch2 = V.chunks_from_words(words, end=1.5)
    assert [c["text"] for c in ch2] == ["Превратите текст", f"в{NB}голос"]
    assert sum(len(c["words"]) for c in ch2) == 4
    # alignment's script words for a Russian line are the plain words
    out, frac = align.align_words(V.display_text(line, "ru").split(),
                                  [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(line.split())],
                                  "ru", 1.5)
    assert frac == 1.0 and [w["w"] for w in out] == line.split()


def _tl(chunks, scene_end=30.0):
    """A timeline with one scene and back-to-back caption chunks [(text, start, end)]."""
    return {"duration": scene_end, "scenes": [{"id": "s", "start": 0.0, "end": scene_end}],
            "captions": [{"text": t, "start": a, "end": b} for t, a, b in chunks]}


def _check_srt(srt: str):
    body = [ln for ln in srt.splitlines() if ln and "-->" not in ln and not ln.isdigit()]
    assert body
    for ln in body:
        assert len(ln) <= exports.MAX_LINE, ln
        assert ln[0] not in "-–—", ln
    return body


def test_srt_lines_at_most_42_characters():
    # the QA's ru "models" cue: "бесплатные модели прямо на вашем компьютере." was a 44-character line
    tl = _tl([("Девять моделей:", 17.0, 17.9), ("голоса Microsoft и", 17.9, 18.8), ("бесплатные модели прямо", 18.8, 19.9),
              ("на вашем компьютере.", 19.9, 21.0)])
    body = _check_srt(exports.srt(tl))
    assert not any(ln.endswith(" и") for ln in body)            # "и" keeps to the next word
    assert any(f"и{NB}бесплатные" in ln for ln in body)


def test_srt_dash_stays_at_line_end():
    # the QA's uz cue 8: the second line started with "— xursand…"
    tl = _tl([("Navoiy modeli oʻnta", 26.9, 27.8), ("hissiyot bilan gapiradi", 27.8, 28.9), ("— xursand ohangdan", 28.9, 30.0),
              ("pichirlashgacha.", 30.0, 31.0)])
    _check_srt(exports.srt(tl))
    # a chunk that starts a new cue with a dash: the dash ends the previous cue instead
    # (a real pause before it, so the chunks are not merged into one cue)
    tl2 = _tl([("Navoiy modeli oʻnta hissiyot", 26.9, 27.8), ("bilan gapiradi", 27.8, 28.5),
               ("— xursand ohangdan pichirlashgacha.", 29.0, 31.0)])
    q = exports.cues(tl2)
    assert all(not x["text"].startswith("—") for x in q)
    assert any(x["text"].endswith("—") for x in q)
    _check_srt(exports.srt(tl2))


def test_lines_rules():
    assert exports.lines("short") == ["short"]
    # the balanced break would be right before the dash: the dash stays at the end of line 1 instead
    a, b = exports.lines("Десять эмоций модели Навои передаёт — от радости до шёпота")
    assert not b.startswith("—") and len(a) <= 42 and len(b) <= 42
    a, b = exports.lines("a" * 20 + " " + "b" * 18 + " — " + "c" * 20 + " " + "d" * 18)
    assert a.endswith("—") and b.startswith("c") and len(a) <= 42 and len(b) <= 42
    a, b = exports.lines("Navoiy modeli oʻnta hissiyot bilan gapiradi — xursand ohangdan")
    assert len(a) <= 42 and len(b) <= 42 and not b.startswith("—")
    # 79 characters with no valid break: it doesn't fit two lines, so cues() splits such a cue
    assert not exports.fits("Navoiy modeli oʻnta hissiyot bilan gapiradi — xursand ohangdan pichirlashgacha.")
    two = exports.lines(f"Это очень длинная строка текста, которая в{NB}конце концов должна разделиться")
    assert all(not x.startswith("конце") for x in two)          # never broken at the no-break space
    assert all(not x.endswith(" в") for x in two)


def test_cue_that_cannot_fit_two_lines_is_split():
    text = " ".join(["слово"] * 30)                               # 179 characters in one chunk
    tl = _tl([(text, 0.0, 6.0)], scene_end=8.0)
    q = exports.cues(tl)
    assert len(q) >= 3
    assert all(exports.fits(x["text"]) for x in q)
    assert q[0]["start"] == 0.0 and all(a["end"] <= b["start"] for a, b in zip(q, q[1:]))
    _check_srt(exports.srt(tl))
    _check_srt(exports.vtt(tl).replace("WEBVTT", "1").replace("Language: ", "1"))


def test_no_orphan_cue_at_a_sentence_end():
    # the real ru "models" line as faster-whisper chunked it: the last word alone did not fit -> "компьютере." was
    # a 0.7 s cue of its own; now it takes the previous chunk with it
    tl = _tl([("Девять моделей:", 17.0, 17.9), ("голоса Microsoft", 17.9, 18.6), ("и бесплатные модели", 18.6, 19.6),
              ("прямо на вашем", 19.6, 20.4), ("компьютере.", 20.4, 21.0)])
    q = exports.cues(tl)
    assert [x["text"].replace(NB, " ") for x in q][-1] == "прямо на вашем компьютере."
    assert q[-1]["start"] == 19.6 and len(q) == 2
    _check_srt(exports.srt(tl))
