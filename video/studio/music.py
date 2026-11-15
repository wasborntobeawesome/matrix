"""Background music: a shared library (video/library/music/) and the final voice + music mix.

    video/library/music/<id>/  track.(mp3|wav|ogg|m4a)  + meta.json
        meta = {id, title, artist, licence, source: "upload"|"generated"|"builtin", duration,
                mood?, bpm?, prompt?, created, lufs, ext}

`add_track(file, meta)` is the one entry point for new tracks (uploads now, an AI music
generator later). Three small builtin tracks are synthesised with numpy on first start
(CC0, generated locally), so music works out of the box.

The mix (`mix()`): the voice-over is loudness-matched to -20 LUFS and the track to -12 LUFS
(8 LU apart, like raw speech vs. a mastered song); then `volume_db` applies to the music,
`duck_db` more while the voice speaks (an envelope from the voice-over's own loudness,
with a short look-ahead so the music is already down when a word starts), fades in/out,
a loop with an equal-power cross-fade when the track is shorter than the video, and finally
EBU R128 loudness normalisation of the whole mix to -14 LUFS / -1.5 dBTP (two-pass loudnorm).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import paths

import numpy as np

from store import FFMPEG, StoreError, now, probe

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
MUSIC = paths.LIB / "music"
EXTS = (".mp3", ".wav", ".ogg", ".m4a")
MAX_BYTES = 60 * 1024 * 1024
MAX_SECONDS = 20 * 60
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SR = 48000
VOICE_REF = -20.0      # LUFS the voice is matched to before mixing
MUSIC_REF = -12.0      # LUFS the track is matched to before volume_db
TARGET = -14.0         # final mix, integrated LUFS
TRUE_PEAK = -1.5
MIX_VERSION = 4          # 4: ducking look-ahead / hold no longer swapped
DUCK_ATTACK = 0.08     # s to go down
DUCK_RELEASE = 0.45    # s to come back up
DUCK_LOOKAHEAD = 0.15  # s before a word starts
DUCK_HOLD = 0.30       # s after speech before releasing
# Tone shaping of generated tracks (ACE-Step output is bass-heavy): a gentle high-pass under the audible bass,
# a mild low-shelf cut, then make-up gain back to the track's own loudness. The original is kept.
TONE = {"highpass_hz": 38.0, "shelf_hz": 120.0, "shelf_db": -3.0, "shelf_q": 0.7}
TONE_VERSION = 1
_lock = threading.RLock()


# ---------------------------------------------------------------- helpers
def _check(i: str) -> str:
    if not ID_RE.match(i or ""):
        raise StoreError("bad music id", 404)
    return i


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(p: Path, d: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def decode(path: Path, channels: int = 2) -> np.ndarray:
    """Any audio -> float32 [n, channels] at 48 kHz."""
    raw = subprocess.run([FFMPEG, "-v", "error", "-i", str(path), "-ac", str(channels), "-ar", str(SR), "-f", "f32le", "-"],
                         check=True, capture_output=True, creationflags=NOWIN).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels).copy()


def loudness(path: Path) -> float | None:
    """Integrated loudness (LUFS) with ffmpeg's EBU R128 scanner. None for silence."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", r.stderr)
    if not m or m[-1] == "-inf":
        return None
    v = float(m[-1])
    return v if v > -70 else None


def tone_filter(t: dict | None = None) -> str:
    t = {**TONE, **(t or {})}
    return (f"highpass=f={t['highpass_hz']}:poles=2,"
            f"lowshelf=f={t['shelf_hz']}:g={t['shelf_db']}:t=q:w={t['shelf_q']}")


def band_rms_db(path: Path, lo: float | None, hi: float | None) -> float | None:
    """RMS level (dBFS, ffmpeg astats) of one frequency band: e.g. (None, 60) = sub-bass, (60, 250) = bass."""
    af = ",".join(x for x in (f"highpass=f={lo}:poles=2" if lo else "", f"lowpass=f={hi}:poles=2" if hi else "",
                              "astats=metadata=0:measure_perchannel=none:measure_overall=RMS_level") if x)
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-ac", "1", "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    m = re.findall(r"RMS level dB:\s+(-?[\d.]+|-inf)", r.stderr)
    return None if not m or m[-1] == "-inf" else round(float(m[-1]), 2)


