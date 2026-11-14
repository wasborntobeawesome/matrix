"""Common interface for all TTS engines (online and local GPU models)."""
from __future__ import annotations

import gc
import io
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

VOICES_DIR = Path(__file__).resolve().parent.parent / "voices"

# Only one local model runs on the GPU at a time.
GPU_LOCK = threading.Lock()


@dataclass
class Voice:
    id: str
    name: str
    ref_wav: Path | None = None   # reference clip for voice-cloning models
    ref_text: str | None = None   # exact transcript of ref_wav
    lang: str | None = None       # language the voice speaks; None = any language the engine supports


def reference_voices(require_text: bool = False) -> list[Voice]:
    """Voices in voices/: <id>.wav (+ optional <id>.txt transcript, <id>.name label)."""
    out = []
    for wav in sorted(VOICES_DIR.glob("*.wav")):
        txt, label = wav.with_suffix(".txt"), wav.with_suffix(".name")
        text = txt.read_text(encoding="utf-8").strip() if txt.exists() else None
        if require_text and not text:
            continue
        name = label.read_text(encoding="utf-8").strip() if label.exists() else wav.stem.title()
        out.append(Voice(wav.stem, name, wav, text))
    return out


def split_text(text: str, max_chars: int) -> list[str]:
    """Split into sentence-sized chunks no longer than max_chars."""
    sentences = re.split(r"(?<=[.!?…؟])\s+|\n+", text.strip())
    chunks, cur = [], ""
    for sent in filter(None, (s.strip() for s in sentences)):
        pieces = [sent]
        if len(sent) > max_chars:  # fall back to commas, then words
            pieces = re.split(r"(?<=[,;:،؛])\s+", sent)
            pieces = [w for p in pieces for w in ([p] if len(p) <= max_chars else p.split())]
        for p in pieces:
            if cur and len(cur) + 1 + len(p) > max_chars:
                chunks.append(cur)
                cur = p
            else:
                cur = f"{cur} {p}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.clip(audio, -1, 1).astype(np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class Engine:
    id: str = ""
    name: str = ""
    description: str = ""
    license: str = ""
    commercial: bool = True          # False -> non-commercial (NC) licence
    terms_unclear: bool = False      # licence terms couldn't be verified (treated as not commercial)
    kind: str = "local"              # "local" (GPU) or "online"
    url: str = ""
    vram_gb: float = 0               # rough GPU memory needed
    supports_rate = False
    supports_pitch = False
    languages: tuple[str, ...] = ("uz",)  # content languages the model can speak
    spell_numbers = True             # expand digits to words before synthesis...
    reads_digits: tuple[str, ...] = ()    # ...except in these languages, where the model reads them itself
    max_chunk_chars = 250

    def __init__(self):
        self.model = None
        self.last_used = 0.0
        self.load_error: str | None = None

    # ---- capability --------------------------------------------------------
    def missing_dependency(self) -> str | None:
        """Return a reason string if this engine can't run on this machine."""
        return None

    def voices(self) -> list[Voice]:
        return []

    def styles(self) -> list[dict]:
        """Optional speaking styles/emotions: [{"id", "name"}]."""
        return []

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def info(self) -> dict:
        reason = self.missing_dependency() or self.load_error
        return {
            "id": self.id, "name": self.name, "description": self.description,
            "license": self.license, "commercial": self.commercial, "terms_unclear": self.terms_unclear,
            "kind": self.kind, "languages": list(self.languages),
            "url": self.url, "supports_rate": self.supports_rate,
            "supports_pitch": self.supports_pitch, "loaded": self.loaded,
            "available": reason is None, "unavailable_reason": reason,
            "voices": [{"id": v.id, "name": v.name, "lang": v.lang} for v in self.voices()],
            "styles": self.styles() if reason is None else [],
        }

    # ---- lifecycle (local engines) ----------------------------------------
    def load(self):
        raise NotImplementedError

    def unload(self):
        self.model = None
        gc.collect()
        try:
            import torch

            torch.cuda.empty_cache()
        except ImportError:
            pass

    def generate(self, text: str, voice: Voice, speed: float) -> tuple[np.ndarray, int]:
        """Synthesize one chunk; return (float32 mono audio, sample_rate)."""
        raise NotImplementedError

    def synthesize(self, text: str, voice_id: str, rate: int = 0, pitch: int = 0, style: str | None = None,
                   lang: str = "uz") -> tuple[bytes, str]:
        """Blocking synthesis of arbitrary-length text. Returns (audio bytes, media type)."""
        voice = next((v for v in self.voices() if v.id == voice_id), None)
        if voice is None:
            raise ValueError(f"Unknown voice '{voice_id}' for {self.name}")
        speed = 1 + rate / 100 if self.supports_rate else 1.0
        with GPU_LOCK:
            if not self.loaded:
                from . import make_room_for

                make_room_for(self)
                self.load()
            self.last_used = time.time()
            pieces, sr = [], 24000
            for chunk in split_text(text, self.max_chunk_chars):
                extra = {"style": style} if style and self.styles() else {}
                if len(self.languages) > 1:
                    extra["lang"] = lang
                audio, sr = self.generate(chunk, voice, speed, **extra)
                pieces += [np.asarray(audio, dtype=np.float32).reshape(-1), np.zeros(int(0.2 * sr), np.float32)]
            audio = np.concatenate(pieces[:-1]) if pieces else np.zeros(1, np.float32)
        return to_wav_bytes(audio, sr), "audio/wav"
