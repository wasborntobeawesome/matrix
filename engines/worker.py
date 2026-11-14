"""Base class for models that run in their own virtualenv as a worker process.

Some models pin library versions that conflict with the main app (CosyVoice needs
transformers 4.51, qwen-tts needs 4.57, OmniVoice needs 5.x). Each of those gets a
venv under .models/ and a small worker script that loads the model once, then answers
JSON requests line by line over stdin/stdout. The worker writes audio to a temp file.
"""
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

from .base import Engine

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / ".models"


def venv_python(name: str) -> Path:
    return MODELS / name / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class WorkerEngine(Engine):
    venv: str = ""                 # folder name under .models/
    worker: str = ""               # script name in engines/
    worker_args: list[str] = []
    setup_hint: str = ""
    out_of_process = True
    load_timeout = 900
    request_timeout = 600

    def missing_dependency(self):
        if not venv_python(self.venv).exists():
            return f"Model muhiti yo'q ({self.setup_hint})"
        return None

    def load(self):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self._log = open(MODELS / f"{self.id}-worker.log", "w", encoding="utf-8")
        proc = subprocess.Popen(
            [str(venv_python(self.venv)), str(Path(__file__).with_name(self.worker)), *self.worker_args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log,
            text=True, encoding="utf-8", env=env, cwd=str(ROOT),
        )
        msg = self._read(proc, self.load_timeout)
        if not msg.get("ready"):
            proc.kill()
            raise RuntimeError(f"{self.name} ishga tushmadi: {msg.get('error')} (.models/{self.id}-worker.log)")
        self.worker_info = msg
        self.model = proc

    def _read(self, proc, timeout) -> dict:
        box = {}
        reader = threading.Thread(target=lambda: box.update(line=proc.stdout.readline()), daemon=True)
        reader.start()
        reader.join(timeout)
        if not box.get("line"):
            proc.kill()
            raise RuntimeError(f"{self.name} javob bermadi (.models/{self.id}-worker.log)")
        return json.loads(box["line"])

    def unload(self):
        if self.model is not None:
            self.model.kill()
            self.model.wait()
        self.model = None

    def request(self, **payload) -> tuple[np.ndarray, int]:
        """Send one synthesis request; returns (audio, sample_rate)."""
        import soundfile as sf

        if self.model is None or self.model.poll() is not None:  # worker died: restart
            self.load()
        fd, out = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            self.model.stdin.write(json.dumps({**payload, "out": out}, ensure_ascii=False) + "\n")
            self.model.stdin.flush()
            msg = self._read(self.model, self.request_timeout)
            if not msg.get("ok"):
                raise RuntimeError(msg.get("error"))
            audio, sr = sf.read(out, dtype="float32")
        finally:
            os.unlink(out)
        return np.asarray(audio).reshape(-1), sr
