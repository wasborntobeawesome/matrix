"""Voice-over + timeline for a project (uses video/shared/voicelib.py).

Per-scene clips are cached in build/voice/<hash>.wav where hash covers
text + model + voice + rate + language + style, so editing one line only
re-synthesises that line. Scenes without a clip get an estimated length and
silence, so preview and render always work (even when Ovoz is down).

Multi-language: every function takes a `lang` (default: the brief's main language) and works
on `brief.localize(brief, lang)` — text maps resolved, that language's voice, Uzbek on-screen
apostrophes fixed. Word timing comes from align.py (faster-whisper) when it is available.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import align
import library
import music
import presenter
from brief import FORMATS, localize
from store import Project

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
import voicelib as V  # noqa: E402

# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and costs ~2 s per call
OVOZ = os.environ.get("OVOZ_URL", "http://127.0.0.1:8000")
FPS = 30
MIN_SCENE = {"screen": 3.2, "screens": 3.4, "stat": 3.0, "presenter": 2.6}


def fmt_slug(fmt: str) -> str:
    return (fmt or "9:16").replace(":", "x")


def langs_of(brief: dict) -> list[str]:
    return list(brief.get("languages") or [brief.get("language") or "en"])


def formats_of(brief: dict) -> list[str]:
    return list(brief.get("formats") or [brief.get("format") or "9:16"])


def say_of(scene: dict) -> str:
    s = scene.get("say") or ""
    if isinstance(s, dict):          # raw (not localized) brief: never happens via localize()
        s = next((x for x in s.values() if x), "")
    return s.strip()


def voice_key(brief: dict, scene: dict) -> str | None:
    text = say_of(scene)
    if not text:
        return None
    v = brief.get("voice", {})
    raw = json.dumps([text, v.get("model"), v.get("voice"), int(v.get("rate") or 0), brief.get("language"),
                      v.get("style")], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def voice_file(p: Project, key: str) -> Path:
    return p.build_dir / "voice" / f"{key}.wav"


def scene_voice(p: Project, brief: dict, scene: dict, lang: str | None = None) -> Path | None:
    """The scene's clip, if generated. `brief`/`scene` may be raw (then `lang` picks the language)."""
    if not brief.get("_lang") or (lang and brief.get("_lang") != lang):
        brief = localize(brief, lang)
        scene = next((s for s in brief["scenes"] if s["id"] == scene["id"]), scene)
    k = voice_key(brief, scene)
    if k and voice_file(p, k).exists():
        return voice_file(p, k)
    return None


def voiced_map(p: Project, brief: dict | None = None) -> dict:
    """{lang: {scene id: bool}} for every language of the project."""
    brief = brief or p.brief
    out = {}
    for lg in langs_of(brief):
        lb = localize(brief, lg)
        out[lg] = {s["id"]: bool(scene_voice(p, lb, s)) for s in lb["scenes"]}
    return out


def todo_lines(p: Project, langs: list[str] | None = None, scene_ids: list[str] | None = None,
               force: bool = False) -> list[tuple[str, dict, dict]]:
    """[(lang, localized brief, scene)] that still need a clip."""
    brief = p.brief
    out = []
    for lg in langs or langs_of(brief):
        lb = localize(brief, lg)
        for s in lb["scenes"]:
            if not say_of(s) or (scene_ids is not None and s["id"] not in scene_ids):
                continue
            if force or not voice_file(p, voice_key(lb, s)).exists():
                out.append((lg, lb, s))
    return out


def generate(p: Project, scene_ids: list[str] | None = None, force: bool = False, progress=None, api: str = OVOZ,
             langs: list[str] | None = None) -> dict:
    """Synthesise missing clips for every language (or `langs` / `scene_ids` only). Raises voicelib.TTSError."""
    todo = todo_lines(p, langs, scene_ids, force)
    total = sum(1 for lg in (langs or langs_of(p.brief)) for s in localize(p.brief, lg)["scenes"]
                if say_of(s) and (scene_ids is None or s["id"] in scene_ids))
    made = []
    for i, (lg, lb, s) in enumerate(todo):
        if progress:
            progress(i / max(1, len(todo)), f"voice: {lg} · {s['id']}")
        f = voice_file(p, voice_key(lb, s))
        f.parent.mkdir(parents=True, exist_ok=True)
        v = lb["voice"]
        audio = V.tts(say_of(s), v["model"], v["voice"], lg, v.get("rate", 0), v.get("style"), api=api)
        V.to_wav(audio, f)
        made.append(f"{lg}:{s['id']}" if len(langs_of(p.brief)) > 1 else s["id"])
    if progress:
        progress(1.0, "voice: done")
    return {"generated": made, "total": total}


