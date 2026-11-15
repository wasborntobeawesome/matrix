"""Test setup: an isolated data folder (projects, library, inbox, templates, workflows) in a temp dir, set up
before any studio module is imported, so the suite never touches the user's projects, job state, warm workers
or GPU. Ovoz is pointed at a closed port; nothing here needs the network or a GPU.

    video/.venv/Scripts/python -m pytest video/studio/tests            # fast suite (< 60 s)
    video/.venv/Scripts/python -m pytest video/studio/tests -m slow    # + a real 2 s render (Chromium + ffmpeg)
"""
from __future__ import annotations

import atexit
import io
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import wave
import zipfile
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parent.parent
VIDEO = STUDIO.parent
TMP = Path(tempfile.mkdtemp(prefix="studio-tests-"))
atexit.register(shutil.rmtree, TMP, True)

(TMP / "data" / "library").mkdir(parents=True)
(TMP / "data" / "library" / ".imported").write_text("{}")      # skip the first-start import of the AI clips
shutil.copytree(VIDEO / "templates", TMP / "templates", ignore=shutil.ignore_patterns("__pycache__"))
shutil.copytree(VIDEO / "comfy-workflows", TMP / "workflows")
os.environ.update({
    "STUDIO_DATA_DIR": str(TMP / "data"),
    "STUDIO_TEMPLATES_DIR": str(TMP / "templates"),
    "STUDIO_WORKFLOWS_DIR": str(TMP / "workflows"),
    "STUDIO_KEEP_LEFTOVERS": "1",              # never touch warm workers of the studio the user is running
    "OVOZ_URL": "http://127.0.0.1:9",          # closed port: Ovoz is "down"
    "STUDIO_WHISPER_PROMPT": "1",
})
sys.path.insert(0, str(STUDIO))

FFMPEG = str(VIDEO / "bin" / "ffmpeg.exe")
EXAMPLE = STUDIO / "example"
BASE = "http://127.0.0.1:8001"


# ---------------------------------------------------------------- helpers
def png_bytes(w: int = 1170, h: int = 2532, color=(40, 80, 200)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def tone_wav(path: Path, seconds: float = 3.0, freqs=(220.0,), sr: int = 48000, amp: float = 0.3,
             gate: tuple[float, float] | None = None, channels: int = 1) -> Path:
    """A simple test signal: sum of sines; `gate` = (on, off) seconds pattern (speech-like bursts)."""
    n = int(seconds * sr)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs) * amp
        if gate and (t % (gate[0] + gate[1])) >= gate[0]:
            v = 0.0
        s = struct.pack("<h", int(max(-1, min(1, v)) * 32767))
        frames += s * channels
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return path


def make_zip(files: dict[str, bytes | str], symlinks: dict[str, str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
        for name, target in (symlinks or {}).items():
            zi = zipfile.ZipInfo(name)
            zi.external_attr = (0o120777 << 16)
            z.writestr(zi, target)
    return buf.getvalue()


def manifest(tid: str = "my-look", kind: str = "full", **kw) -> str:
    m = {"id": tid, "name": "My look", "kind": kind, "description": "test", "author": "tests", "version": "1.0",
         "licence": "CC0", "tokens": {"colors": {"primary": "#112233"}}, **kw}
    return json.dumps(m)


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return TMP


@pytest.fixture(scope="session")
def server():
    import server as srv
    return srv


@pytest.fixture(scope="session")
def client(server):
    from fastapi.testclient import TestClient
    return TestClient(server.app, base_url=BASE)


@pytest.fixture(scope="session")
def example_project(client):
    """One project made from video/studio/example (shared: creating it transcodes a recording)."""
    r = client.post("/api/projects/example")
    assert r.status_code == 201, r.text
    return r.json()
