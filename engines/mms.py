import importlib.util

import numpy as np

from uzbek import latin_to_cyrillic

from .base import Engine, Voice

MODELS = {
    "mms-male": ("facebook/mms-tts-uzb-script_cyrillic", "Meta MMS (erkak)"),
    "mms-female": ("MuzaffarSharofitdinov/mms-tts-uzbek-girl-voice_cyrillic", "Qiz ovozi (MMS fine-tune)"),
}


class MMSEngine(Engine):
    id = "mms"
    name = "Meta MMS-TTS"
    description = "Kichik va tez VITS modeli (kirill yozuvida o'qitilgan). Sifati robotga yaqin."
    license = "CC BY-NC 4.0"
    commercial = False
    url = "https://huggingface.co/facebook/mms-tts-uzb-script_cyrillic"
    vram_gb = 0.5
    supports_rate = True
    max_chunk_chars = 400

    def missing_dependency(self):
        if importlib.util.find_spec("transformers") is None:
            return "transformers o'rnatilmagan"
        return None

    def voices(self):
        return [Voice(k, label) for k, (_, label) in MODELS.items()]

    def load(self):
        import torch
        from transformers import AutoTokenizer, VitsModel

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = {
            key: (VitsModel.from_pretrained(repo).to(self.device).eval(), AutoTokenizer.from_pretrained(repo))
            for key, (repo, _) in MODELS.items()
        }

    def generate(self, text, voice, speed):
        import torch

        model, tok = self.model[voice.id]
        model.speaking_rate = speed
        inputs = tok(latin_to_cyrillic(text).lower(), return_tensors="pt").to(self.device)
        torch.manual_seed(0)
        with torch.no_grad():
            wav = model(**inputs).waveform[0].float().cpu().numpy()
        return wav.astype(np.float32), model.config.sampling_rate
