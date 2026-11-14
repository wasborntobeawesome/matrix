"""Qwen3-TTS based Uzbek models (Alibaba Qwen architecture). Both run in
.models/qwen-venv through qwen_worker.py because qwen-tts pins transformers 4.57."""
from .base import Voice, reference_voices
from .worker import WorkerEngine


class Qwen3UzbekEngine(WorkerEngine):
    id = "qwen3uz"
    name = "Qwen3-TTS Uzbek"
    description = "Alibaba Qwen3-TTS (0.6B) asosida, 20 diktorning ~33 soatlik o'zbekcha nutqida o'qitilgan. Namuna ovozni klonlaydi."
    license = "Apache-2.0"
    commercial = True
    url = "https://huggingface.co/mrmuminov/Qwen3-TTS-12Hz-0.6B-Uzbek"
    vram_gb = 3
    max_chunk_chars = 250
    venv = "qwen-venv"
    worker = "qwen_worker.py"
    worker_args = ["mrmuminov/Qwen3-TTS-12Hz-0.6B-Uzbek"]
    setup_hint = "python scripts/setup_qwen.py"

    supports_rate = True
    # The model speaks ~2.6-3.3 words/s (others ~1.9): the pace is baked into the
    # checkpoint and no generation setting changes it, so we slow the audio down with
    # WSOLA time-stretching, which keeps the pitch. 1.35 brings it to a natural pace.
    stretch = 1.35

    def voices(self):
        return reference_voices()

    def generate(self, text, voice, speed):
        import numpy as np
        import pytsmod

        audio, sr = self.request(mode="clone", text=text, ref=str(voice.ref_wav))
        factor = self.stretch / speed  # speed slider: >1 faster, <1 slower
        if abs(factor - 1) > 0.01:
            audio = pytsmod.wsola(audio, factor).astype(np.float32)
        return audio, sr


SAYRO_STYLES = {"oddiy": "", "neytral": "Neutral", "xursand": "Happy", "hayajonli": "Excited"}


class SayroEngine(WorkerEngine):
    id = "sayro"
    name = "Sayro TTS (UzLM)"
    description = ("Qwen3-TTS (1.7B) asosida UzLM jamoasi tayyorlagan model: o'zbekcha yangiliklar, IT va podkast "
                   "nutqida o'qitilgan, o'z diktori bor. Foydalanish shartlari (sayro.uz/terms) tasdiqlanmagan — "
                   "tijoriy foydalanishdan oldin tekshiring.")
    license = "Sayro Terms of Use"
    commercial = False
    terms_unclear = True
    url = "https://huggingface.co/uzlm/sayro-tts-1.7B"
    vram_gb = 6
    max_chunk_chars = 250
    venv = "qwen-venv"
    worker = "qwen_worker.py"
    worker_args = ["uzlm/sayro-tts-1.7B"]
    setup_hint = "python scripts/setup_qwen.py"

    def missing_dependency(self):
        from huggingface_hub import try_to_load_from_cache

        if not isinstance(try_to_load_from_cache("uzlm/sayro-tts-1.7B", "model.safetensors"), str):
            return "Hugging Face ruxsati kutilmoqda (huggingface.co/uzlm/sayro-tts-1.7B), so'ng: python scripts/setup_qwen.py"
        return super().missing_dependency()

    def voices(self):
        return [Voice("sayro", "Sayro (asl ovoz)")]

    def styles(self):
        return [{"id": k, "name": k.capitalize()} for k in SAYRO_STYLES]

    def generate(self, text, voice, speed, style=None):
        return self.request(mode="custom", text=text, speaker=voice.id, instruct=SAYRO_STYLES.get(style or "oddiy", ""))
