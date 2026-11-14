import importlib.util

from .base import Engine, reference_voices


class F5Engine(Engine):
    id = "f5"
    name = "F5-TTS Uzbek"
    description = "F5-TTS asosida o'zbekchaga moslangan tajriba modeli (14.6 soat, 2 diktor). Namuna ovozni klonlaydi."
    license = "CC BY-NC 4.0"
    commercial = False
    url = "https://huggingface.co/mrmuminov/f5-tts-uzbek"
    vram_gb = 3
    supports_rate = True
    max_chunk_chars = 200

    def missing_dependency(self):
        if importlib.util.find_spec("f5_tts") is None:
            return "f5-tts o'rnatilmagan"
        return None

    def voices(self):
        return reference_voices(require_text=True)

    def load(self):
        from f5_tts.api import F5TTS
        from huggingface_hub import hf_hub_download

        ckpt = hf_hub_download("mrmuminov/f5-tts-uzbek", "model.safetensors")
        vocab = hf_hub_download("mrmuminov/f5-tts-uzbek", "vocab.txt")
        self.model = F5TTS(model="F5TTS_v1_Base", ckpt_file=ckpt, vocab_file=vocab, device="cuda")

    def generate(self, text, voice, speed):
        wav, sr, _ = self.model.infer(
            str(voice.ref_wav), voice.ref_text, text, nfe_step=32, speed=speed, seed=0, show_info=lambda *a: None
        )
        return wav, sr
