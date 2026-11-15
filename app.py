"""Ovoz — a simple text-to-speech web app (Uzbek first, plus Russian, English and Arabic)."""
import time
from pathlib import Path

from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import engines
import voice_library
from engines.base import VoiceNotFound
from languages import LANGUAGES, prepare

LANG_PATTERN = "^(" + "|".join(LANGUAGES) + ")$"

MAX_CHARS = 5000
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Ovoz — Uzbek TTS")
# Allows the page to work when index.html is opened directly from disk (origin "null").
# This open policy is fine for synthesis; endpoints that change files on disk are
# additionally guarded by same_origin_only below.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Synthesis-Seconds", "X-Model"],
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def same_origin_only(request: Request):
    """Reject writes that come from another website open in the user's browser.

    Browsers send an Origin header on cross-site POST/DELETE; it must name this server.
    The Host must be a loopback name too, which blocks DNS-rebinding tricks. Requests
    without Origin (curl, scripts, tests) are local tools and are allowed.
    """
    host = urlsplit(f"//{request.headers.get('host', '')}").hostname or ""
    origin = request.headers.get("origin")
    if host not in LOCAL_HOSTS or (origin is not None and urlsplit(origin).netloc != request.headers.get("host")):
        raise HTTPException(403, "Ovozlarni faqat ilovaning oʻz sahifasidan (http://localhost:8000) qoʻshish yoki oʻchirish mumkin.")


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_CHARS)
    model: str = "edge"
    voice: str = "uz-UZ-MadinaNeural"
    rate: int = Field(0, ge=-50, le=100, description="Speed change in percent")
    pitch: int = Field(0, ge=-50, le=50, description="Pitch change in Hz (online model only)")
    style: str | None = Field(None, description="Speaking style / emotion, for models that support it")
    language: str = Field("uz", pattern=LANG_PATTERN, description="Content language: uz, ru, en or ar")


class PreviewRequest(BaseModel):
    text: str = Field(max_length=MAX_CHARS)
    model: str = "edge"
    language: str = Field("uz", pattern=LANG_PATTERN)


class VoiceUpload(BaseModel):
    name: str = Field(min_length=1, max_length=60, description="Label shown in the UI")
    language: str = Field(pattern=LANG_PATTERN, description="Language the speaker speaks in the clip")
    transcript: str = Field(min_length=1, max_length=600, description="Exact words spoken in the clip")
    consent: bool = Field(description="The speaker agreed to having their voice cloned")
    audio: str = Field(max_length=8_000_000, description="Base64-encoded WAV")


def prepare_text(text: str, engine, lang: str = "uz") -> tuple[str, bool]:
    return prepare(text, lang, engine.spell_numbers and lang not in engine.reads_digits)


def get_engine(model_id: str):
    engine = engines.get(model_id)
    if engine is None:
        raise HTTPException(400, f"Noma'lum model: {model_id}")
    return engine


@app.get("/api/models")
def models():
    return [e.info() for e in engines.ENGINES.values()]


@app.post("/api/unload", dependencies=[Depends(same_origin_only)])
async def unload():
    """Free GPU memory: unload all local models. Used by Video Studio between the
    voice-over and video steps so each gets the whole GPU."""
    return {"unloaded": await run_in_threadpool(engines.unload_all)}


@app.get("/api/languages")
def languages():
    return [{"id": k, **v} for k, v in LANGUAGES.items()]


@app.get("/api/voices")
def voices():
    """Backwards-compatible: voices of the default online model."""
    return engines.get("edge").info()["voices"]


@app.post("/api/voices", dependencies=[Depends(same_origin_only)])
def add_voice(req: VoiceUpload):
    """Save a native speaker's reference clip for the voice-cloning models."""
    import base64
    import binascii

    if not req.consent:
        raise HTTPException(400, "Ovoz egasining roziligi kerak.")
    try:
        wav = base64.b64decode(req.audio, validate=True)
    except binascii.Error:
        raise HTTPException(400, "Audio noto'g'ri formatda.")
    try:
        voice_id = voice_library.save_voice(req.name, req.language, req.transcript, wav)
    except voice_library.VoiceError as exc:
        raise HTTPException(400, str(exc))
    return {"id": voice_id, "language": req.language}


@app.delete("/api/voices/{lang}/{slug}", dependencies=[Depends(same_origin_only)])
def remove_voice(lang: str, slug: str):
    try:
        voice_library.delete_voice(f"{lang}/{slug}")
    except voice_library.VoiceError as exc:
        raise HTTPException(400, str(exc))
    return {"deleted": f"{lang}/{slug}"}


@app.post("/api/normalize")
def preview(req: PreviewRequest):
    text, converted = prepare_text(req.text, get_engine(req.model), req.language)
    return {"text": text, "transliterated": converted}


@app.post("/api/tts")
async def tts(req: TTSRequest):
    engine = get_engine(req.model)
    if reason := engine.missing_dependency():
        raise HTTPException(503, f"{engine.name} mavjud emas: {reason}")
    lang = req.language
    if lang not in engine.languages:
        raise HTTPException(400, f"{engine.name} bu tilni qo'llab-quvvatlamaydi ({LANGUAGES[lang]['name']})")
    voice = next((v for v in engine.voices() if v.id == req.voice), None)
    if voice is None:
        raise HTTPException(400, "Noma'lum ovoz")
    # A built-in voice only speaks its own language. A cloned voice can speak any language
    # the model supports, just with the reference speaker's accent.
    if not voice.cross_lingual and voice.lang not in (None, lang):
        raise HTTPException(400, f"{voice.name} ovozi bu tilda gapirmaydi ({LANGUAGES[lang]['name']})")
    text, _ = prepare_text(req.text, engine, lang)
    if not any(c.isalnum() for c in text):
        raise HTTPException(400, "Matn bo'sh")

    started = time.perf_counter()
    try:
        if engine.kind == "online":
            audio, media_type = await engine.synthesize_async(text, req.voice, req.rate, req.pitch)
        else:
            audio, media_type = await run_in_threadpool(engine.synthesize, text, req.voice, req.rate, 0, req.style, lang)
    except (FileNotFoundError, VoiceNotFound):  # voice deleted while the request was running
        raise HTTPException(410, "Bu ovoz namunasi oʻchirilgan. Boshqa ovozni tanlang.")
    except Exception as exc:  # network, model download or GPU errors
        if type(exc).__name__ == "NoAudioReceived":
            raise HTTPException(422, "Ovoz yaratib bo'lmadi. Matnni tekshiring.")
        raise HTTPException(502, f"{engine.name}: ovoz yaratishda xatolik — {exc}")
    if not audio:
        raise HTTPException(422, "Ovoz yaratib bo'lmadi. Matnni tekshiring.")

    ext = "mp3" if media_type == "audio/mpeg" else "wav"
    return Response(
        audio,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="ovoz-{engine.id}-{lang}.{ext}"',
            "X-Synthesis-Seconds": f"{time.perf_counter() - started:.2f}",
            "X-Model": engine.id,
        },
    )


# Serve the frontend last so it doesn't shadow the /api routes.
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
