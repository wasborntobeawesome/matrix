"""A tiny stand-in for the Ovoz TTS app (tests and UI demos): /api/models, /api/tts, /api/unload.

/api/tts answers a short tone WAV, except for texts matching REFUSE: those get Ovoz's real answer to Microsoft's
"NoAudioReceived" — 422 "Ovoz yaratib bo'lmadi. Matnni tekshiring." — every time (a sentence Edge refuses),
unless the voice is in ACCEPT_VOICES (another voice reads it, as Dmitry did in the QA run).

    python fake_ovoz.py 8019 "арабском"        # serve on :8019, refuse lines containing "арабском"
"""
from __future__ import annotations

import io
import json
import math
import re
import struct
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = [
    {"id": "edge", "name": "Microsoft Neural (online)", "kind": "online", "languages": ["uz", "ru", "en", "ar"],
     "available": True, "loaded": True, "supports_rate": True,
     "voices": [{"id": "uz-UZ-MadinaNeural", "name": "Madina", "lang": "uz"},
                {"id": "uz-UZ-SardorNeural", "name": "Sardor", "lang": "uz"},
                {"id": "ru-RU-SvetlanaNeural", "name": "Svetlana", "lang": "ru"},
                {"id": "ru-RU-DmitryNeural", "name": "Dmitriy", "lang": "ru"},
                {"id": "en-US-AvaNeural", "name": "Ava", "lang": "en"},
                {"id": "en-US-AndrewNeural", "name": "Andrew", "lang": "en"},
                {"id": "ar-SA-ZariyahNeural", "name": "Zariyah", "lang": "ar"}]},
    {"id": "navoiy", "name": "Navoiy (local)", "kind": "local", "languages": ["uz"], "available": True,
     "loaded": False, "voices": [{"id": "madina", "name": "Madina", "lang": "uz"}]},
]


def tone(seconds: float, sr: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        n = int(seconds * sr)
        w.writeframes(b"".join(struct.pack("<h", int(9000 * math.sin(2 * math.pi * 220 * i / sr)
                                                     * (1 if (i // 2400) % 3 else 0.2))) for i in range(n)))
    return buf.getvalue()


class State:
    refuse: re.Pattern | None = None
    accept_voices: set = {"ru-RU-DmitryNeural"}
    calls: list = []
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/models"):
            return self._send(200, json.dumps(MODELS).encode())
        return self._send(404, b'{"detail": "not found"}')

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if self.path.startswith("/api/unload"):
            return self._send(200, b'{"unloaded": []}')
        if not self.path.startswith("/api/tts"):
            return self._send(404, b'{"detail": "not found"}')
        text = data.get("text") or ""
        with State.lock:
            State.calls.append(data)
        if State.refuse and State.refuse.search(text) and data.get("voice") not in State.accept_voices:
            return self._send(422, json.dumps({"detail": "Ovoz yaratib bo'lmadi. Matnni tekshiring."}).encode())
        return self._send(200, tone(min(4.0, 0.6 + len(text) / 16)), "audio/wav")


def serve(port: int = 0, refuse: str | None = None) -> ThreadingHTTPServer:
    State.refuse = re.compile(refuse, re.I) if refuse else None
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8019
    s = serve(port, sys.argv[2] if len(sys.argv) > 2 else None)
    print(f"fake Ovoz on http://127.0.0.1:{port}", flush=True)
    threading.Event().wait()
