"""Uzbek Chatterbox fine-tune. Uses the model code vendored in the training toolkit
(github.com/gokhaneraslan/chatterbox-finetuning), cloned into .models/ by scripts/setup_models.py."""
import importlib.util
import random
import sys
from pathlib import Path

import numpy as np

from .base import Engine, Voice, reference_voices

TOOLKIT = Path(__file__).resolve().parent.parent / ".models" / "chatterbox-finetuning"
UZ_REPO = "Abduqayum/uzbek-tts-natural-speech-chatterbox"


def _trim_tail(wav: np.ndarray, sr: int, threshold: float = 0.01) -> np.ndarray:
    """Cut trailing silence/noise (the AR model sometimes babbles after the sentence)."""
    frame = int(0.02 * sr)
    energy = np.sqrt(np.convolve(wav**2, np.ones(frame) / frame, mode="same"))
    loud = np.nonzero(energy > threshold)[0]
    return wav[: min(len(wav), loud[-1] + int(0.15 * sr))] if len(loud) else wav


class ChatterboxEngine(Engine):
    id = "chatterbox"
    name = "Chatterbox Uzbek"
    description = "ResembleAI Chatterbox, ~30 soat o'zbekcha audiokitobda o'qitilgan. O'z diktori bor, boshqa ovozni ham klonlaydi."
    license = "MIT"
    commercial = True
    url = "https://huggingface.co/" + UZ_REPO
    vram_gb = 5
    max_chunk_chars = 140

    def missing_dependency(self):
        if not (TOOLKIT / "src" / "chatterbox_").exists():
            return "Chatterbox toolkit yo'q (python scripts/setup_models.py)"
        for mod in ("perth", "s3tokenizer", "conformer", "diffusers"):
            if importlib.util.find_spec(mod) is None:
                return f"{mod} o'rnatilmagan"
        return None

    def voices(self):
        return [Voice("diktor", "Diktor (asl ovoz)")] + reference_voices()

    def load(self):
        import torch
        from huggingface_hub import hf_hub_download
        from safetensors.torch import load_file

        if str(TOOLKIT) not in sys.path:
            sys.path.insert(0, str(TOOLKIT))
        from src.chatterbox_.models.s3gen import S3Gen
        from src.chatterbox_.models.t3 import T3
        from src.chatterbox_.models.t3.modules.t3_config import T3Config
        from src.chatterbox_.models.tokenizers import EnTokenizer
        from src.chatterbox_.models.voice_encoder import VoiceEncoder
        from src.chatterbox_.tts import ChatterboxTTS, Conditionals

        dev = "cuda"
        base = {f: hf_hub_download("ResembleAI/chatterbox", f) for f in ("ve.safetensors", "s3gen.safetensors", "conds.pt")}
        uz = {f: hf_hub_download(UZ_REPO, f) for f in ("t3_finetuned_merged.safetensors", "tokenizer.json", "reference_voice.wav")}
        self.default_ref = uz["reference_voice.wav"]

        ve = VoiceEncoder()
        ve.load_state_dict(load_file(base["ve.safetensors"]))
        t3 = T3(hp=T3Config.multilingual())
        missing, _ = t3.load_state_dict(load_file(uz["t3_finetuned_merged.safetensors"]), strict=False)
        if missing:
            raise RuntimeError(f"Chatterbox T3 weights missing keys: {missing[:5]}")
        s3gen = S3Gen()
        s3gen.load_state_dict(load_file(base["s3gen.safetensors"]), strict=False)
        conds = Conditionals.load(base["conds.pt"]).to(dev)
        self.model = ChatterboxTTS(
            t3.to(dev).eval(), s3gen.to(dev).eval(), ve.to(dev).eval(),
            EnTokenizer(uz["tokenizer.json"]), dev, conds=conds,
        )
        self.current_voice = None
        self._torch = torch

    def generate(self, text, voice, speed):
        torch = self._torch
        if self.current_voice != voice.cache_key:
            ref = self.default_ref if voice.id == "diktor" else str(voice.ref_wav)
            self.model.prepare_conditionals(ref, exaggeration=0.5)
            self.current_voice = voice.cache_key
        random.seed(42)
        np.random.seed(42)
        torch.manual_seed(42)
        with torch.no_grad():
            wav = self.model.generate(text, repetition_penalty=2.0, cfg_weight=0.5, temperature=0.8, exaggeration=0.5)
        wav = wav.squeeze().float().cpu().numpy()
        return _trim_tail(wav, self.model.sr), self.model.sr
