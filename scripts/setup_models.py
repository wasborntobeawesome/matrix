"""One-time setup for the local models: clone the Chatterbox toolkit, create the
reference voices and pre-download all weights (~15 GB) so the first request is fast.

    python scripts/setup_models.py            # everything
    python scripts/setup_models.py --no-download
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLKIT = ROOT / ".models" / "chatterbox-finetuning"
TOOLKIT_REPO = "https://github.com/gokhaneraslan/chatterbox-finetuning.git"
TOOLKIT_COMMIT = "2a6aba3"  # version the Chatterbox engine was tested with

WEIGHTS = {
    "k2-fsa/OmniVoice": None,
    "mrmuminov/f5-tts-uzbek": ["model.safetensors", "vocab.txt"],
    "Abduqayum/uzbek-tts-natural-speech-chatterbox": ["t3_finetuned_merged.safetensors", "tokenizer.json", "reference_voice.wav"],
    "ResembleAI/chatterbox": ["ve.safetensors", "s3gen.safetensors", "conds.pt",  # base of the Uzbek fine-tune
                              "ve.pt", "s3gen.pt", "t3_mtl23ls_v3.safetensors",  # Chatterbox Multilingual
                              "grapheme_mtl_merged_expanded_v1.json", "Cangjie5_TC.json"],
    "Anadilorg/Anadil_Uzbek_TTS": ["*.json", "*.safetensors"],
    "openbmb/VoxCPM2": None,
    "facebook/mms-tts-uzb-script_cyrillic": ["*.json", "model.safetensors"],
    "MuzaffarSharofitdinov/mms-tts-uzbek-girl-voice_cyrillic": None,
}


def main():
    if not TOOLKIT.exists():
        TOOLKIT.parent.mkdir(exist_ok=True)
        subprocess.run(["git", "clone", TOOLKIT_REPO, str(TOOLKIT)], check=True)
        subprocess.run(["git", "-C", str(TOOLKIT), "checkout", "-q", TOOLKIT_COMMIT], check=True)
    if not any((ROOT / "voices").glob("*.wav")):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_reference_voices.py")], check=True)
    if "--no-download" in sys.argv:
        return
    from huggingface_hub import snapshot_download

    for repo, patterns in WEIGHTS.items():
        print(f"Downloading {repo} ...")
        snapshot_download(repo, allow_patterns=patterns)
    print("Done.")


if __name__ == "__main__":
    main()
