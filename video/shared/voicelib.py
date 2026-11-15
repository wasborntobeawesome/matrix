"""Voice-over building blocks shared by make_shared.py and the Video Studio.

Talks to the Ovoz TTS app over HTTP (POST /api/tts), converts the answer to a
trimmed 48 kHz mono WAV, joins clips with padding and splits each line into
short timed caption chunks. No CLI here; import it.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

VIDEO = Path(__file__).resolve().parent.parent
FFMPEG = str(VIDEO / "bin" / "ffmpeg.exe")
RATE = 48000
DEFAULT_API = "http://localhost:8000"


class TTSError(RuntimeError):
    """Ovoz refused or could not be reached. `unreachable` is True when it is down.

    `status` = Ovoz's HTTP status, `detail` = its message, `attempts` = how many times the line was tried,
    `invalid` = Ovoz rejected the request itself (FastAPI validation: text too long, rate out of range…)."""

    def __init__(self, message: str, unreachable: bool = False, status: int | None = None, detail: str = "",
                 attempts: int = 1, invalid: bool = False):
        super().__init__(message)
        self.unreachable = unreachable
        self.status = status
        self.detail = detail
        self.attempts = attempts
        self.invalid = invalid


# Ovoz answers 422 with a message (detail is a string) when the voice engine returned no audio (Microsoft's
# online service sends "NoAudioReceived" for some sentences, sometimes only now and then) and 5xx for engine /
# network errors: worth another try. A 422 whose detail is a LIST is FastAPI's request validation (text too
# long, rate out of range): the same request fails the same way, never retried. 400 (bad voice / language),
# 410 (voice deleted), 503 (model not installed) are not retried either.
RETRY_STATUS = {422, 500, 502, 504}


def validation_reason(detail: list) -> str:
    """FastAPI's [{loc: ["body", "text"], msg: "String should have at most 5000 characters"}, …] -> readable."""
    out = []
    for d in detail[:3]:
        if isinstance(d, dict):
            loc = [str(x) for x in d.get("loc") or [] if x != "body"]
            out.append((".".join(loc) + ": " if loc else "") + str(d.get("msg") or "invalid"))
        else:
            out.append(str(d))
    return "; ".join(out) or "invalid request"
RETRY_DELAYS = (1.5, 4.0)          # seconds before the 2nd and the 3rd attempt, times a 0.7..1.3 jitter
_sleep = time.sleep                # tests replace it


def _tts_once(text, model, voice, lang, rate, style, api, timeout) -> bytes:
    payload = {"text": text, "model": model, "voice": voice, "rate": int(rate or 0), "language": lang}
    if style:
        payload["style"] = style
    req = urllib.request.Request(api.rstrip("/") + "/api/tts", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            body = json.loads(body).get("detail", body)
        except (ValueError, AttributeError):
            pass
        if isinstance(body, list):                 # FastAPI validation error: the request itself is wrong
            reason = validation_reason(body)
            raise TTSError(f"Ovoz rejected the request: {reason}", status=e.code, detail=reason, invalid=True) from None
        raise TTSError(f"Ovoz error {e.code}: {body}", status=e.code, detail=str(body)) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise TTSError(f"Ovoz TTS is not reachable at {api} ({reason})", unreachable=True) from None


def tts(text: str, model: str, voice: str, lang: str, rate: int = 0, style: str | None = None,
        api: str = DEFAULT_API, timeout: float = 600, retries: int | None = None, on_retry=None) -> bytes:
    """Ask Ovoz for one clip. Returns the raw audio bytes (mp3 or wav).

    A refusal that may be temporary (RETRY_STATUS) is retried up to `retries` times (default
    len(RETRY_DELAYS)) with backoff and jitter; `on_retry(attempt, error)` is called before each wait."""
    delays = RETRY_DELAYS if retries is None else tuple(RETRY_DELAYS[min(i, len(RETRY_DELAYS) - 1)]
                                                        for i in range(max(0, retries)))
    for attempt in range(len(delays) + 1):
        try:
            return _tts_once(text, model, voice, lang, rate, style, api, timeout)
        except TTSError as e:
            e.attempts = attempt + 1
            if e.unreachable or e.invalid or e.status not in RETRY_STATUS or attempt >= len(delays):
                raise
            if on_retry:
                on_retry(attempt + 1, e)
            _sleep(delays[attempt] * random.uniform(0.7, 1.3))
    raise AssertionError("unreachable")


def models(api: str = DEFAULT_API, timeout: float = 5) -> list:
    """GET /api/models from Ovoz (raises TTSError when down)."""
    try:
        with urllib.request.urlopen(api.rstrip("/") + "/api/models", timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise TTSError(f"Ovoz error {e.code}", status=e.code) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError) as e:
        raise TTSError(f"Ovoz TTS is not reachable at {api} ({getattr(e, 'reason', e)})",
                       unreachable=True) from None


def to_wav(audio: bytes, out: Path) -> None:
    """Any audio -> 48 kHz mono 16-bit WAV, trimmed of leading/trailing silence."""
    trim = ("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,"
            "areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.08,areverse")
    out = Path(out)
    tmp = out.with_suffix(".tmp.wav")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", "pipe:0", "-af", trim,
                    "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(tmp)],
                   input=audio, check=True, capture_output=True)
    tmp.replace(out)


def duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


# Uzbek Latin: o' / g' are the letters oʻ gʻ (U+02BB); any other apostrophe inside a word is the
# tutuq belgisi ʼ (U+02BC). A product name followed by an apostrophe and a suffix ("Nimbo'ni",
# "Nimbo'da") is also the tutuq belgisi, not the letter oʻ.
_APOS = "'‘’`ʻʼ"
_UZ_SUFFIX = re.compile(r"^(?:lar)?(?:im|ing|i|si|imiz|ingiz|miz|ngiz)?"
                        r"(?:ni|ning|ga|ka|qa|da|dan|dagi|dek|day|cha|chi|mi|siz|li|lik|ligi)?$", re.I)


def _uz_word(w: str, product: str = "") -> str:
    def fix(m: re.Match) -> str:
        pre, rest = w[:m.start() + 1], w[m.end():]
        letter = m.group(1)
        prod = re.sub(r"\W", "", product.lower())
        tutuq = bool(prod) and re.sub(r"\W", "", pre.lower()) == prod and rest[:1].isalpha()
        # capitalised name ending in o + apostrophe + case suffix: "Nimbo'ni", "Payme'ga" stays below
        if not tutuq and letter in "oO" and len(pre) >= 4 and pre[:1].isupper() and rest and _UZ_SUFFIX.match(rest):
            tutuq = True
        return letter + ("ʼ" if tutuq else "ʻ")
    w = re.sub(r"([oOgG])['‘’`ʻ]", fix, w)
    return re.sub(r"(?<=\w)['’`](?=\w)", "ʼ", w)


NBSP = " "
# Russian typography: a one-letter preposition / conjunction never ends a line ("текст в / голос"), so it is
# tied to the next word with a no-break space. Python's str.split() and JS /\s+/ both still split at U+00A0,
# so word lists (alignment, caption word times) are unchanged; only line breaking is.
RU_SHORT = "вксиаоуяВКСИАОУЯ"
_RU_SHORT_WORD = re.compile(rf"^[{RU_SHORT}]$")
_RU_NBSP = re.compile(rf"(?<!\w)([{RU_SHORT}])[ \t]+(?=\S)")


def ru_nbsp(text: str) -> str:
    """"Превратите текст в голос" -> "Превратите текст в голос" (also after the start of a line / punctuation)."""
    return _RU_NBSP.sub(lambda m: m.group(1) + NBSP, text) if text else text


def short_word(w: str) -> bool:
    """A Russian one-letter preposition / conjunction that keeps to the next word."""
    return bool(_RU_SHORT_WORD.match(w or ""))


def join_words(words: list[str]) -> str:
    """Join words with spaces, but a no-break space after a Russian one-letter preposition / conjunction."""
    out = ""
    for i, w in enumerate(words):
        if i:
            out += NBSP if short_word(words[i - 1]) else " "
        out += w
    return out


def display_text(text: str, lang: str = "uz", product: str = "") -> str:
    """On-screen text: typewriter apostrophes -> proper Uzbek letters (oʻ gʻ ʼ); Russian one-letter
    prepositions / conjunctions get a no-break space after them. Other languages unchanged."""
    if not text:
        return text
    if lang == "ru":
        return ru_nbsp(text)
    if lang != "uz":
        return text
    return re.sub(r"\S+", lambda m: _uz_word(m.group(0), product), text)


_PUNCT_END = re.compile(r"[.,!?:;،؟…]$")


