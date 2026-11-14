"""Set up the Qwen3-TTS based models (Qwen3-TTS Uzbek, Sayro) in their own virtualenv
(.models/qwen-venv), because qwen-tts pins transformers 4.57.

Both repos are gated on Hugging Face: accept/request access on the model pages, then
log in first with `hf auth login`.

    python scripts/setup_qwen.py
"""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".models" / "qwen-venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REPOS = ["mrmuminov/Qwen3-TTS-12Hz-0.6B-Uzbek", "uzlm/sayro-tts-1.7B"]


def run(*cmd):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(list(map(str, cmd)), check=True)


def main():
    if not PY.exists():
        launcher = ["py", "-3.12"] if os.name == "nt" else ["python3.12"]
        run(*launcher, "-m", "venv", VENV)
        run(PY, "-m", "pip", "install", "--upgrade", "pip")
        run(PY, "-m", "pip", "install", "torch==2.8.0", "torchaudio==2.8.0",
            "--index-url", "https://download.pytorch.org/whl/cu128")
        run(PY, "-m", "pip", "install", "qwen-tts", "huggingface_hub")
    for repo in REPOS:
        try:
            run(PY, "-c", f"from huggingface_hub import snapshot_download as d; d('{repo}'); print('ok {repo}')")
        except subprocess.CalledProcessError:
            print(f"!! Could not download {repo}: request access on its Hugging Face page and run `hf auth login`.")


if __name__ == "__main__":
    main()