def align_all(p: Project, langs: list[str] | None = None, progress=None) -> dict:
    """Word timing (faster-whisper on the CPU) for every voiced line. Cached per audio hash."""
    brief = p.brief
    jobs = []
    for lg in langs or langs_of(brief):
        lb = localize(brief, lg)
        for s in lb["scenes"]:
            wav = scene_voice(p, lb, s)
            if wav:
                jobs.append((lg, lb, s, wav))
    res = {"aligned": 0, "estimated": 0, "lines": len(jobs)}
    for i, (lg, lb, s, wav) in enumerate(jobs):
        if progress:
            progress(i / max(1, len(jobs)), f"word timing: {lg} · {s['id']}")
        r = align.timing(wav, say_of(s), lg, lb.get("product") or "", run=True)
        res["aligned" if r and r.get("method") == "aligned" else "estimated"] += 1
    if progress:
        progress(1.0, "word timing: done")
    return res


def timeline(p: Project, brief: dict | None = None, lang: str | None = None, fmt: str | None = None,
             align_mode: str = "cache") -> dict:
    """Timeline for the renderer: scene times (real clip lengths or estimates), captions, assets.

    align_mode: "cache" = use cached word timing only, "queue" = also queue missing alignments in
    the background (live preview), "run" = align now (renders)."""
    raw = brief or p.brief
    main = raw.get("language")
    lb = localize(raw, lang)
    lang = lb["language"]
    fmt = fmt if fmt in FORMATS else lb.get("format")
    W, H = FORMATS.get(fmt, FORMATS["9:16"])
    product = lb.get("product") or ""
    assets = p.assets()
    rate = int(lb.get("voice", {}).get("rate") or 0)
    t = 0.0
    scenes, captions, parts = [], [], []
    n = len(lb["scenes"])
    voiced = 0
    with_say = 0
    for i, s in enumerate(lb["scenes"]):
        last = i == n - 1
        wav = scene_voice(p, lb, s)
        say = say_of(s)
        if say:
            with_say += 1
        timing = None
        if wav:
            speech = V.duration(wav)
            voiced += 1
            timing = align.timing(wav, say, lang, product, run=align_mode == "run")
            if timing is None and align_mode == "queue":
                align.request(wav, say, lang, product)
        else:
            speech = V.estimate_speech(say, lang, rate)
        pad = s.get("pad", 1.0 if last else 0.4)
        length = max(speech + pad, s.get("duration") or 0, MIN_SCENE.get(s["layout"], 2.2))
        if s["layout"] == "screen" and assets.get(s.get("asset"), {}).get("type") == "video" and not s.get("duration"):
            vd = assets[s["asset"]].get("duration") or 0
            length = max(length, min(vd - float(s.get("trim_start") or 0) + 0.5, 10.0))
        length = round(length, 3)
        method = "aligned" if timing and timing.get("method") == "aligned" else "estimated"
        scenes.append({**s, "start": round(t, 3), "end": round(t + length, 3), "speech_end": round(t + speech, 3),
                       "voiced": bool(wav), "timing": method})
        if say:
            if method == "aligned":
                words = [{"w": w["w"], "start": t + w["start"], "end": t + w["end"]} for w in timing["words"]]
                captions += V.chunks_from_words(words, end=t + speech)
            else:
                captions += V.caption_chunks(say, t, speech, lang=lang, product=product)
        parts.append((wav, speech, length - speech))
        t += length
    duration = round(t, 3)
    used = set()
    for s in lb["scenes"]:
        if s.get("asset"):
            used.add(s["asset"])
        for it in s.get("items") or []:
            if isinstance(it, dict) and it.get("asset"):
                used.add(it["asset"])
    if lb["brand"].get("logo"):
        used.add(lb["brand"]["logo"])
    has_broll = _ai_layers(p, lb, scenes)
    tl = {
        "title": lb.get("title"), "language": lang, "format": fmt,
        "width": W, "height": H, "fps": FPS, "duration": duration, "frames": round(duration * FPS),
        "brand": {"name": lb.get("product_display") or product, **lb["brand"]},
        "voice": lb.get("voice"),
        "audio": None, "assets_dir": "../assets",
        "assets": {k: {kk: vv for kk, vv in m.items() if kk in ("type", "w", "h", "duration")}
                   for k, m in assets.items() if k in used},
        "scenes": scenes, "captions": captions, "captions_off": lb.get("captions") is False,
        "voiced": voiced, "voice_needed": with_say,
    }
    if lang == "ar":
        tl["rtl"] = True
    if len(langs_of(raw)) > 1:
        tl["languages"] = langs_of(raw)
    if len(formats_of(raw)) > 1:
        tl["formats"] = formats_of(raw)
    tl["timing"] = {"aligned": sum(1 for s in scenes if s["voiced"] and s["timing"] == "aligned"),
                    "voiced": voiced, "model": align.model_name()}
    key = hashlib.sha1(json.dumps([[str(w) if w else None, round(sp, 3), round(pd, 3)] for w, sp, pd in parts]).encode()).hexdigest()[:16]
    tl["audio_key"] = key if voiced else None
    tl["_parts"] = parts
    tl["_lang_main"] = lang == main
    ms = music.settings(raw.get("music"))
    if ms:
        tl["_music"] = ms
    if has_broll:
        tl["broll"] = True
    pl = presenter.plan(p, lb, tl)
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
    """Join clips (+ silence for unvoiced scenes) into one WAV, cached by audio_key.

    The main language keeps build/voiceover.wav (the presenter lip-syncs to it); other languages
    use build/voiceover-<lang>.wav."""
    if not tl.get("audio_key"):
        return None
    main = tl.get("_lang_main", True)
    name = "voiceover" if main else f"voiceover-{tl['language']}"
    out = p.build_dir / f"{name}.wav"
    keyf = p.build_dir / f"{name}.key"
    if out.exists() and keyf.exists() and keyf.read_text().strip() == tl["audio_key"]:
        return out
    p.build_dir.mkdir(parents=True, exist_ok=True)
    V.join(tl["_parts"], out)
    keyf.write_text(tl["audio_key"])
    return out