def shape_generated(src: Path, dest: Path, lufs_in: float | None = None) -> dict:
    """Tone shaping for a generated track: high-pass ~38 Hz + a mild low-shelf cut, then make-up gain so the
    integrated loudness stays at the original's (a limiter at -1 dBFS catches the few peaks the gain raises).
    Writes `dest` (same container as `src`); returns the measurements."""
    src, dest = Path(src), Path(dest)
    lufs_in = lufs_in if lufs_in is not None else loudness(src)
    filt = tone_filter()
    # pass 1: loudness after the EQ alone
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(src), "-af", filt + ",ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", r.stderr)
    eq_lufs = float(m[-1]) if m and m[-1] != "-inf" else None
    gain = round(max(-6.0, min(6.0, (lufs_in - eq_lufs) if (lufs_in is not None and eq_lufs is not None) else 0.0)), 2)
    codec = {".ogg": ["-c:a", "libvorbis", "-q:a", "6"], ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
             ".m4a": ["-c:a", "aac", "-b:a", "192k"], ".wav": ["-c:a", "pcm_s16le"]}.get(src.suffix.lower(),
                                                                                      ["-c:a", "libvorbis", "-q:a", "6"])
    tmp = dest.with_name(dest.stem + ".shaping" + dest.suffix)
    r2 = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-af",
                         f"{filt},volume={gain}dB,alimiter=limit=0.891:level=0:latency=1", "-ar", str(SR),
                         *codec, str(tmp)], capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    if r2.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("tone shaping failed: " + r2.stderr.strip()[-300:])
    tmp.replace(dest)
    return {**TONE, "version": TONE_VERSION, "makeup_gain_db": gain, "lufs_before": lufs_in,
            "lufs_after": loudness(dest),
            "sub_db_before": band_rms_db(src, None, 60), "sub_db_after": band_rms_db(dest, None, 60),
            "bass_db_before": band_rms_db(src, 60, 250), "bass_db_after": band_rms_db(dest, 60, 250)}


def write_wav(path: Path, x: np.ndarray) -> None:
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1] if x.ndim == 2 else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- library
def list_tracks() -> list[dict]:
    out = []
    if MUSIC.exists():
        for d in MUSIC.iterdir():
            m = _read(d / "meta.json")
            if m and (d / f"track{m.get('ext', '')}").exists():
                out.append(m)
    order = {"builtin": 0, "generated": 1, "upload": 2}
    return sorted(out, key=lambda m: (order.get(m.get("source"), 3), -(m.get("created") or 0)))


def get(tid: str | None) -> dict | None:
    if not tid or not ID_RE.match(tid):
        return None
    m = _read(MUSIC / tid / "meta.json")
    return m if m and (MUSIC / tid / f"track{m.get('ext', '')}").exists() else None


def track_file(tid: str) -> Path:
    m = get(_check(tid))
    if not m:
        raise StoreError("track not found", 404)
    return MUSIC / tid / f"track{m['ext']}"


def add_track(src: Path, meta: dict, move: bool = False) -> dict:
    """Add an audio file to the library. meta: title, artist, licence, source, mood?, bpm?, prompt?, id?.

    Checks that ffmpeg can decode it, measures duration + loudness. Returns the stored meta.
    A generated track (source "generated") is tone-shaped (shape_generated: less sub-bass / boom, same
    loudness); the untouched original is kept next to it as original.<ext> (meta "tone" has the numbers)."""
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in EXTS:
        raise StoreError(f"unsupported audio type {ext or '?'} (mp3, wav, ogg, m4a)")
    if src.stat().st_size > MAX_BYTES:
        raise StoreError(f"audio file too large (limit {MAX_BYTES // 2**20} MB)", 413)
    info = probe(src)
    dur = info.get("duration") or 0
    if dur <= 0.5:
        raise StoreError("not a readable audio file")
    if dur > MAX_SECONDS:
        raise StoreError(f"track too long ({dur / 60:.0f} min; limit {MAX_SECONDS // 60} min)")
    if not str(meta.get("licence") or "").strip():
        raise StoreError("licence is required (e.g. 'CC BY 4.0, Artist', 'own recording')", 422)
    lufs = loudness(src)
    if lufs is None:
        raise StoreError("the track is silent")
    tid = meta.get("id") or ("m-" + hashlib.sha1(src.read_bytes()).hexdigest()[:10])
    _check(tid)
    d = MUSIC / tid
    with _lock:
        d.mkdir(parents=True, exist_ok=True)
        for old in [*d.glob("track.*"), *d.glob("original.*")]:
            old.unlink()
        tone = None
        if meta.get("source") == "generated" and meta.get("tone") is not False:
            (shutil.move if move else shutil.copy2)(str(src), d / f"original{ext}")
            try:
                tone = shape_generated(d / f"original{ext}", d / f"track{ext}", lufs)
                tone["original"] = f"original{ext}"
                lufs = tone["lufs_after"] if tone.get("lufs_after") is not None else lufs
            except (RuntimeError, OSError) as e:     # never lose a generated track over the EQ
                shutil.copy2(d / f"original{ext}", d / f"track{ext}")
                tone = {"error": str(e)[:300]}
        else:
            (shutil.move if move else shutil.copy2)(str(src), d / f"track{ext}")
        m = {"id": tid, "title": str(meta.get("title") or src.stem)[:80], "artist": str(meta.get("artist") or "")[:80],
             "licence": str(meta["licence"]).strip()[:160], "source": meta.get("source") or "upload",
             "duration": round(dur, 2), "created": meta.get("created") or now(), "lufs": round(lufs, 1), "ext": ext}
        for k in ("mood", "bpm", "prompt", "original_name", "genre", "seed", "model", "gen_seconds", "check", "key"):
            if meta.get(k) not in (None, ""):
                m[k] = meta[k]
        if tone:
            m["tone"] = tone
        _write(d / "meta.json", m)
    return m


