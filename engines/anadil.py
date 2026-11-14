import importlib.util
import json
from pathlib import Path

from .base import Engine, reference_voices


class AnadilEngine(Engine):
    id = "anadil"
    name = "Anadil Uzbek (VoxCPM2)"
    description = "VoxCPM2 + LoRA, Common Voice o'zbekcha ma'lumotlarida o'qitilgan. Namuna ovozni klonlaydi."
    license = "MIT (asos: Apache-2.0)"
    commercial = True
    url = "https://huggingface.co/Anadilorg/Anadil_Uzbek_TTS"
    vram_gb = 7
    max_chunk_chars = 250

    def missing_dependency(self):
        if importlib.util.find_spec("voxcpm") is None:
            return "voxcpm o'rnatilmagan"
        return None

    def voices(self):
        return reference_voices()

    def load(self):
        from huggingface_hub import snapshot_download
        from voxcpm.core import VoxCPM
        from voxcpm.model.voxcpm2 import LoRAConfig

        repo = Path(snapshot_download("Anadilorg/Anadil_Uzbek_TTS", allow_patterns=["*.json", "*.safetensors"]))
        cfg = json.loads((repo / "lora_config.json").read_text(encoding="utf-8")).get("lora_config", {})
        self.model = VoxCPM.from_pretrained(
            hf_model_id="openbmb/VoxCPM2", load_denoiser=False, optimize=False,
            lora_config=LoRAConfig(**cfg), lora_weights_path=str(repo / "lora_weights.safetensors"),
        )

    def generate(self, text, voice, speed):
        audio = self.model.generate(
            text=text, reference_wav_path=str(voice.ref_wav), cfg_value=2.0,
            inference_timesteps=10, normalize=False, max_len=2000, min_len=2,
        )
        return audio, self.model.tts_model.sample_rate
