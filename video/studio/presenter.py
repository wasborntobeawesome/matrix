"""AI presenter: plan, cache and assemble the talking-head video for a project.

The talking head is generated per *piece* of the timeline. A scene that shows the presenter
(corner bubble or `presenter` layout) is split into chunks of <= 5 s (EchoMimic sequence
limit on 16 GB); every other scene is filled with the still portrait (never visible).

    projects/<id>/presenter/seg/<segkey>.wav|.mp4   one EchoMimic clip per chunk
    projects/<id>/presenter/<key>.webm              assembled, time-aligned to the whole timeline
    projects/<id>/presenter/<key>.json              metadata

segkey = hash(scene voice clip, offset, length, portrait, settings), so editing one line
only regenerates that scene's chunks. key = hash of all pieces: when the voice-over changes,
the current key has no file yet and the presenter is "stale" (the render leaves it out).
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

from PIL import Image

import library as L
from store import FFMPEG, Project, StoreError

FPS = 25
MAX_CHUNK = 5.0
PRESETS = {
    "draft": {"width": 448, "height": 800, "steps": 8, "teacache": 0.1, "seed": 43},
    "final": {"width": 576, "height": 1024, "steps": 8, "teacache": 0.1, "seed": 43},
}
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_sha_cache: dict = {}


def pdir(p: Project) -> Path:
    return p.dir / "presenter"


def portrait_source(p: Project, ref: str | None) -> tuple[Path | None, dict]:
    """Resolve presenter.portrait: a library portrait id or a project image asset."""
    if not ref:
        return None, {}
    m = L.portrait_get(ref)
    if m:
        return L.PORTRAITS / ref / "portrait.png", {"kind": "library", "id": ref, "meta": m,
                                                    "url": f"/api/library/portraits/{ref}/portrait.png"}
    try:
        f = p.asset_path(ref)
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return f, {"kind": "asset", "id": ref, "url": f"/api/projects/{p.id}/files/{ref}"}
    except StoreError:
        pass
    return None, {}


def sha(f: Path) -> str:
    st = f.stat()
    k = (str(f), st.st_mtime, st.st_size)
    if k not in _sha_cache:
        _sha_cache[k] = L.file_sha(f)
    return _sha_cache[k]


def needed(brief: dict) -> tuple[list[str], list[str], dict]:
    """(bubble scene ids, full-frame presenter scene ids, normalised presenter settings)."""
    pres = brief.get("presenter") if isinstance(brief.get("presenter"), dict) else {}
    ids = [s["id"] for s in brief["scenes"]]
    full = [s["id"] for s in brief["scenes"] if s.get("layout") == "presenter"]
    bubble = []
    if pres.get("enabled") and pres.get("mode", "corner") == "corner":
        want = ids if pres.get("scenes", "all") == "all" else [i for i in pres.get("scenes") or [] if i in ids]
        bubble = [i for i in want if i not in full]
    if not pres.get("enabled") and not full:
        return [], [], pres
    return bubble, full, pres


def plan(p: Project, brief: dict, tl: dict, quality: str | None = None) -> dict | None:
    """Current presenter state for a timeline. None when the project uses no presenter."""
    import build  # late import (build imports this module)
    from brief import localize
    if not brief.get("_lang"):
        brief = localize(brief)          # the presenter speaks the main language
    bubble, full, pres = needed(brief)
    if not bubble and not full:
        return None
    quality = quality or pres.get("quality") or "draft"
    st = PRESETS.get(quality, PRESETS["draft"])
    out = {"mode": pres.get("mode", "corner"), "enabled": bool(pres.get("enabled")), "bubble": bubble, "full": full,
           "quality": quality, "ready": False, "stale": False, "video": None, "portrait": None, "error": None,
           "w": st["width"], "h": st["height"], "pieces": [], "missing_seconds": 0.0}
    src, info = portrait_source(p, pres.get("portrait"))
    if not src:
        out["error"] = "choose a portrait first" if not pres.get("portrait") else f"portrait '{pres.get('portrait')}' not found"
        return out
    out["portrait"] = info
    psha = sha(src)
    settings = [st["width"], st["height"], st["steps"], st["teacache"], st["seed"]]
    by_id = {s["id"]: s for s in brief["scenes"]}
    show = set(bubble) | set(full)
    d = pdir(p)
    pieces = []
    for s in tl["scenes"]:
        a, b = s["start"], s["end"]
        if s["id"] not in show:
            pieces.append({"type": "still", "start": a, "dur": round(b - a, 3), "scene": s["id"]})
            continue
        vk = build.voice_key(brief, by_id[s["id"]]) if s.get("voiced") else "silence"
        speech = round(s["speech_end"] - a, 3)
        n = max(1, math.ceil((b - a) / MAX_CHUNK - 1e-6))
        ch = (b - a) / n
        for i in range(n):
            k = hashlib.sha1(json.dumps([vk, speech, round(i * ch, 3), round(ch, 3), psha, settings]).encode()).hexdigest()[:16]
            seg = d / "seg" / f"{k}.mp4"
            pieces.append({"type": "seg", "key": k, "start": round(a + i * ch, 3), "dur": round(ch, 3), "scene": s["id"],
                           "wav": str(d / "seg" / f"{k}.wav"), "out": str(seg), "done": seg.exists()})
            if not seg.exists():
                out["missing_seconds"] += ch
    key = hashlib.sha1(json.dumps([[x["type"], x.get("key"), x["dur"]] for x in pieces] + [psha, settings]).encode()).hexdigest()[:16]
    f = d / f"{key}.webm"
    out.update(key=key, pieces=pieces, ready=f.exists(), file=str(f), portrait_sha=psha, portrait_path=str(src),
               missing_seconds=round(out["missing_seconds"], 2),
               generate_seconds=round(sum(x["dur"] for x in pieces if x["type"] == "seg"), 2))
    if f.exists():
        out["video"] = f.name
    else:
        out["stale"] = any(d.glob("*.webm"))
    return out


def crop_portrait(src: Path, W: int, H: int, dest: Path) -> Path:
    """Cover-crop to W:H (keep the upper part: faces sit high in portraits)."""
    if dest.exists():
        return dest
    with Image.open(src) as im:
        im = im.convert("RGB")
        w, h = im.size
        if w / h > W / H:
            nw = round(h * W / H)
            box = ((w - nw) // 2, 0, (w - nw) // 2 + nw, h)
        else:
            nh = round(w * H / W)
            y0 = round((h - nh) * 0.25)
            box = (0, y0, w, y0 + nh)
        im.crop(box).resize((W, H), Image.LANCZOS).save(dest, "PNG")
    return dest


def slice_wav(voiceover: Path, start: float, dur: float, dest: Path) -> None:
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.wav")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(voiceover), "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-ac", "1", "-ar", "16000", str(tmp)], check=True, capture_output=True, creationflags=NOWIN)
    tmp.replace(dest)


def assemble(pl: dict, portrait_png: Path, duration: float) -> Path:
    """Concatenate generated chunks + still fillers into one VP9 WebM aligned to the timeline."""
    out = Path(pl["file"])
    W, H = pl["w"], pl["h"]
    inputs, fc = [], []
    # frame-exact piece boundaries so the lip sync never drifts over many pieces
    bounds = [round(x["start"] * FPS) for x in pl["pieces"]] + [round(duration * FPS)]
    for i, x in enumerate(pl["pieces"]):
        nfr = max(1, bounds[i + 1] - bounds[i])
        if x["type"] == "seg":
            inputs += ["-i", x["out"]]
            fc.append(f"[{i}:v]fps={FPS},tpad=stop_mode=clone:stop_duration=2,trim=end_frame={nfr},setpts=PTS-STARTPTS,"
                      f"scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p[v{i}]")
        else:
            inputs += ["-loop", "1", "-framerate", str(FPS), "-t", f"{nfr / FPS + 0.2:.3f}", "-i", str(portrait_png)]
            fc.append(f"[{i}:v]fps={FPS},trim=end_frame={nfr},setpts=PTS-STARTPTS,scale={W}:{H},setsar=1,format=yuv420p[v{i}]")
    fc.append("".join(f"[v{i}]" for i in range(len(pl["pieces"]))) + f"concat=n={len(pl['pieces'])}:v=1:a=0[out]")
    tmp = out.with_suffix(".tmp.webm")
    r = subprocess.run([FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc), "-map", "[out]",
                        "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30", "-deadline", "good", "-cpu-used", "4",
                        "-row-mt", "1", "-g", "10", "-an", "-f", "webm", str(tmp)],
                       capture_output=True, text=True, errors="replace", creationflags=NOWIN)
    if r.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise RuntimeError("assembling the presenter video failed: " + r.stderr.strip()[-400:])
    tmp.replace(out)
    L.video_thumb(out, out.with_suffix(".jpg"), at=min(2.0, duration / 2))
    meta = {k: pl[k] for k in ("key", "quality", "w", "h", "bubble", "full", "portrait_sha", "generate_seconds")}
    meta["duration"] = duration
    meta["portrait"] = (pl.get("portrait") or {}).get("id")
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    # keep the 3 newest assembled videos
    olds = sorted(out.parent.glob("*.webm"), key=lambda f: f.stat().st_mtime, reverse=True)[3:]
    for o in olds:
        for ext in (".webm", ".json", ".jpg"):
            o.with_suffix(ext).unlink(missing_ok=True)
    return out