GENERATED_LICENCE = ("Generated locally with ACE-Step v1.5 (MIT); the model card allows commercial use of the "
                     "music. AI-generated.")


def generated_for(spec: dict) -> dict | None:
    """The library track generated for a project's `music.generate` spec (same prompt/genre/mood/bpm/duration/seed)."""
    k = generate_key(spec)
    for m in list_tracks():
        if m.get("source") == "generated" and m.get("key") == k:
            return m
    return None


def generate_key(spec: dict) -> str:
    raw = json.dumps([spec.get(k) for k in ("prompt", "genre", "mood", "bpm", "duration", "seed")], ensure_ascii=False)
    return "g-" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def finish_generated(raw: Path, out: Path, want: float) -> dict:
    """A generated take -> library-ready OGG: leading silence cut, the silent / faded-out tail cut (ACE-Step often
    ends a little early), a 20 ms fade-in and a fade-out over the last 1.5 s of what is left, at most `want` + 4 s."""
    x = decode(Path(raw), 2)
    n = len(x)
    win = int(0.05 * SR)
    frames = max(1, n // win)
    rms = np.sqrt(np.mean(x[: frames * win].reshape(frames, win, 2) ** 2, axis=(1, 2)) + 1e-12)
    db = 20 * np.log10(rms)
    loud = db > (np.percentile(db, 95) - 45)
    first = int(np.argmax(loud)) if loud.any() else 0
    last = frames - int(np.argmax(loud[::-1])) if loud.any() else frames
    a = first * win
    b = min(n, last * win + int(0.3 * SR))
    b = min(b, a + int((want + 4.0) * SR))
    y = x[a:b].copy()
    fi, fo = min(len(y), int(0.02 * SR)), min(len(y), int(1.5 * SR))
    if fi:
        y[:fi] *= np.linspace(0, 1, fi)[:, None]
    if fo:
        y[-fo:] *= np.cos(np.linspace(0, np.pi / 2, fo))[:, None]
    tmp = Path(out).with_suffix(".tmp.wav")
    write_wav(tmp, y)
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(tmp), "-c:a", "libvorbis", "-q:a", "6", str(out)],
                       capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError("could not encode the generated track: " + r.stderr.strip()[-300:])
    return {"raw_seconds": round(n / SR, 2), "kept_seconds": round(len(y) / SR, 2), "cut_head": round(a / SR, 2),
            "cut_tail": round((n - b) / SR, 2), "short": len(y) / SR < want - 0.5}


