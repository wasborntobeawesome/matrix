"""Word timing for voice-over clips: faster-whisper (CPU, int8) + alignment onto the known script.

The TTS gives no word timestamps, so each scene's clip is transcribed with word timestamps and
the recognised words are mapped onto the script's words with a character-level sequence
alignment (difflib). ASR spelling differs from the script (Uzbek apostrophes, Cyrillic output,
Arabic diacritics, numbers), so both sides are normalised first, and words the recogniser
missed are interpolated between their matched neighbours (by character weight).

    words, method = timing(wav, text, lang, product)   # method: "aligned" | "estimated"

Results are cached per audio hash (+ script + model) in library/align/<key>.json, so every
project and every render reuses them. When the model is missing, faster-whisper isn't
installed, or the alignment looks wrong (too few matched words), the old estimate is used.

Model: STUDIO_WHISPER_MODEL (default "small"), see README "Caption and word timing" for the
speed/accuracy measurements on this PC. Models live in the Hugging Face cache; the studio never
downloads one by itself (local_files_only) — run
    video/.venv/Scripts/python -c "from faster_whisper import download_model; download_model('small')"
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

import paths

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
sys.path.insert(0, str(VIDEO / "shared"))
import voicelib as V  # noqa: E402

CACHE = paths.LIB / "align"
MODEL = os.environ.get("STUDIO_WHISPER_MODEL", "small")
THREADS = int(os.environ.get("STUDIO_WHISPER_THREADS", "8"))
VERSION = 3          # bump when the alignment algorithm changes (invalidates the cache)
PROMPT = os.environ.get("STUDIO_WHISPER_PROMPT", "1") == "1"   # give the script to Whisper as context
MIN_MATCHED = 0.5    # fraction of script words that must be matched, else "estimated"
RETRY_BELOW = 0.85   # below this the clip is transcribed again without the prompt (keeps the better one)

_model = None
_model_err: str | None = None
_lock = threading.Lock()

# ---------------------------------------------------------------- normalisation
_CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
                ["a", "b", "v", "g", "d", "e", "yo", "j", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                 "u", "f", "x", "ts", "ch", "sh", "sh", "", "i", "", "e", "yu", "ya", "o", "q", "g", "h"]))
_AR_DIAC = re.compile("[ً-ْٰـۖ-ۭ]")
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ء": ""})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def norm(w: str, lang: str) -> str:
    """Comparable form of a word: lower case, no apostrophes/punctuation/diacritics."""
    w = unicodedata.normalize("NFKC", w).lower().translate(_DIGITS)
    w = re.sub(r"['‘’`ʻʼ]", "", w)
    if lang == "ar":
        w = _AR_DIAC.sub("", w).translate(_AR_MAP)
    elif lang == "ru":
        w = w.replace("ё", "е")
    elif lang == "uz" and re.search("[а-яёўқғҳ]", w):
        w = "".join(_CYR.get(c, c) for c in w)       # Whisper sometimes answers in Cyrillic Uzbek
    return "".join(c for c in w if c.isalnum())


# ---------------------------------------------------------------- model
def model_name() -> str:
    return MODEL


def load_model():
    """The WhisperModel (CPU int8), or None when it can't be loaded (reason in _model_err)."""
    global _model, _model_err
    with _lock:
        if _model is not None or _model_err:
            return _model
        try:
            from faster_whisper import WhisperModel
            t0 = time.time()
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS,
                                  local_files_only=True)
            print(f"[align] faster-whisper '{MODEL}' loaded in {time.time() - t0:.1f}s", flush=True)
        except Exception as e:  # missing package / model: fall back to estimates
            _model_err = f"{type(e).__name__}: {e}"
            print(f"[align] whisper unavailable ({_model_err}) — using estimated timing", flush=True)
        return _model


def status() -> dict:
    return {"model": MODEL, "loaded": _model is not None, "error": _model_err, "threads": THREADS}