def _chunk_groups(words: list[str], max_words: int) -> list[list[int]]:
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if _PUNCT_END.search(w) or i == len(words) - 1:
            groups.append(cur)
            cur = []
        elif len(cur) >= max_words:
            if short_word(w) and len(cur) > 1:
                # "текст в" | "голос": the one-letter preposition moves on with its word
                groups.append(cur[:-1])
                cur = [i]
            else:
                groups.append(cur)
                cur = []
    return groups


def word_times(text: str, start: float, length: float, lang: str = "uz", product: str = "") -> list[dict]:
    """Estimated per-word times (character weight, punctuation adds a pause)."""
    words = display_text(text, lang, product).split()
    if not words:
        return []
    weight = [len(re.sub(r"\W", "", w)) + 1 + (3 if _PUNCT_END.search(w) else 0) for w in words]
    total = sum(weight)
    out, t = [], start
    for w, wt in zip(words, weight):
        dt = length * wt / total
        # the pause weight of a trailing comma/period is silence after the word, not the word itself
        speak = dt * (len(re.sub(r"\W", "", w)) + 1) / wt
        out.append({"w": w, "start": round(t, 3), "end": round(t + speak, 3)})
        t += dt
    return out


def chunks_from_words(words: list[dict], max_words: int = 3, end: float | None = None) -> list[dict]:
    """Caption chunks from timed words [{w, start, end}]. Each chunk keeps its word times."""
    if not words:
        return []
    groups = _chunk_groups([w["w"] for w in words], max_words)
    out = []
    for gi, g in enumerate(groups):
        a = words[g[0]]["start"]
        if gi + 1 < len(groups):
            nxt = words[groups[gi + 1][0]]["start"]
            last = words[g[-1]]["end"]
            b = nxt if nxt - last < 0.35 else last + 0.15   # bridge short gaps, keep real pauses
        else:
            b = max(words[g[-1]]["end"], end if end is not None else 0)
        out.append({"text": join_words([words[i]["w"] for i in g]), "start": round(a, 3), "end": round(b, 3),
                    "words": [[round(words[i]["start"], 3), round(words[i]["end"], 3)] for i in g]})
    return out


def caption_chunks(text: str, start: float, length: float, max_words: int = 3, lang: str = "uz",
                   product: str = "") -> list[dict]:
    """Split a line into short chunks timed by character weight (punctuation adds a pause)."""
    words = display_text(text, lang, product).split()
    if not words:
        return []
    weight = [len(re.sub(r"\W", "", w)) + 1 + (3 if _PUNCT_END.search(w) else 0) for w in words]
    total = sum(weight)
    chunks, t = [], start
    for g in _chunk_groups(words, max_words):
        dt = length * sum(weight[j] for j in g) / total
        chunks.append({"text": join_words([words[j] for j in g]), "start": round(t, 3), "end": round(t + dt, 3)})
        t += dt
    return chunks


# speaking speed used to estimate scene length before a voice exists (chars per second)
CHARS_PER_SEC = {"uz": 13.5, "ru": 14.0, "en": 14.5, "ar": 12.5}


def estimate_speech(text: str, lang: str = "en", rate: int = 0) -> float:
    n = len(re.sub(r"\s+", " ", text or "").strip())
    if not n:
        return 0.0
    cps = CHARS_PER_SEC.get(lang, 14.0) * (1 + (rate or 0) / 100)
    return max(1.0, n / max(cps, 4) + 0.25)


def join(parts: list[tuple], out: Path) -> None:
    """Join [(wav_path_or_None, seconds_if_silence, pad_after)] into one 48 kHz mono WAV.

    A part with path None becomes silence of the given length (used for scenes
    that have no voice yet), so the track always lines up with the timeline.
    """
    inputs, filters = [], []
    for i, (wav, secs, pad) in enumerate(parts):
        if wav:
            inputs += ["-i", str(wav)]
        else:
            inputs += ["-f", "lavfi", "-t", f"{max(secs, 0.01):.3f}", "-i", f"anullsrc=r={RATE}:cl=mono"]
        filters.append(f"[{i}:a]aformat=sample_rates={RATE}:channel_layouts=mono,apad=pad_dur={pad:.3f}[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[out]")
    out = Path(out)
    tmp = out.with_suffix(".tmp.wav")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filters),
                    "-map", "[out]", "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le", str(tmp)],
                   check=True, capture_output=True)
    tmp.replace(out)