def analyze(path: Path) -> dict:
    """Sanity checks for a generated track (nobody can listen here): duration, loudness (EBU R128 via
    ffmpeg), true peak, silent fraction, and the spectrum — spectral flatness (white noise ≈ 0.5–0.6 on this
    scale, tonal music < 0.15), spectral centroid, band energies and how much the spectrum changes over time
    (static drones/hum ≈ 0). `ok` is False for silence, noise or a broken file."""
    x = decode(Path(path), 1)[:, 0].astype(np.float64)
    dur = len(x) / SR
    out: dict = {"duration": round(dur, 2)}
    if dur < 0.5:
        return {**out, "ok": False, "problem": "too short"}
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true:framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    m = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", r.stderr)
    tp = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", r.stderr)
    lra = re.findall(r"LRA:\s+(-?[\d.]+) LU", r.stderr)
    out["lufs"] = None if not m or m[-1] == "-inf" else float(m[-1])
    out["true_peak_db"] = None if not tp or tp[-1] == "-inf" else float(tp[-1])
    out["lra"] = float(lra[-1]) if lra else None
    n = 4096
    hop = n // 2
    frames = np.lib.stride_tricks.sliding_window_view(x, n)[::hop]
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    out["silent_fraction"] = round(float(np.mean(20 * np.log10(rms) < -50)), 3)
    spec = np.abs(np.fft.rfft(frames * np.hanning(n), axis=1)) ** 2 + 1e-12
    loud = rms > np.percentile(rms, 20)
    sp = spec[loud] if loud.any() else spec
    flat = np.exp(np.mean(np.log(sp), axis=1)) / np.mean(sp, axis=1)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    cen = np.sum(sp * freqs, axis=1) / np.sum(sp, axis=1)
    bands = {"sub <60": (0, 60), "bass 60-250": (60, 250), "low-mid 250-2k": (250, 2000), "high-mid 2k-6k": (2000, 6000),
             "air >6k": (6000, SR / 2)}
    tot = np.sum(sp)
    out["bands"] = {k: round(float(np.sum(sp[:, (freqs >= a) & (freqs < b)]) / tot), 3) for k, (a, b) in bands.items()}
    out["flatness"] = round(float(np.median(flat)), 4)
    out["centroid_hz"] = round(float(np.median(cen)))
    # spectral flux: mean change (dB) of 24 log-spaced band levels between frames, floored 60 dB below the
    # loudest band, over ~0.5 s steps (0 = a frozen tone or drone; music moves several dB)
    edges = np.geomspace(40, SR / 2, 25)
    bl = np.stack([np.sum(spec[:, (freqs >= a) & (freqs < b)], axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)
    db = 10 * np.log10(bl + 1e-12)
    db = np.maximum(db, db.max() - 60)
    step = max(1, int(0.5 * SR / hop))
    out["flux"] = round(float(np.mean(np.abs(db[step:] - db[:-step]))), 2) if len(db) > step else 0.0
    problems = []
    if out["lufs"] is None or out["lufs"] < -45:
        problems.append("silent")
    if out["silent_fraction"] > 0.5:
        problems.append("mostly silence")
    if out["flatness"] > 0.35:
        problems.append("noise-like spectrum")
    if out["flux"] < 0.5:
        problems.append("static (no musical change)")
    out["ok"] = not problems
    if problems:
        out["problem"] = ", ".join(problems)
    return out


def delete(tid: str) -> None:
    d = MUSIC / _check(tid)
    if not d.exists():
        raise StoreError("track not found", 404)
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- builtin tracks (numpy synthesis, CC0)
def _midi(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


class _Loop:
    """A stereo circular buffer: notes that ring past the end wrap to the start, so it loops seamlessly."""

    def __init__(self, seconds: float):
        self.n = int(round(seconds * SR))
        self.buf = np.zeros((self.n, 2), np.float32)

    def add(self, t: float, x: np.ndarray, pan: float = 0.0) -> None:
        i0 = int(round(t * SR)) % self.n
        x = x.astype(np.float32)
        lg, rg = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        idx = (i0 + np.arange(len(x))) % self.n
        np.add.at(self.buf[:, 0], idx, x * lg)
        np.add.at(self.buf[:, 1], idx, x * rg)

    def echo(self, delay: float, fb: float, mix: float) -> None:
        """Ping-pong delay, computed circularly (steady state of a looping track)."""
        d = int(delay * SR)
        dry = self.buf.copy()
        wet = np.zeros_like(dry)
        tap = dry.copy()
        for k in range(1, 7):
            tap = np.roll(tap[:, ::-1], d, axis=0) * fb
            wet += tap
        self.buf = dry + mix * wet


def _env(n: int, a: float, r: float, sustain: float | None = None) -> np.ndarray:
    t = np.arange(n) / SR
    att = np.clip(t / max(a, 1e-3), 0, 1)
    if sustain is None:
        return att * np.exp(-t / max(r, 1e-3))
    rel = np.clip((sustain + r - t) / r, 0, 1)
    return att * rel


def _onepole(x: np.ndarray, cutoff: float) -> np.ndarray:
    a = float(np.exp(-2 * np.pi * cutoff / SR))
    y = np.empty_like(x)
    acc = 0.0
    # chunked recursion: exact one-pole, done per sample in pure numpy via cumulative trick
    # y[n] = (1-a) * sum_k a^(n-k) x[k]; computed in blocks to keep a^n in float range
    B = 128
    for s in range(0, len(x), B):
        blk = x[s:s + B].astype(np.float64)
        p = a ** np.arange(len(blk))
        y_blk = (1 - a) * np.cumsum(blk / p) * p + acc * a * p
        y[s:s + B] = y_blk
        acc = y_blk[-1]
    return y


def _saw(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    return 2 * ((f * t + phase) % 1.0) - 1


def _pad_note(f: float, dur: float, rng) -> np.ndarray:
    n = int((dur + 1.6) * SR)
    x = sum(_saw(f * d, n, rng.random()) for d in (0.996, 1.0, 1.004)) / 3
    x = _onepole(_onepole(x, 900), 1400)
    return x * _env(n, 0.9, 1.5, dur) * 0.22


def _sine(f: float, n: int) -> np.ndarray:
    return np.sin(2 * np.pi * f * np.arange(n) / SR)


def _pluck(f: float, dur: float, rng) -> np.ndarray:
    """Karplus-Strong string."""
    n = int(dur * SR)
    p = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, p)
    out = np.empty(n)
    for i in range(0, n, p):
        k = min(p, n - i)
        out[i:i + k] = buf[:k]
        buf = 0.5 * (buf + np.roll(buf, -1)) * 0.996
    return _onepole(out, 3500) * _env(n, 0.002, dur * 0.35)


def _epiano(f: float, dur: float) -> np.ndarray:
    n = int((dur + 1.0) * SR)
    t = np.arange(n) / SR
    mod = np.sin(2 * np.pi * f * t) * 1.2 * np.exp(-t / 0.6)
    x = np.sin(2 * np.pi * f * t + mod)
    return x * _env(n, 0.005, 1.2, dur) * 0.25


def _kick(n_s: float = 0.35) -> np.ndarray:
    n = int(n_s * SR)
    t = np.arange(n) / SR
    f = 45 + 90 * np.exp(-t / 0.04)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.12)


def _hat(rng, n_s: float = 0.06) -> np.ndarray:
    n = int(n_s * SR)
    x = rng.uniform(-1, 1, n)
    x = x - _onepole(x, 6000)
    return x * np.exp(-np.arange(n) / SR / 0.018)


CHORDS = {  # midi notes
    "C": [48, 55, 60, 64, 67], "Am": [45, 52, 57, 60, 64], "F": [41, 48, 53, 57, 60], "G": [43, 50, 55, 59, 62],
    "Em": [40, 47, 52, 55, 59], "Dm": [38, 45, 50, 53, 57],
}


def _build_calm() -> np.ndarray:
    rng = np.random.default_rng(7)
    bpm, bars = 72, 8
    bar = 4 * 60 / bpm
    L = _Loop(bars * bar)
    prog = ["C", "Am", "F", "G", "C", "Em", "F", "G"]
    for i, ch in enumerate(prog):
        for k, nn in enumerate(CHORDS[ch][1:]):
            L.add(i * bar, _pad_note(_midi(nn + 12), bar, rng), pan=(k - 1.5) / 3)
        L.add(i * bar, _sine(_midi(CHORDS[ch][0]), int(bar * SR)) * _env(int(bar * SR), 0.3, 0.6, bar - 0.6) * 0.18)
        for b in range(4):   # gentle bell on each beat, chord tones
            nn = CHORDS[ch][1 + (b + i) % 4] + 24
            L.add(i * bar + b * bar / 4, _epiano(_midi(nn), 0.3) * 0.25, pan=0.4 * np.sin(i + b))
    L.echo(60 / bpm * 0.75, 0.35, 0.35)
    return L.buf


def _build_pluck() -> np.ndarray:
    rng = np.random.default_rng(11)
    bpm, bars = 104, 8
    beat = 60 / bpm
    bar = 4 * beat
    L = _Loop(bars * bar)
    prog = ["Am", "F", "C", "G", "Am", "F", "C", "G"]
    for i, ch in enumerate(prog):
        notes = CHORDS[ch][1:] + [CHORDS[ch][2] + 12]
        for e in range(8):
            nn = notes[[0, 2, 1, 3, 4, 3, 1, 2][e]] + 12
            L.add(i * bar + e * beat / 2, _pluck(_midi(nn), 0.9, rng) * 0.32, pan=0.5 if e % 2 else -0.5)
        L.add(i * bar, _sine(_midi(CHORDS[ch][0] - 12 + 12), int(bar * SR)) * _env(int(bar * SR), 0.02, 0.4, bar - 0.4) * 0.22)
        for b in range(4):
            L.add(i * bar + b * beat, _kick() * 0.35)
            L.add(i * bar + b * beat + beat / 2, _hat(rng) * 0.10, pan=0.3)
    L.echo(beat * 0.75, 0.3, 0.25)
    return L.buf


def _build_pulse() -> np.ndarray:
    rng = np.random.default_rng(23)
    bpm, bars = 90, 8
    beat = 60 / bpm
    bar = 4 * beat
    L = _Loop(bars * bar)
    prog = ["Dm", "F", "C", "G", "Dm", "F", "Am", "G"]
    for i, ch in enumerate(prog):
        for k, nn in enumerate(CHORDS[ch][1:4]):
            for hit in (0, 1.5, 2.5):
                L.add(i * bar + hit * beat, _epiano(_midi(nn + 12), beat * 0.9) * 0.55, pan=(k - 1) * 0.4)
        bass = _sine(_midi(CHORDS[ch][0]), int(bar * SR)) + 0.3 * _sine(2 * _midi(CHORDS[ch][0]), int(bar * SR))
        L.add(i * bar, bass * _env(int(bar * SR), 0.01, 0.3, bar - 0.3) * 0.2)
        for b in range(4):
            if b in (0, 2):
                L.add(i * bar + b * beat, _kick() * 0.3)
            if b in (1, 3):
                L.add(i * bar + b * beat, _hat(rng, 0.18) * 0.12)
            for s in range(2):
                L.add(i * bar + b * beat + s * beat / 2 + beat / 4, _hat(rng) * 0.05, pan=-0.3)
    L.echo(beat * 0.5, 0.25, 0.2)
    return L.buf


BUILTIN = [
    {"id": "builtin-calm-pad", "title": "Calm pad", "mood": "calm, warm, gentle", "bpm": 72, "build": _build_calm},
    {"id": "builtin-bright-pluck", "title": "Bright pluck", "mood": "upbeat, friendly, bright", "bpm": 104, "build": _build_pluck},
    {"id": "builtin-soft-pulse", "title": "Soft pulse", "mood": "modern, focused, tech", "bpm": 90, "build": _build_pulse},
]


def ensure_builtin() -> list[str]:
    """Synthesise the builtin tracks once (a few seconds of CPU). Returns the ids that were created."""
    made = []
    for spec in BUILTIN:
        if get(spec["id"]):
            continue
        x = spec["build"]()
        x = x / (np.max(np.abs(x)) + 1e-9) * 0.7
        tmp_dir = MUSIC / "_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        wav = tmp_dir / f"{spec['id']}.wav"
        write_wav(wav, x)
        ogg = tmp_dir / "track.ogg"
        subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(wav), "-c:a", "libvorbis", "-q:a", "6", str(ogg)],
                       check=True, capture_output=True, creationflags=NOWIN)
        add_track(ogg, {"id": spec["id"], "title": spec["title"], "artist": "Video Studio",
                        "licence": "CC0, generated locally", "source": "builtin", "mood": spec["mood"], "bpm": spec["bpm"],
                        "prompt": "numpy synthesis (pads, plucks, FM piano, kick/hat), seamless loop"}, move=True)
        wav.unlink(missing_ok=True)
        made.append(spec["id"])
    shutil.rmtree(MUSIC / "_tmp", ignore_errors=True)
    return made


