"""Per-render exports: .srt / .vtt subtitles (from the timeline's aligned captions) and the cover time."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import voicelib as V  # noqa: E402

MAX_LINE = 42             # characters per subtitle line (the usual broadcast / platform limit)
MAX_CHARS = 2 * MAX_LINE  # a cue has at most two lines
MAX_CUE = 6.0             # seconds
SENTENCE_END = re.compile(r"[.!?؟…]$")
DASHES = "-–—"


def _join(a: str, b: str) -> str:
    """Two caption chunks -> one text (a no-break space after a Russian one-letter preposition)."""
    last = a.split()[-1] if a.split() else ""
    return a + (V.NBSP if V.short_word(last) else " ") + b


def fits(text: str) -> bool:
    """The text wraps into at most two lines of MAX_LINE characters."""
    return all(len(x) <= MAX_LINE for x in lines(text))


def _split(q: dict) -> list[dict]:
    """A cue that can't wrap into 2 x MAX_LINE -> several cues (time shared by characters)."""
    if fits(q["text"]):
        return [q]
    words = q["text"].split(" ")
    parts, cur = [], ""
    for w in words:
        cand = f"{cur} {w}" if cur else w
        if cur and not fits(cand):
            parts.append(cur)
            cur = w
        else:
            cur = cand
    parts.append(cur)
    total = sum(len(x) for x in parts) or 1
    out, t, dur = [], q["start"], q["end"] - q["start"]
    for x in parts:
        dt = dur * len(x) / total
        out.append({**q, "text": x, "start": round(t, 3), "end": round(t + dt, 3)})
        t += dt
    return out


def cues(tl: dict) -> list[dict]:
    """Merge the 3-word caption chunks into subtitle cues (<= 2 lines of 42 chars, <= 6 s, split at
    sentence ends, never across scenes or real pauses)."""
    out: list[dict] = []
    for c in tl.get("captions") or []:
        sc = next((s for s in tl["scenes"] if s["start"] - 0.01 <= c["start"] < s["end"]), None)
        sid = sc["id"] if sc else None
        end = c["words"][-1][1] if c.get("words") else c["end"]
        text = V.ru_nbsp(c["text"])
        cur = out[-1] if out else None
        same = bool(cur and cur["scene"] == sid and not SENTENCE_END.search(cur["text"]) and c["start"] - cur["end"] < 0.3)
        if same and fits(_join(cur["text"], text)) and end - cur["start"] <= MAX_CUE:
            cur["text"] = _join(cur["text"], text)
            cur["end"] = end
            cur["parts"].append((text, c["start"], end))
        else:
            start = c["start"]
            # a cue never starts with a dash (in subtitles that means a new speaker): it ends the previous cue
            m = re.match(rf"^[{DASHES}]\s+(?=\S)", text)
            if m and cur and cur["scene"] == sid and fits(cur["text"] + " " + text[:m.end()].strip()):
                cur["text"] += " " + text[:m.end()].strip()
                text = text[m.end():]
            parts = [(text, start, end)]
            # no orphan: a short sentence end ("компьютере.") that does not fit takes the previous cue's last chunk
            if same and len(text) < 16 and len(cur["parts"]) > 1 and SENTENCE_END.search(text):
                pt = cur["parts"][-1]
                cand = _join(pt[0], text)
                rest = cur["parts"][:-1]
                if fits(cand):
                    cur["parts"] = rest
                    cur["text"] = rest[0][0]
                    for x in rest[1:]:
                        cur["text"] = _join(cur["text"], x[0])
                    cur["end"] = rest[-1][2]
                    text, start, parts = cand, pt[1], [pt, *parts]
            out.append({"text": text, "start": start, "end": end, "scene": sid, "parts": parts})
    out = [x for q in out for x in _split({k: v for k, v in q.items() if k != "parts"})]
    # a cue stays up at least 0.7 s (or until the next one), and never overlaps the next one
    for i, q in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else tl.get("duration", q["end"] + 1)
        q["end"] = min(max(q["end"] + 0.15, q["start"] + 0.7), nxt - 0.02 if nxt > q["start"] + 0.1 else nxt)
    return out


def lines(text: str) -> list[str]:
    """One line, or two lines: broken at an ordinary space (never at a no-break space), never so that
    the second line starts with a dash (the dash stays at the end of the first line), preferring the
    break where both lines fit MAX_LINE and are most balanced."""
    if len(text) <= MAX_LINE:
        return [text]
    cuts = [i for i, ch in enumerate(text) if ch == " " and text[i + 1:i + 2] not in tuple(DASHES)]
    if not cuts:
        return [text]

    def cost(i):
        a, b = len(text[:i]), len(text[i + 1:])
        return (max(a, b) > MAX_LINE, max(a, b), abs(a - b))
    cut = min(cuts, key=cost)
    return [text[:cut], text[cut + 1:]]


def _ts(t: float, sep: str) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def srt(tl: dict) -> str:
    rtl = tl.get("rtl")
    out = []
    for i, q in enumerate(cues(tl), 1):
        ls = lines(q["text"])
        if rtl:   # right-to-left embedding for players without bidi support
            ls = ["‫" + x + "‬" for x in ls]
        out += [str(i), f"{_ts(q['start'], ',')} --> {_ts(q['end'], ',')}", *ls, ""]
    return "\n".join(out)


def vtt(tl: dict) -> str:
    out = ["WEBVTT", f"Language: {tl.get('language', '')}", ""]
    for i, q in enumerate(cues(tl), 1):
        out += [str(i), f"{_ts(q['start'], '.')} --> {_ts(q['end'], '.')}", *lines(q["text"]), ""]
    return "\n".join(out)


def cover_time(tl: dict, cover: dict | None) -> float:
    """Default: the first scene at its most complete moment (everything in, before the exit fade)."""
    scenes = tl.get("scenes") or []
    if not scenes:
        return 0.0
    sc = scenes[0]
    if cover and cover.get("scene"):
        sc = next((s for s in scenes if s["id"] == cover["scene"]), sc)
    lo, hi = sc["start"] + 0.2, max(sc["start"] + 0.2, sc["end"] - 0.35)
    if cover and cover.get("t") is not None:
        return round(min(hi, max(lo, sc["start"] + float(cover["t"]))), 3)
    return round(hi, 3)
