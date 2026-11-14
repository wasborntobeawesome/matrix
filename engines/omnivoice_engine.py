import importlib.util

from .base import Engine, reference_voices


class OmniVoiceEngine(Engine):
    id = "omnivoice"
    name = "OmniVoice (k2-fsa)"
    description = ("600+ tilli zamonaviy model (o'zbekcha ~115 soat). Namuna ovozni klonlaydi — "
                   "rus, ingliz va arab tilida ham, lekin namuna shu tilda bo'lsa aksent tabiiyroq chiqadi.")
    languages = ("uz", "ru", "en", "ar")
    license = "Apache-2.0"
    commercial = True
    url = "https://huggingface.co/k2-fsa/OmniVoice"
    vram_gb = 4
    supports_rate = True
    max_chunk_chars = 300

    def missing_dependency(self):
        if importlib.util.find_spec("omnivoice") is None:
            return "omnivoice o'rnatilmagan"
        return None

    def voices(self):
        return reference_voices(require_text=True)

    def load(self):
        import torch
        from omnivoice import OmniVoice

        self.model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cuda:0", dtype=torch.float16)
        self.prompts = {}

    def generate(self, text, voice, speed, lang="uz"):
        if voice.id not in self.prompts:
            self.prompts[voice.id] = self.model.create_voice_clone_prompt(
                ref_audio=str(voice.ref_wav), ref_text=voice.ref_text
            )
        audio = self.model.generate(
            text=text, language=lang, voice_clone_prompt=self.prompts[voice.id], speed=speed, num_step=32
        )
        return audio[0], 24000