# ---------------------------------------------------------------- ducking + mix
def speech_gate(voice: np.ndarray, hop: float = 0.01) -> tuple[np.ndarray, float]:
    """1/0 per `hop` frame: is the voice speaking? (RMS above peak-38 dB, dilated by look-ahead/hold)."""
    h = int(hop * SR)
    n = len(voice) // h
    if n == 0:
        return np.zeros(0), hop
    fr = voice[: n * h].reshape(n, h)
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    top = np.percentile(db, 98) if n > 10 else db.max()
    on = (db > max(top - 38, -60)).astype(np.float64)
    pre, hold = int(DUCK_LOOKAHEAD / hop), int(DUCK_HOLD / hop)
    k = np.convolve(on, np.ones(pre + hold + 1), mode="full")
    # gate[i] = speech anywhere in [i - hold, i + pre]: down `pre` before a word, up `hold` after it
    # (it was k[hold:], which swapped the two: 0.30 s look-ahead and only 0.15 s hold)
    gate = (k[pre: pre + n] > 0).astype(np.float64)
    return gate, hop


def duck_curve(gate: np.ndarray, hop: float, duck_db: float) -> np.ndarray:
    """Gain in dB per frame: 0 in pauses, duck_db while speaking, smoothed (attack/release)."""
    target = gate * duck_db
    out = np.empty_like(target)
    g = 0.0
    a_att = np.exp(-hop / DUCK_ATTACK)
    a_rel = np.exp(-hop / DUCK_RELEASE)
    for i, tv in enumerate(target):
        a = a_att if tv < g else a_rel
        g = a * g + (1 - a) * tv
        out[i] = g
    return out


