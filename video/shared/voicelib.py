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


def display_text(text: str, lang: str = "uz") -> str:
    """Typewriter apostrophes -> proper Uzbek letters for on-screen captions (oʻ gʻ ʼ)."""
    if lang != "uz":
        return text
    text = re.sub(r"([oOgG])['‘’`]", r"\1ʻ", text)
    return re.sub(r"(?<=\w)['’`](?=\w)", "ʼ", text)


def caption_chunks(text: str, start: float, length: float, max_words: int = 3, lang: str = "uz") -> list[dict]:
    """Split a line into short chunks timed by character weight (punctuation adds a pause)."""
    words = display_text(text, lang).split()
    if not words:
        return []
    weight = [len(re.sub(r"\W", "", w)) + 1 + (3 if re.search(r"[.,!?:;،؟]$", w) else 0) for w in words]
    total = sum(weight)
    chunks, cur, t = [], [], start
    for i, w in enumerate(words):
        cur.append(i)
        end_of_phrase = re.search(r"[.,!?:;،؟]$", w)
        if len(cur) == max_words or end_of_phrase or i == len(words) - 1:
            dt = length * sum(weight[j] for j in cur) / total
            chunks.append({"text": " ".join(words[j] for j in cur), "start": round(t, 3), "end": round(t + dt, 3)})
            t += dt
            cur = []
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
