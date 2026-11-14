"""ResembleAI Chatterbox Multilingual (v3, MIT) for Russian, English and Arabic.

Uses the multilingual model code vendored in the Chatterbox training toolkit (cloned
into .models/ by scripts/setup_models.py), so no extra package is needed. It clones a
reference clip; for a native accent, the clip should be a native speaker of the language.
"""
import random
import sys

import numpy as np

from .base import Voice, reference_voices
from .chatterbox_engine import TOOLKIT, ChatterboxEngine, _trim_tail

# Delivery presets: (exaggeration, cfg_weight). Resemble recommends a lower cfg_weight
# with higher exaggeration for more expressive, faster-paced speech.
STYLES = {
    "oddiy": ("Oddiy", 0.5, 0.5),
    "jonli": ("Jonli", 0.7, 0.4),
    "hayajonli": ("Hayajonli", 0.9, 0.3),
}


class ChatterboxMultilingualEngine(ChatterboxEngine):
    id = "chatterbox-ml"
    name = "Chatterbox Multilingual"
    description = ("ResembleAI'ning 23 tilli modeli (v3): rus, ingliz va arab tilida tabiiy gapiradi. "
                   "Namuna ovozni klonlaydi — ona tilida gapiradigan odamning namunasini qo'shing. "
                   "Ifoda kuchini tanlash mumkin.")
    license = "MIT"
    commercial = True
    url = "https://huggingface.co/ResembleAI/chatterbox"
    vram_gb = 4
    max_chunk_chars = 200
    languages = ("ru", "en", "ar")

    def voices(self):
        # The built-in speaker is a native English speaker; it can read other languages with an accent.
        builtin = Voice("default", "Standart diktor (amerikalik)", lang="en", cross_lingual=True)
        return [builtin] + reference_voices(langs=self.languages)

    def styles(self):
        return [{"id": k, "name": label} for k, (label, _, _) in STYLES.items()]

    def load(self):
        import torch

        if str(TOOLKIT) not in sys.path:
            sys.path.insert(0, str(TOOLKIT))
        from src.chatterbox_.mtl_tts import ChatterboxMultilingualTTS

        self.model = ChatterboxMultilingualTTS.from_pretrained("cuda", t3_model="v3")
        self.builtin_conds = self.model.conds
        self.current_voice = None
        self._torch = torch

    def generate(self, text, voice, speed, style=None, lang="en"):
        torch = self._torch
        _, exaggeration, cfg_weight = STYLES.get(style or "oddiy", STYLES["oddiy"])
        # The reference clip is processed once per voice; generate() itself applies a new
        # exaggeration to the prepared conditionals, so changing the style needs no re-prep.
        if self.current_voice != voice.cache_key:
            if voice.clone:
                self.model.prepare_conditionals(str(voice.ref_wav), exaggeration=exaggeration)
            else:
                self.model.conds = self.builtin_conds
            self.current_voice = voice.cache_key
        random.seed(42)
        np.random.seed(42)
        torch.manual_seed(42)
        wav = self.model.generate(text, language_id=lang, exaggeration=exaggeration, cfg_weight=cfg_weight)
        wav = wav.squeeze().float().cpu().numpy()
        return _trim_tail(wav, self.model.sr), self.model.sr