def _fit_music(track: np.ndarray, n: int, start: float, xfade: float = 1.0) -> np.ndarray:
    """n samples of music from `start` (s), looping with an equal-power cross-fade if needed."""
    s0 = int(start * SR) % max(1, len(track))
    x = track[s0:]
    xf = int(min(xfade, len(track) / SR / 4) * SR)
    while len(x) < n:
        if xf > 0 and len(x) > xf:
            t = np.linspace(0, np.pi / 2, xf)[:, None]
            head = track[:xf] * np.sin(t)
            tail = x[-xf:] * np.cos(t)
            x = np.concatenate([x[:-xf], head + tail, track[xf:]])
        else:
            x = np.concatenate([x, track])
    return x[:n]


_voice_lufs: dict = {}


def voice_lufs(wav: Path) -> float | None:
    k = (str(wav), wav.stat().st_mtime_ns)
    if k not in _voice_lufs:
        _voice_lufs[k] = loudness(wav)
    return _voice_lufs[k]


def settings(m: dict | None) -> dict | None:
    """The project's music settings with defaults, or None when no (existing) track is chosen."""
    if not m or not m.get("track") or not get(m["track"]):
        return None
    from brief import MUSIC_DEFAULT
    return {**MUSIC_DEFAULT, **{k: v for k, v in m.items() if v is not None}}


