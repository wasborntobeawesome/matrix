"""Voice-over + timeline for a project (uses video/shared/voicelib.py).

Per-scene clips are cached in build/voice/<hash>.wav where hash covers
text + model + voice + rate + language + style, so editing one line only
re-synthesises that line. Scenes without a clip get an estimated length and
silence, so preview and render always work (even when Ovoz is down).
"""
from __future__ import annotations

import hashlib
import os
import json
import sys
from pathlib import Path

import library
import presenter
from brief import FORMATS
from store import Project

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import voicelib as V  # noqa: E402

# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and costs ~2 s per call
OVOZ = os.environ.get("OVOZ_URL", "http://127.0.0.1:8000")
FPS = 30
MIN_SCENE = {"screen": 3.2, "screens": 3.4, "stat": 3.0, "presenter": 2.6}


def voice_key(brief: dict, scene: dict) -> str | None:
    text = (scene.get("say") or "").strip()
    if not text:
        return None
    v = brief.get("voice", {})
    raw = json.dumps([text, v.get("model"), v.get("voice"), int(v.get("rate") or 0), brief.get("language"),
                      v.get("style")], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def voice_file(p: Project, key: str) -> Path:
    return p.build_dir / "voice" / f"{key}.wav"


def scene_voice(p: Project, brief: dict, scene: dict) -> Path | None:
    k = voice_key(brief, scene)
    if k and voice_file(p, k).exists():
        return voice_file(p, k)
    return None


def generate(p: Project, scene_ids: list[str] | None = None, force: bool = False, progress=None, api: str = OVOZ) -> dict:
    """Synthesise missing clips (or `scene_ids` only). Raises voicelib.TTSError."""
    brief = p.brief
    v = brief["voice"]
    todo = [s for s in brief["scenes"] if (s.get("say") or "").strip() and (scene_ids is None or s["id"] in scene_ids)]
    done = 0
    made = []
    for i, s in enumerate(todo):
        if progress:
            progress(i / max(1, len(todo)), f"voice: {s['id']}")
        f = voice_file(p, voice_key(brief, s))
        if f.exists() and not force:
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        audio = V.tts(s["say"], v["model"], v["voice"], brief["language"], v.get("rate", 0), v.get("style"), api=api)
        V.to_wav(audio, f)
        made.append(s["id"])
        done += 1
    if progress:
        progress(1.0, "voice: done")
    return {"generated": made, "total": len(todo)}


def timeline(p: Project, brief: dict | None = None) -> dict:
    """Timeline for the renderer: scene times (real clip lengths or estimates), captions, assets."""
    brief = brief or p.brief
    lang = brief["language"]
    W, H = FORMATS.get(brief.get("format"), FORMATS["9:16"])
    assets = p.assets()
    rate = int(brief.get("voice", {}).get("rate") or 0)
    t = 0.0
    scenes, captions, parts = [], [], []
    n = len(brief["scenes"])
    voiced = 0
    with_say = 0
    for i, s in enumerate(brief["scenes"]):
        last = i == n - 1
        wav = scene_voice(p, brief, s)
        say = (s.get("say") or "").strip()
        if say:
            with_say += 1
        if wav:
            speech = V.duration(wav)
            voiced += 1
        else:
            speech = V.estimate_speech(say, lang, rate)
        pad = s.get("pad", 1.0 if last else 0.4)
        length = max(speech + pad, s.get("duration") or 0, MIN_SCENE.get(s["layout"], 2.2))
        if s["layout"] == "screen" and assets.get(s.get("asset"), {}).get("type") == "video" and not s.get("duration"):
            vd = assets[s["asset"]].get("duration") or 0
            length = max(length, min(vd - float(s.get("trim_start") or 0) + 0.5, 10.0))
        length = round(length, 3)
        scenes.append({**s, "start": round(t, 3), "end": round(t + length, 3), "speech_end": round(t + speech, 3),
                       "voiced": bool(wav)})
        if say:
            captions += V.caption_chunks(say, t, speech, lang=lang)
        parts.append((wav, speech, length - speech))
        t += length
    duration = round(t, 3)
    used = set()
    for s in brief["scenes"]:
        if s.get("asset"):
            used.add(s["asset"])
        for it in s.get("items") or []:
            if isinstance(it, dict) and it.get("asset"):
                used.add(it["asset"])
    if brief["brand"].get("logo"):
        used.add(brief["brand"]["logo"])
    has_broll = _ai_layers(p, brief, scenes)
    tl = {
        "title": brief.get("title"), "language": lang, "format": brief.get("format", "9:16"),
        "width": W, "height": H, "fps": FPS, "duration": duration, "frames": round(duration * FPS),
        "brand": {"name": brief.get("product"), **brief["brand"]},
        "voice": brief.get("voice"),
        "audio": None, "assets_dir": "../assets",
        "assets": {k: {kk: vv for kk, vv in m.items() if kk in ("type", "w", "h", "duration")}
                   for k, m in assets.items() if k in used},
        "scenes": scenes, "captions": captions, "captions_off": brief.get("captions") is False,
        "voiced": voiced, "voice_needed": with_say,
    }
    key = hashlib.sha1(json.dumps([[str(w) if w else None, round(sp, 3), round(pd, 3)] for w, sp, pd in parts]).encode()).hexdigest()[:16]
    tl["audio_key"] = key if voiced else None
    tl["_parts"] = parts
    if has_broll:
        tl["broll"] = True
    pl = presenter.plan(p, brief, tl)
    if pl:
        tl["presenter"] = {k: pl.get(k) for k in ("mode", "bubble", "full", "ready", "stale", "video", "w", "h", "quality", "error")}
        tl["presenter"]["portrait"] = (pl.get("portrait") or {}).get("url")
        tl["_presenter"] = pl
    return tl


def _ai_layers(p: Project, brief: dict, scenes: list) -> bool:
    """Resolve b-roll backgrounds against the library (missing clip -> brand gradient)."""
    found = False
    for s in scenes:
        bg = s.get("background")
        if not bg:
            continue
        m = library.broll_get(bg.get("clip")) if bg.get("type") == "broll" else None
        if not m:
            s.pop("background")
            continue
        s["background"] = {"type": "broll", "clip": m["id"], "dim": bg.get("dim", 0.55), "blur": bg.get("blur", 0),
                           "w": m.get("w"), "h": m.get("h"), "duration": m.get("duration")}
        found = True
    return found


def ai_urls(p: Project, tl: dict, mode: str) -> dict:
    """Fill in where the renderer loads b-roll / presenter media from: 'http' (studio preview) or 'file' (render.py)."""
    if mode == "http":
        tl["broll_base"] = "/api/library/broll/"
    else:
        tl["broll_base"] = library.BROLL.resolve().as_uri() + "/"
    pr = tl.get("presenter")
    if pr:
        pl = tl.get("_presenter") or {}
        if pr.get("video"):
            pr["video_url"] = (f"/api/projects/{p.id}/presenter/{pr['video']}" if mode == "http"
                               else (presenter.pdir(p) / pr["video"]).resolve().as_uri())
        if pl.get("portrait_path"):
            cropped = presenter.pdir(p) / f"portrait-{pl['portrait_sha']}-{pl['w']}x{pl['h']}.png"
            if mode == "http":
                pr["portrait_url"] = pr.get("portrait")
            else:
                pr["portrait_url"] = (cropped if cropped.exists() else Path(pl["portrait_path"])).resolve().as_uri()
        pr.pop("portrait", None)
    return tl


def voiceover(p: Project, tl: dict) -> Path | None:
    """Join clips (+ silence for unvoiced scenes) into build/voiceover.wav, cached by audio_key."""
    if not tl.get("audio_key"):
        return None
    out = p.build_dir / "voiceover.wav"
    keyf = p.build_dir / "voiceover.key"
    if out.exists() and keyf.exists() and keyf.read_text().strip() == tl["audio_key"]:
        return out
    p.build_dir.mkdir(parents=True, exist_ok=True)
    V.join(tl["_parts"], out)
    keyf.write_text(tl["audio_key"])
    return out


def write_render_timeline(p: Project) -> dict:
    """build/timeline.json + voiceover.wav for render.py --project."""
    tl = timeline(p)
    wav = voiceover(p, tl)
    tl["audio"] = wav.name if wav else None
    if tl.get("broll") or tl.get("presenter"):
        ai_urls(p, tl, "file")
    clean = {k: v for k, v in tl.items() if not k.startswith("_")}
    (p.build_dir / "timeline.json").write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    return clean
