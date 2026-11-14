"""Reference voices added from the web UI (recorded or uploaded native speakers).

Saved as voices/<lang>/<slug>.wav with <slug>.txt (exact transcript, needed by
OmniVoice) and <slug>.name (label shown in the UI). See engines.base.reference_voices.
"""
import io
import re
import threading

import numpy as np

from engines import base
from languages import LANGUAGES

MIN_SECONDS, MAX_SECONDS = 3, 30
_USER_VOICE_ID = re.compile(rf"({'|'.join(map(re.escape, LANGUAGES))})/[a-z0-9-]{{1,40}}")
# Picking a free slug and writing its files must not interleave between two uploads.
_WRITE_LOCK = threading.Lock()


class VoiceError(ValueError):
    pass


def _slug(name: str, folder) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "ovoz"
    candidate, n = slug, 2
    while (folder / f"{candidate}.wav").exists():
        candidate, n = f"{slug}-{n}", n + 1
    return candidate


def save_voice(name: str, lang: str, transcript: str, wav_bytes: bytes) -> str:
    """Validate and store a reference clip; returns the new voice id ("ru/anna")."""
    import soundfile as sf

    name, transcript = name.strip(), transcript.strip()
    if not name or not transcript:
        raise VoiceError("Ism va oʻqilgan matn boʻsh boʻlmasligi kerak.")
    if lang not in LANGUAGES:
        raise VoiceError(f"Nomaʼlum til: {lang}")
    try:
        audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=True)
    except Exception as exc:
        raise VoiceError(f"Audio faylni o'qib bo'lmadi: {exc}")
    audio = audio.mean(axis=1)
    seconds = len(audio) / sr
    if not MIN_SECONDS <= seconds <= MAX_SECONDS:
        raise VoiceError(f"Namuna {MIN_SECONDS}–{MAX_SECONDS} soniya bo'lishi kerak (hozir {seconds:.1f} s).")
    peak = float(np.abs(audio).max())
    if peak < 0.02:
        raise VoiceError("Namuna juda jim — ovoz yozilmaganga o'xshaydi.")
    audio = audio * (0.9 / peak)  # consistent level for cloning

    folder = base.VOICES_DIR / lang
    with _WRITE_LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        slug = _slug(name, folder)
        # Transcript and label first: a listing that sees the .wav then sees complete metadata.
        (folder / f"{slug}.txt").write_text(transcript, encoding="utf-8")
        (folder / f"{slug}.name").write_text(name, encoding="utf-8")
        sf.write(folder / f"{slug}.wav", audio, sr, subtype="PCM_16")
    return f"{lang}/{slug}"


def delete_voice(voice_id: str) -> None:
    """Delete a voice added from the UI. The starter voices in voices/ can't be deleted here."""
    if not _USER_VOICE_ID.fullmatch(voice_id):
        raise VoiceError("Bu ovozni o'chirib bo'lmaydi.")
    wav = base.VOICES_DIR / f"{voice_id}.wav"
    with _WRITE_LOCK:
        if not wav.exists():
            raise VoiceError("Ovoz topilmadi.")
        for ext in (".wav", ".txt", ".name"):
            wav.with_suffix(ext).unlink(missing_ok=True)
