"""Voice-over building blocks shared by make_shared.py and the Video Studio.

Talks to the Ovoz TTS app over HTTP (POST /api/tts), converts the answer to a
trimmed 48 kHz mono WAV, joins clips with padding and splits each line into
short timed caption chunks. No CLI here; import it.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
import wave
from pathlib import Path

VIDEO = Path(__file__).resolve().parent.parent
FFMPEG = str(VIDEO / "bin" / "ffmpeg.exe")
RATE = 48000
DEFAULT_API = "http://localhost:8000"


class TTSError(RuntimeError):
    """Ovoz refused or could not be reached. `unreachable` is True when it is down."""

    def __init__(self, message: str, unreachable: bool = False, status: int | None = None):
        super().__init__(message)
        self.unreachable = unreachable
        self.status = status


def tts(text: str, model: str, voice: str, lang: str, rate: int = 0, style: str | None = None,
        api: str = DEFAULT_API, timeout: float = 600) -> bytes:
    """Ask Ovoz for one clip. Returns the raw audio bytes (mp3 or wav)."""
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
        raise TTSError(f"Ovoz error {e.code}: {body}", status=e.code) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise TTSError(f"Ovoz TTS is not reachable at {api} ({reason})", unreachable=True) from None


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


def display_text(text: str, lang: str = "uz", product: str = "") -> str:
    """Typewriter apostrophes -> proper Uzbek letters for on-screen text (oʻ gʻ ʼ). Other languages unchanged."""
    if lang != "uz" or not text:
        return text
    return re.sub(r"\S+", lambda m: _uz_word(m.group(0), product), text)


_PUNCT_END = re.compile(r"[.,!?:;،؟…]$")


def _chunk_groups(words: list[str], max_words: int) -> list[list[int]]:
    groups, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if len(cur) == max_words or _PUNCT_END.search(w) or i == len(words) - 1:
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
        out.append({"text": " ".join(words[i]["w"] for i in g), "start": round(a, 3), "end": round(b, 3),
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
        chunks.append({"text": " ".join(words[j] for j in g), "start": round(t, 3), "end": round(t + dt, 3)})
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