_preview_cache: dict = {}


def preview_info(m: dict, voice: Path | None, duration: float) -> dict:
    """What the editor preview needs to play the music with approximate ducking.

    gain_db: gain for the raw track relative to the raw voice-over (both played at volume 1);
    duck: [dB per 0.1 s] envelope (empty without a voice-over)."""
    meta = get(m["track"])
    vl = voice_lufs(voice) if voice else None
    vl = vl if vl is not None else VOICE_REF
    gain = (vl - VOICE_REF) + (MUSIC_REF - meta["lufs"]) + m["volume_db"]
    duck = []
    if voice and m.get("duck"):
        key = (str(voice), voice.stat().st_mtime_ns, m["duck_db"])
        if key not in _preview_cache:
            v = decode(voice, 1)[:, 0]
            gate, hop = speech_gate(v)
            curve = duck_curve(gate, hop, m["duck_db"])
            step = int(0.1 / hop)
            _preview_cache.clear()
            _preview_cache[key] = [round(float(x), 1) for x in curve[::step]]
        duck = _preview_cache[key]
    return {"track": meta["id"], "title": meta["title"], "url": f"/api/library/music/{meta['id']}/track{meta['ext']}",
            "duration": meta["duration"], "gain_db": round(gain, 2), "duck": duck, "duck_step": 0.1,
            **{k: m[k] for k in ("start", "fade_in", "fade_out", "volume_db", "duck_db")}, "duration_video": duration}


