"""Ovoz — a simple text-to-speech web app (Uzbek first, plus Russian, English and Arabic)."""
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import engines
from languages import LANGUAGES, prepare

LANG_PATTERN = "^(" + "|".join(LANGUAGES) + ")$"

MAX_CHARS = 5000
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Ovoz — Uzbek TTS")
# Allows the page to work when index.html is opened directly from disk (origin "null").
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    expose_headers=["X-Synthesis-Seconds", "X-Model"],
)


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


@app.get("/api/languages")
def languages():
    return [{"id": k, **v} for k, v in LANGUAGES.items()]


@app.get("/api/voices")
def voices():
    """Backwards-compatible: voices of the default online model."""
    return engines.get("edge").info()["voices"]


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
    if voice.lang not in (None, lang):
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
