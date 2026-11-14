"""Aisha's Navoiy TTS (CosyVoice2 fine-tune, ~600 h of Uzbek, emotion control).
Runs in .models/navoiy-venv via navoiy_worker.py (CosyVoice needs older libraries)."""
import json
from pathlib import Path

from .base import reference_voices
from .worker import MODELS, WorkerEngine

REPO = "aisha-org/navoiy-tts"
DEFAULT_STYLE = "xotirjam"


class NavoiyEngine(WorkerEngine):
    id = "navoiy"
    name = "Navoiy TTS (Aisha)"
    description = "Aisha'ning ochiq modeli: CosyVoice2 asosida ~600 soat o'zbekcha nutqda o'qitilgan, his-tuyg'u bilan gapira oladi. Namuna ovozni klonlaydi."
    license = "Apache-2.0"
    commercial = True
    url = "https://huggingface.co/" + REPO
    vram_gb = 3
    supports_rate = True
    max_chunk_chars = 300
    venv = "navoiy-venv"
    worker = "navoiy_worker.py"
    setup_hint = "python scripts/setup_navoiy.py"

    def __init__(self):
        super().__init__()
        self._emotions = None

    def missing_dependency(self):
        if not (MODELS / "CosyVoice" / "cosyvoice").exists():
            return f"CosyVoice kodi yo'q ({self.setup_hint})"
        return super().missing_dependency()

    def voices(self):
        return reference_voices()

    def emotions(self) -> dict:
        """{uz_name: instruction} from the model's emotions_40h.json (cached download)."""
        if self._emotions is None:
            from huggingface_hub import try_to_load_from_cache

            path = try_to_load_from_cache(REPO, "emotions_40h.json")
            entries = json.loads(Path(path).read_text(encoding="utf-8")) if isinstance(path, str) else []
            self._emotions = {e["uz"]: e["instruct"] for e in entries} or {
                DEFAULT_STYLE: "Speak in a calm, steady, neutral voice with even pacing and relaxed intonation."
            }
        return self._emotions

    def styles(self):
        return [{"id": k, "name": k.replace("'", "ʻ").capitalize()} for k in self.emotions()]

    def generate(self, text, voice, speed, style=None):
        instruct = self.emotions().get(style or DEFAULT_STYLE) or self.emotions()[DEFAULT_STYLE]
        return self.request(text=text, ref=str(voice.ref_wav), instruct=instruct, speed=speed)
