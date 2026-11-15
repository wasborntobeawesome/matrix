"""Shared AI library (all projects): b-roll clips and presenter portraits.

    video/library/broll/<id>/      clip.mp4 (H.264 from the model), clip.webm (VP9, for the renderer),
                                   thumb.jpg, meta.json
    video/library/portraits/<id>/  portrait.png, thumb.jpg, meta.json

B-roll ids are a hash of prompt + seed + steps + resolution (incl. frame count), so an
identical request reuses the clip instead of spending GPU time again.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import paths

from PIL import Image

from store import FFMPEG, StoreError, now, probe

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
LIB = paths.LIB
BROLL = LIB / "broll"
PORTRAITS = LIB / "portraits"
AI = VIDEO / "ai"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_lock = threading.RLock()

WAN = {"model": "Wan2.2-TI2V-5B (Wan-AI)", "licence": "Apache-2.0"}
ECHO = {"model": "EchoMimicV3-Flash (Ant Group) on Wan2.1-Fun-1.3B-InP", "licence": "Apache-2.0"}

# Generation presets. "final" = the settings of the existing clips; "draft" = fast trials.
BROLL_PRESETS = {
    "draft": {"steps": 20, "frames": 81, "portrait": (480, 832), "landscape": (832, 480)},
    "final": {"steps": 30, "frames": 121, "portrait": (704, 1280), "landscape": (1280, 704)},
}
NEGATIVE = (
    "bright colors, overexposed, static, blurred details, subtitles, text, watermark, logo, style, "
    "artwork, painting, still picture, overall gray, worst quality, low quality, JPEG compression "
    "residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, "
    "disfigured, misshapen limbs, fused fingers, still frame, messy background, many people, "
    "walking backwards"
)


def broll_key(prompt: str, seed: int, steps: int, width: int, height: int, frames: int) -> str:
    raw = json.dumps([prompt.strip(), int(seed), int(steps), f"{int(width)}x{int(height)}x{int(frames)}"])
    return "b-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def _check(i: str) -> str:
    if not ID_RE.match(i or ""):
        raise StoreError("bad library id", 404)
    return i


def vp9(src: Path, dest: Path, gop: int = 6, crf: int = 26, fps: int | None = None, max_side: int = 1600) -> None:
    """Any video -> silent VP9 WebM with a short GOP (cheap random seeks in the renderer)."""
    tmp = dest.with_suffix(".tmp.webm")
    vf = (f"scale=w='trunc(min(1,{max_side}/max(iw,ih))*iw/2)*2':h='trunc(min(1,{max_side}/max(iw,ih))*ih/2)*2'"
          ":flags=lanczos" + (f",fps={fps}" if fps else "") + ",format=yuv420p")
    r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-an", "-vf", vf, "-c:v", "libvpx-vp9",
                        "-b:v", "0", "-crf", str(crf), "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
                        "-g", str(gop), "-f", "webm", str(tmp)],
                       capture_output=True, text=True, errors="replace", timeout=1800, creationflags=NOWIN)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise StoreError(f"could not convert video: {r.stderr.strip()[-300:]}")
    tmp.replace(dest)


def video_thumb(src: Path, dest: Path, at: float = 1.0, width: int = 360) -> None:
    for ss in (at, 0):
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(ss), "-i", str(src), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-q:v", "4", str(dest)],
                       capture_output=True, timeout=60, creationflags=NOWIN)
        if dest.exists():
            return


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(p: Path, d: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------- b-roll
def broll_list() -> list[dict]:
    out = []
    if BROLL.exists():
        for d in BROLL.iterdir():
            m = _read(d / "meta.json")
            if m and (d / "clip.webm").exists():
                out.append(m)
    return sorted(out, key=lambda m: m.get("created") or 0, reverse=True)


def broll_get(cid: str) -> dict | None:
    if not cid or not ID_RE.match(cid):
        return None
    d = BROLL / cid
    m = _read(d / "meta.json")
    return m if m and (d / "clip.webm").exists() else None


def broll_spec(m: dict) -> tuple:
    """What a b-roll clip shows: (prompt, seed, image sha, provider). Both orientations of one b-roll share it."""
    return (str(m.get("prompt") or "").strip(), str(m.get("seed")), m.get("image_sha") or None, m.get("provider") or None)


def spec_matches(m: dict, want: tuple) -> bool:
    """broll_spec(m) == want; a provider missing on either side (clips from before providers) is not compared."""
    have = broll_spec(m)
    if have[3] is None or want[3] is None:
        return have[:3] == tuple(want[:3])
    return have == tuple(want)


def variant_fits(main_id: str | None, alt_id: str | None) -> bool:
    """Was the other-orientation clip `alt_id` made for the same b-roll as `main_id` (same prompt, seed, animated
    image, provider)? A clip that is not in the library cannot be compared and counts as fitting."""
    m, a = broll_get(main_id) if main_id else None, broll_get(alt_id) if alt_id else None
    if not m or not a:
        return True
    return spec_matches(a, broll_spec(m))


def drop_stale_alts(brief: dict) -> list[str]:
    """Remove `clip_alt` from scenes whose main clip it was not made for (another clip was picked / generated).
    Returns the scene ids it changed."""
    out = []
    for s in brief.get("scenes") or []:
        bg = s.get("background")
        if isinstance(bg, dict) and bg.get("clip_alt") and bg.get("clip") and not variant_fits(bg["clip"], bg["clip_alt"]):
            bg.pop("clip_alt", None)
            out.append(s.get("id"))
    return out


def broll_file(cid: str, name: str) -> Path:
    if name not in ("clip.mp4", "clip.webm", "thumb.jpg"):
        raise StoreError("not found", 404)
    p = BROLL / _check(cid) / name
    if not p.is_file():
        raise StoreError("not found", 404)
    return p


def broll_add(src: Path, meta: dict, move: bool = False) -> dict:
    """Store a generated clip (mp4) under its cache id; make the VP9 copy + thumbnail."""
    cid = _check(meta["id"])
    d = BROLL / cid
    with _lock:
        d.mkdir(parents=True, exist_ok=True)
        mp4 = d / "clip.mp4"
        (shutil.move if move else shutil.copy2)(str(src), mp4)
        vp9(mp4, d / "clip.webm")
        video_thumb(mp4, d / "thumb.jpg")
        info = probe(d / "clip.webm")
        m = {**WAN, **meta, "w": info.get("w") or meta.get("width"), "h": info.get("h") or meta.get("height"),
             "duration": info.get("duration") or meta.get("seconds"), "created": meta.get("created") or now(),
             "size": mp4.stat().st_size}
        _write(d / "meta.json", m)
    return m


def broll_delete(cid: str) -> None:
    d = BROLL / _check(cid)
    if not d.exists():
        raise StoreError("clip not found", 404)
    with _lock:
        shutil.rmtree(d, ignore_errors=True)


def import_existing() -> list[str]:
    """First start: put video/ai/clips/{waves,phone,mic}.mp4 into the library (once)."""
    marker = LIB / ".imported"
    if marker.exists():
        return []
    LIB.mkdir(parents=True, exist_ok=True)
    stats = _read(AI / "clips" / "stats.json") or []
    added = []
    for name in ("waves", "phone", "mic"):
        src = AI / "clips" / f"{name}.mp4"
        if not src.exists():
            continue
        rec = next((r for r in reversed(stats) if r.get("name") == name), None) or {}
        w, h = (int(x) for x in str(rec.get("size") or "704x1280").split("x"))
        steps, seed, frames = int(rec.get("steps") or 30), int(rec.get("seed") or 42), int(rec.get("frames") or 121)
        prompt = rec.get("prompt") or name
        cid = broll_key(prompt, seed, steps, w, h, frames)
        if broll_get(cid):
            continue
        broll_add(src, {"id": cid, "name": name, "prompt": prompt, "negative": NEGATIVE, "seed": seed,
                        "steps": steps, "guidance": rec.get("guidance", 5.0), "width": w, "height": h,
                        "frames": frames, "fps": rec.get("fps", 24), "seconds": round(frames / (rec.get("fps") or 24), 2),
                        "preset": "final", "source": "imported from video/ai/clips",
                        "gen_seconds": rec.get("gen_seconds"), "peak_vram_reserved_gb": rec.get("peak_vram_reserved_gb"),
                        "created": src.stat().st_mtime})
        added.append(cid)
    # the fictional presenter portrait made by video/ai/portrait.py
    pp = AI / "build" / "presenter.png"
    if pp.exists():
        try:
            portrait_add(pp, {"source": "generated", "fictional": True, "name": "Presenter (Wan2.2, seed 1)",
                              "prompt": "Professional studio portrait photo of a friendly fictional young Central Asian "
                                        "woman news presenter, head and shoulders (video/ai/portrait.py)",
                              "seed": 1, **WAN})
        except StoreError:
            pass
    marker.write_text(json.dumps({"imported": added, "at": time.time()}))
    return added


# ---------------------------------------------------------------- portraits
CONSENT_TEXT = "This is a fictional person or I have the person's permission"


def portrait_list() -> list[dict]:
    out = []
    if PORTRAITS.exists():
        for d in PORTRAITS.iterdir():
            m = _read(d / "meta.json")
            if m and (d / "portrait.png").exists():
                out.append(m)
    return sorted(out, key=lambda m: m.get("created") or 0, reverse=True)


def portrait_get(pid: str) -> dict | None:
    if not pid or not ID_RE.match(pid):
        return None
    m = _read(PORTRAITS / pid / "meta.json")
    return m if m and (PORTRAITS / pid / "portrait.png").exists() else None


def portrait_file(pid: str, name: str) -> Path:
    if name not in ("portrait.png", "thumb.jpg"):
        raise StoreError("not found", 404)
    p = PORTRAITS / _check(pid) / name
    if not p.is_file():
        raise StoreError("not found", 404)
    return p


def portrait_add(src: Path, meta: dict) -> dict:
    """Validate an image, store it as PNG (RGB, long side <= 1600) with a thumbnail."""
    try:
        with Image.open(src) as im:
            im.verify()
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w < 256 or h < 256:
                raise StoreError("portrait is too small (at least 256x256)")
            if w * h > 60_000_000:
                raise StoreError("portrait is too large")
            im.thumbnail((1600, 1600), Image.LANCZOS)
            import io
            buf = io.BytesIO()
            im.save(buf, "PNG")
    except StoreError:
        raise
    except Exception:
        raise StoreError("not a valid image (png, jpg, webp)") from None
    data = buf.getvalue()
    pid = "p-" + hashlib.sha1(data).hexdigest()[:10]
    d = PORTRAITS / pid
    with _lock:
        d.mkdir(parents=True, exist_ok=True)
        (d / "portrait.png").write_bytes(data)
        with Image.open(d / "portrait.png") as im:
            W, H = im.size
            t = im.copy()
            t.thumbnail((360, 640))
            t.save(d / "thumb.jpg", "JPEG", quality=84)
        m = {"id": pid, "created": now(), "w": W, "h": H, **meta}
        if meta.get("source") == "upload":
            m["consent_text"] = CONSENT_TEXT
        _write(d / "meta.json", m)
    return m


def portrait_delete(pid: str) -> None:
    d = PORTRAITS / _check(pid)
    if not d.exists():
        raise StoreError("portrait not found", 404)
    with _lock:
        shutil.rmtree(d, ignore_errors=True)


def file_sha(p: Path) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:16]