def transcribe(wav: Path, lang: str, prompt: str | None = None) -> list[dict] | None:
    m = load_model()
    if m is None:
        return None
    import numpy as np
    import subprocess
    # decode with our ffmpeg (16 kHz mono float), not PyAV
    raw = subprocess.run([V.FFMPEG, "-v", "error", "-i", str(wav), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    segs, _info = m.transcribe(audio, language=lang, word_timestamps=True, beam_size=5, vad_filter=False,
                               condition_on_previous_text=False, temperature=0.0,
                               initial_prompt=prompt or None)
    out = []
    for s in segs:
        for w in s.words or []:
            out.append({"w": w.word.strip(), "start": float(w.start), "end": float(w.end), "p": float(w.probability)})
    return out


# ---------------------------------------------------------------- alignment
def align_words(script: list[str], asr: list[dict], lang: str, duration: float) -> tuple[list[dict], float]:
    """Map ASR word times onto the script words. Returns ([{w, start, end, m}], matched fraction)."""
    s_chars, s_owner, s_pos = [], [], []
    for i, w in enumerate(script):
        n = norm(w, lang)
        for k, c in enumerate(n):
            s_chars.append(c); s_owner.append(i); s_pos.append(k)
    a_chars, a_time = [], []
    for w in asr:
        n = norm(w["w"], lang)
        if not n:
            continue
        d = max(0.01, w["end"] - w["start"]) / len(n)
        for k, c in enumerate(n):
            a_chars.append(c)
            a_time.append((w["start"] + k * d, w["start"] + (k + 1) * d))
    nw = len(script)
    hits: list[list[tuple[int, int]]] = [[] for _ in range(nw)]   # per script word: (script char pos, asr char idx)
    if s_chars and a_chars:
        sm = difflib.SequenceMatcher(None, s_chars, a_chars, autojunk=False)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                si, ai = blk.a + k, blk.b + k
                hits[s_owner[si]].append((s_pos[si], ai))
    out: list[dict] = []
    matched = 0
    for i, w in enumerate(script):
        L = max(1, len(norm(w, lang)))
        h = hits[i]
        ok = len(h) >= max(1, min(2, L)) and len(h) / L >= 0.4
        if ok:
            matched += 1
            (p0, a0), (p1, a1) = h[0], h[-1]
            cd = sum(a_time[a][1] - a_time[a][0] for _, a in h) / len(h)
            st = a_time[a0][0] - p0 * cd
            en = a_time[a1][1] + (L - 1 - p1) * cd
            out.append({"w": w, "start": st, "end": max(en, st + 0.05), "m": True})
        else:
            out.append({"w": w, "start": None, "end": None, "m": False, "L": L})
    # monotonic: a matched word never starts before the previous matched one
    prev = None
    for o in out:
        if o["m"]:
            if prev is not None and o["start"] < prev["start"] + 0.02:
                o["start"] = prev["start"] + 0.02
                o["end"] = max(o["end"], o["start"] + 0.05)
            prev = o
    # interpolate unmatched runs between neighbours by character weight
    i = 0
    while i < nw:
        if out[i]["m"]:
            i += 1
            continue
        j = i
        while j < nw and not out[j]["m"]:
            j += 1
        a = out[i - 1]["end"] if i > 0 else 0.0
        b = out[j]["start"] if j < nw else duration
        if i > 0 and j < nw and b - a < 0.05 * (j - i):      # no room: share the neighbours' span
            a = out[i - 1]["start"] + 0.6 * (out[i - 1]["end"] - out[i - 1]["start"])
        tot = sum(out[k]["L"] for k in range(i, j))
        t = a
        for k in range(i, j):
            dt = max(0.0, b - a) * out[k]["L"] / tot
            out[k].update(start=t, end=t + dt)
            out[k].pop("L", None)
            t += dt
        i = j
    for o in out:
        o["start"] = round(min(max(0.0, o["start"]), duration), 3)
        o["end"] = round(min(max(o["start"], o["end"]), duration), 3)
    return out, (matched / nw if nw else 0.0)


def remap_words(spoken: list[dict], caption: list[str], lang: str) -> list[dict]:
    """Word times of the SPOKEN text (`say_tts` / pronounced form) -> the caption words (`say`).

    Words that are the same on both sides keep their own times; a differing run ("Ovoz" captioned,
    "Овоз" spoken; "AI" vs "эй-ай") takes the time span of the spoken run, shared by character weight; caption
    words with no spoken counterpart get a zero-length slot at the boundary, so captions stay in order."""
    if not caption:
        return []
    if not spoken:
        return [{"w": w, "start": 0.0, "end": 0.0} for w in caption]
    a = [norm(w["w"], lang) for w in spoken]
    b = [norm(w, lang) for w in caption]
    out: list[dict] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            for k in range(j2 - j1):
                w = spoken[i1 + k]
                out.append({"w": caption[j1 + k], "start": w["start"], "end": w["end"]})
        elif op in ("replace", "insert"):
            if i2 > i1:
                t0, t1 = spoken[i1]["start"], spoken[i2 - 1]["end"]
            else:      # caption words that are not spoken: a zero-length slot after the previous word
                t0 = t1 = out[-1]["end"] if out else spoken[0]["start"]
            ws = [max(1, len(b[j])) for j in range(j1, j2)]
            t = t0
            for j, wt in zip(range(j1, j2), ws):
                dt = (t1 - t0) * wt / sum(ws)
                out.append({"w": caption[j], "start": round(t, 3), "end": round(t + dt, 3)})
                t += dt
        # "delete": spoken words with no caption word; their time just passes
    return out


def _sha(f: Path) -> str:
    h = hashlib.sha1()
    with open(f, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_sha_memo: dict = {}


def cache_key(wav: Path, text: str, lang: str) -> str:
    st = wav.stat()
    k = (str(wav), st.st_mtime_ns, st.st_size)
    if k not in _sha_memo:
        _sha_memo[k] = _sha(wav)
    raw = json.dumps([_sha_memo[k], text, lang, MODEL, VERSION], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def cached(wav: Path, text: str, lang: str) -> dict | None:
    f = CACHE / f"{cache_key(wav, text, lang)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def compute(wav: Path, text: str, lang: str, product: str = "") -> dict:
    """Transcribe + align one clip and cache it. {method, words, matched, model, seconds, asr?}."""
    words_disp = V.display_text(text, lang, product).split()
    dur = V.duration(wav)
    t0 = time.time()
    res = {"method": "estimated", "model": MODEL, "matched": 0.0, "words": None, "duration": round(dur, 3)}
    # 1st pass with the script as context (better spelling -> more matches); Whisper sometimes
    # hallucinates on a prompt (e.g. Russian "subtitle credits"), so a weak result is retried without it
    best = None
    for prompt in ([text, None] if PROMPT else [None]):
        try:
            asr = transcribe(wav, lang, prompt)
        except Exception as e:
            res["error"] = f"{type(e).__name__}: {e}"
            break
        if asr is None:
            break
        words, frac = align_words(words_disp, asr, lang, dur)
        if best is None or frac > best[1]:
            best = (words, frac, asr, prompt is not None)
        if frac >= RETRY_BELOW:
            break
    if best is None:
        res["error"] = res.get("error") or _model_err
        return res          # not cached: the model may become available later
    words, frac, asr, prompted = best
    res.pop("error", None)
    res.update(matched=round(frac, 3), seconds=round(time.time() - t0, 2), prompted=prompted,
               asr=" ".join(w["w"] for w in asr))
    if frac >= MIN_MATCHED and words:
        res.update(method="aligned", words=[{"w": w["w"], "start": w["start"], "end": w["end"], "m": w["m"]} for w in words])
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{cache_key(wav, text, lang)}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
    return res


def timing(wav: Path, text: str, lang: str, product: str = "", run: bool = True) -> dict | None:
    """Cached result, or compute it now (run=True), or None (run=False and not cached yet)."""
    c = cached(wav, text, lang)
    if c is not None or not run:
        return c
    with _run_lock:
        c = cached(wav, text, lang)
        return c if c is not None else compute(wav, text, lang, product)


_run_lock = threading.Lock()

# ---------------------------------------------------------------- background worker (live preview)
_q: queue.Queue = queue.Queue()
_pending: set = set()


def _worker() -> None:
    while True:
        wav, text, lang, product = _q.get()
        try:
            timing(Path(wav), text, lang, product, run=True)
        except Exception as e:  # never kill the worker
            print("[align] failed:", e, flush=True)
        finally:
            _pending.discard((wav, text, lang))


threading.Thread(target=_worker, name="align", daemon=True).start()


def request(wav: Path, text: str, lang: str, product: str = "") -> None:
    """Queue an alignment in the background (used by the preview timeline)."""
    if _model_err:
        return
    k = (str(wav), text, lang)
    if k in _pending:
        return
    _pending.add(k)
    _q.put((str(wav), text, lang, product))