def mix_key(voice: Path | None, m: dict, duration: float) -> str:
    meta = get(m["track"]) or {}
    vk = [str(voice), voice.stat().st_mtime_ns, voice.stat().st_size] if voice else None
    raw = json.dumps([vk, meta.get("id"), meta.get("lufs"), meta.get("duration"), {k: m.get(k) for k in
                      ("volume_db", "duck", "duck_db", "fade_in", "fade_out", "start")}, round(duration, 3), MIX_VERSION])
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def mix(voice: Path | None, m: dict, duration: float, out: Path, stems: bool = False) -> dict:
    """voice-over (or silence) + music -> stereo 48 kHz WAV at -14 LUFS. Returns measurements.

    stems=True also writes <out>.voice.wav and <out>.music.wav with the final gain applied (for measuring)."""
    t0 = time.time()
    n = int(round(duration * SR))
    meta = get(m["track"])
    track = decode(track_file(meta["id"]), 2)
    music = _fit_music(track, n, float(m.get("start") or 0))
    stats: dict = {"track": meta["id"], "track_lufs": meta["lufs"]}
    if voice:
        v = decode(voice, 1)[:, 0]
        v = np.pad(v[:n], (0, max(0, n - len(v))))
        vl = voice_lufs(voice)
        vg = 10 ** ((VOICE_REF - vl) / 20) if vl is not None else 1.0
        stats["voice_lufs"] = vl
    else:
        v, vg = np.zeros(n, np.float32), 1.0
    mg_db = MUSIC_REF - meta["lufs"] + m["volume_db"]
    g = np.full(n, 10 ** (mg_db / 20), np.float64)
    if voice and m.get("duck"):
        gate, hop = speech_gate(v)
        curve = duck_curve(gate, hop, m["duck_db"])
        tt = (np.arange(len(curve)) + 0.5) * hop
        g *= 10 ** (np.interp(np.arange(n) / SR, tt, curve) / 20)
        stats["speech_fraction"] = round(float(gate.mean()), 3)
    fi, fo = int(m.get("fade_in", 0) * SR), int(m.get("fade_out", 0) * SR)
    if fi > 0:
        g[:fi] *= np.sin(np.linspace(0, np.pi / 2, min(fi, n)))[: min(fi, n)]
    if fo > 0:
        fo = min(fo, n)
        g[n - fo:] *= np.cos(np.linspace(0, np.pi / 2, fo))
    x = music * g[:, None].astype(np.float32) + (v * vg)[:, None].astype(np.float32)
    out = Path(out)
    tmp = out.with_suffix(".pre.wav")
    peak = float(np.max(np.abs(x))) or 1.0
    write_wav(tmp, x / max(1.0, peak))          # headroom only; loudnorm sets the level
    # two-pass EBU R128 loudness normalisation (linear when possible)
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(tmp), "-af",
                        f"loudnorm=I={TARGET}:TP={TRUE_PEAK}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    js = json.loads(r.stderr[r.stderr.rindex("{"): r.stderr.rindex("}") + 1])
    af = (f"loudnorm=I={TARGET}:TP={TRUE_PEAK}:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:"
          f"linear=true:print_format=json")
    fin = out.with_suffix(".tmp.wav")
    r2 = subprocess.run([FFMPEG, "-y", "-hide_banner", "-nostats", "-i", str(tmp), "-af", af, "-ar", str(SR), "-ac", "2",
                         "-c:a", "pcm_s16le", str(fin)], capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    tmp.unlink(missing_ok=True)
    if r2.returncode != 0 or not fin.exists():
        raise RuntimeError("music mix failed: " + r2.stderr.strip()[-300:])
    js2 = json.loads(r2.stderr[r2.stderr.rindex("{"): r2.stderr.rindex("}") + 1])
    fin.replace(out)
    if stems:   # linear loudnorm = one constant gain: apply it to each stem
        k = 10 ** ((float(js2["output_i"]) - float(js["input_i"])) / 20) / max(1.0, peak)
        write_wav(out.with_suffix(".voice.wav"), np.repeat((v * vg * k)[:, None], 2, axis=1))
        write_wav(out.with_suffix(".music.wav"), music * g[:, None].astype(np.float32) * k)
    stats.update(music_gain_db=round(mg_db, 2), voice_gain_db=round(20 * np.log10(vg), 2),
                 pre_lufs=float(js["input_i"]), lufs=float(js2["output_i"]), true_peak=float(js2["output_tp"]),
                 normalization=js2.get("normalization_type"), seconds=round(time.time() - t0, 2))
    return stats
