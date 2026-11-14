"""Set up Aisha's Navoiy TTS: clones CosyVoice (pinned revision), creates a separate
virtualenv in .models/navoiy-venv (CosyVoice needs older libraries than the main app)
and downloads the weights (~4 GB).

    python scripts/setup_navoiy.py
Requires Python 3.12 (`py -3.12`) and an NVIDIA GPU.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / ".models"
COSYVOICE = MODELS / "CosyVoice"
COSYVOICE_REV = "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc"  # revision Navoiy was released with
VENV = MODELS / "navoiy-venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

TORCH = ["torch==2.8.0", "torchaudio==2.8.0", "--index-url", "https://download.pytorch.org/whl/cu128"]
PACKAGES = [
    "numpy<2", "transformers==4.51.3", "conformer==0.3.2", "diffusers==0.29.0", "hydra-core==1.3.2",
    "HyperPyYAML==1.2.3", "inflect", "librosa", "omegaconf", "onnxruntime", "openai-whisper",
    "x-transformers", "wetext", "soundfile", "pyarrow==18.1.0", "matplotlib", "lightning", "rich",
    "gdown", "wget", "pyworld", "einops", "regex", "tiktoken", "scipy", "pyyaml",
    "modelscope", "huggingface_hub",
]


def run(*cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(list(map(str, cmd)), check=True)


def main():
    MODELS.mkdir(exist_ok=True)
    if not COSYVOICE.exists():
        run("git", "clone", "https://github.com/FunAudioLLM/CosyVoice.git", COSYVOICE)
        run("git", "-C", COSYVOICE, "checkout", "-q", COSYVOICE_REV)
        run("git", "-C", COSYVOICE, "submodule", "update", "--init", "--recursive")
    if not PY.exists():
        launcher = ["py", "-3.12"] if os.name == "nt" else [sys.executable]
        run(*launcher, "-m", "venv", VENV)
        run(PY, "-m", "pip", "install", "--upgrade", "pip")
        run(PY, "-m", "pip", "install", *TORCH)
        run(PY, "-m", "pip", "install", *PACKAGES)
    run(PY, "-c", "from huggingface_hub import snapshot_download as d; "
        "d('aisha-org/navoiy-tts'); d('FunAudioLLM/CosyVoice2-0.5B'); print('weights ready')")


if __name__ == "__main__":
    main()