def music_mix(p: Project, tl: dict, wav: Path | None) -> tuple[Path | None, dict | None]:
    """Voice-over + background music, loudness-normalised (cached). (None, None) without music."""
    ms = tl.get("_music")
    if not ms:
        return None, None
    key = music.mix_key(wav, ms, tl["duration"])
    out = p.build_dir / f"mix-{tl['language']}.wav"
    meta = p.build_dir / f"mix-{tl['language']}.json"
    if out.exists() and meta.exists():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            if m.get("key") == key:
                return out, m
        except ValueError:
            pass
    stats = music.mix(wav, ms, tl["duration"], out)
    stats["key"] = key
    meta.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return out, stats


def preview_music(p: Project, tl: dict) -> dict | None:
    ms = tl.get("_music")
    if not ms:
        return None
    wav = voiceover(p, tl) if tl.get("audio_key") else None
    return music.preview_info(ms, wav, tl["duration"])


def write_render_timeline(p: Project, lang: str | None = None, fmt: str | None = None) -> dict:
    """build/timeline-<lang>-<fmt>.json + its audio (voice-over or the music mix) for render.py --project."""
    tl = timeline(p, lang=lang, fmt=fmt, align_mode="run")
    wav = voiceover(p, tl)
    mixed, stats = music_mix(p, tl, wav)
    tl["audio"] = (mixed or wav).name if (mixed or wav) else None
    if mixed and wav:
        tl["analysis_audio"] = wav.name          # waveforms follow the voice, not the music
    if stats:
        tl["music"] = {k: stats.get(k) for k in ("track", "lufs", "true_peak", "music_gain_db", "voice_lufs")}
    if tl.get("broll") or tl.get("presenter"):
        ai_urls(p, tl, "file")
    clean = {k: v for k, v in tl.items() if not k.startswith("_")}
    name = f"timeline-{tl['language']}-{fmt_slug(tl['format'])}.json"
    (p.build_dir / name).write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    clean["_file"] = name
    return clean
