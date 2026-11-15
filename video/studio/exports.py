"""Per-render exports: .srt / .vtt subtitles (from the timeline's aligned captions) and the cover time."""
from __future__ import annotations

import re

MAX_LINE = 42             # characters per subtitle line (the usual broadcast / platform limit)
MAX_CHARS = 2 * MAX_LINE  # a cue has at most two lines
MAX_CUE = 6.0             # seconds
SENTENCE_END = re.compile(r"[.!?؟…]$")


def cues(tl: dict) -> list[dict]:
    """Merge the 3-word caption chunks into subtitle cues (<= 2 lines of 42 chars, <= 6 s, split at
    sentence ends, never across scenes or real pauses)."""
    out: list[dict] = []
    for c in tl.get("captions") or []:
        sc = next((s for s in tl["scenes"] if s["start"] - 0.01 <= c["start"] < s["end"]), None)
        sid = sc["id"] if sc else None
        end = c["words"][-1][1] if c.get("words") else c["end"]
        cur = out[-1] if out else None
        if (cur and cur["scene"] == sid and len(cur["text"]) + 1 + len(c["text"]) <= MAX_CHARS
                and end - cur["start"] <= MAX_CUE and not SENTENCE_END.search(cur["text"])
                and c["start"] - cur["end"] < 0.3):
            cur["text"] += " " + c["text"]
            cur["end"] = end
        else:
            out.append({"text": c["text"], "start": c["start"], "end": end, "scene": sid})
    # a cue stays up at least 0.7 s (or until the next one), and never overlaps the next one
    for i, q in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else tl.get("duration", q["end"] + 1)
        q["end"] = min(max(q["end"] + 0.15, q["start"] + 0.7), nxt - 0.02 if nxt > q["start"] + 0.1 else nxt)
    return out


def lines(text: str) -> list[str]:
    """One line, or two lines broken at the space nearest to the middle."""
    if len(text) <= MAX_LINE or " " not in text:
        return [text]
    mid = len(text) / 2
    cut = min((i for i, ch in enumerate(text) if ch == " "), key=lambda i: abs(i - mid))
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
