"""The brief: the video script in JSON, written by the source AI and edited in the studio.

`SCHEMA` is published at GET /api/brief-spec. `normalize()` is deliberately lenient
(AIs make small mistakes): it fixes what it can, returns warnings for the rest,
and raises BriefError only when the input is unusable.
"""
from __future__ import annotations

import copy
import re

LANGS = ["uz", "ru", "en", "ar"]
FORMATS = {"9:16": (1080, 1920), "16:9": (1920, 1080)}
LAYOUTS = ["hero", "chips", "stat", "list", "cta", "screen", "screens", "presenter"]
PRESENTER_MODES = ["corner", "full"]
PRESENTER_QUALITY = ["draft", "final"]
BG_DIM_DEFAULT = 0.55
DEVICES = ["phone", "browser", "none"]
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
MAX_SCENES = 12
SAY_WORDS_SOFT = 18

DEFAULT_BRAND = {"primary": "#7C5CFF", "accent": "#22D3A6", "background": "#0B1020"}
DEFAULT_VOICE = {
    "uz": ("edge", "uz-UZ-MadinaNeural"), "ru": ("edge", "ru-RU-SvetlanaNeural"),
    "en": ("edge", "en-US-AvaNeural"), "ar": ("edge", "ar-SA-ZariyahNeural"),
}

_str = {"type": "string"}
_focus = {
    "type": "object", "description": "Area of the screenshot to zoom into, fractions 0..1 of the image (x,y = top-left).",
    "properties": {k: {"type": "number", "minimum": 0, "maximum": 1} for k in "xywh"},
    "required": ["x", "y", "w", "h"],
}
_background = {
    "description": "Optional scene background. Default: the brand gradient. 'broll' = a looping AI video clip "
                   "from the studio's b-roll library behind the scene (darkened/blurred so text stays readable). "
                   "A source AI may give only a 'prompt' (a suggestion); the studio user generates the clip.",
    "oneOf": [
        {"type": "object", "required": ["type"], "properties": {"type": {"const": "brand"}}},
        {"type": "object", "required": ["type"], "properties": {
            "type": {"const": "broll"},
            "clip": {**_str, "description": "b-roll library id (set by the studio)"},
            "prompt": {**_str, "description": "Suggested text-to-video prompt: what the background footage shows, "
                                              "no text/logos/people's faces, e.g. 'slow drifting purple light ribbons on dark navy'"},
            "dim": {"type": "number", "minimum": 0, "maximum": 0.9, "default": BG_DIM_DEFAULT,
                    "description": "Darkening 0..0.9"},
            "blur": {"type": "number", "minimum": 0, "maximum": 24, "default": 0, "description": "Blur in px"},
        }},
    ],
}
_presenter = {
    "type": "object",
    "description": "Optional AI presenter: a talking head lip-synced to the voice-over (generated locally with "
                   "EchoMimicV3). 'corner' = a round bubble on the listed scenes; scenes with layout 'presenter' "
                   "show it full-frame in both modes.",
    "properties": {
        "enabled": {"type": "boolean", "default": False},
        "portrait": {**_str, "description": "Portrait: a studio library portrait id or an uploaded image asset"},
        "mode": {"enum": PRESENTER_MODES, "default": "corner"},
        "scenes": {"description": "Scenes with the corner bubble: \"all\" or a list of scene ids",
                   "anyOf": [{"const": "all"}, {"type": "array", "items": _str}], "default": "all"},
        "quality": {"enum": PRESENTER_QUALITY, "default": "draft", "description": "draft = 448x800 (faster), final = 576x1024"},
        "portrait_prompt": {**_str, "description": "Suggestion for generating a fictional presenter portrait"},
    },
}
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "video-studio/brief.schema.json",
    "title": "Video Studio brief",
    "description": "A 15-45 s promo video script. Each scene = one voice-over line + what is on screen.",
    "type": "object",
    "required": ["product", "language", "scenes"],
    "properties": {
        "title": {**_str, "description": "Internal project title"},
        "product": {**_str, "description": "Product / service name, shown in the header and the CTA"},
        "language": {"enum": LANGS, "description": "Voice-over and on-screen text language"},
        "format": {"enum": list(FORMATS), "default": "9:16", "description": "9:16 = 1080x1920 (Reels/Shorts/TikTok), 16:9 = 1920x1080"},
        "brand": {
            "type": "object",
            "properties": {
                "primary": {**_str, "pattern": HEX.pattern, "description": "Main brand colour, hex"},
                "accent": {**_str, "pattern": HEX.pattern, "description": "Highlight colour, hex"},
                "background": {**_str, "pattern": HEX.pattern, "description": "Dark background colour, hex"},
                "logo": {**_str, "description": "Optional logo asset filename (square PNG)"},
            },
        },
        "voice": {
            "type": "object", "description": "Optional; the studio user can change it",
            "properties": {"model": _str, "voice": _str, "rate": {"type": "integer", "minimum": -50, "maximum": 100}, "style": _str},
        },
        "captions": {"type": "boolean", "default": True},
        "presenter": _presenter,
        "scenes": {
            "type": "array", "minItems": 1, "maxItems": MAX_SCENES,
            "items": {
                "type": "object",
                "required": ["id", "layout"],
                "properties": {
                    "id": {**_str, "pattern": "^[a-z0-9][a-z0-9-]*$"},
                    "layout": {"enum": LAYOUTS},
                    "say": {**_str, "description": f"Voice-over line, <= ~{SAY_WORDS_SOFT} words"},
                    "pad": {"type": "number", "minimum": 0, "maximum": 5, "description": "Silence after the line (s), default 0.4"},
                    "duration": {"type": "number", "minimum": 1, "maximum": 30, "description": "Minimum scene length (s)"},
                    "background": _background,
                    "kicker": {**_str, "description": "hero/screen/presenter: small label above the headline"},
                    "headline": {**_str, "description": "Big on-screen text, 1-5 words"},
                    "sub": {**_str, "description": "Smaller line under the headline"},
                    "items": {"description": "chips/list: 2-6 short strings; screens: 2-3 {asset, label}",
                              "type": "array", "maxItems": 6,
                              "items": {"anyOf": [_str, {"type": "object", "required": ["asset"],
                                                         "properties": {"asset": _str, "label": _str}}]}},
                    "stat": {**_str, "description": "stat: the number, e.g. \"3x\", \"50%\", \"9\""},
                    "button": {**_str, "description": "cta: button text (default: product name)"},
                    "url": {**_str, "description": "cta: shown under the button; screen/browser: URL bar text"},
                    "device": {"enum": DEVICES, "description": "screen/screens: frame around the screenshot"},
                    "asset": {**_str, "description": "screen: screenshot or recording filename"},
                    "focus": _focus,
                    "callout": {**_str, "description": "screen: short label pointing at the focus area"},
                    "zoom": {"type": "number", "minimum": 1, "maximum": 4, "description": "screen: max zoom into focus (default auto, <= 2.4)"},
                    "trim_start": {"type": "number", "minimum": 0, "description": "recording: start offset (s)"},
                },
            },
        },
    },
}


class BriefError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def slug(s: str, fallback: str = "scene") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s or "").lower()).strip("-")
    return s[:40] or fallback


def _text(v, maxlen=300) -> str | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:maxlen] if v else None


def _num(v, lo, hi, default=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:  # NaN
        return default
    return min(hi, max(lo, x))


def _hex(v, default):
    v = _text(v, 9)
    return v if v and HEX.match(v) else default


def normalize(raw, assets: dict | None = None) -> tuple[dict, list[str]]:
    """Return (clean brief, warnings). `assets` = {name: meta} of the project, for checks."""
    if not isinstance(raw, dict):
        raise BriefError(["brief must be a JSON object"])
    warn: list[str] = []
    b: dict = {}
    b["product"] = _text(raw.get("product") or raw.get("name") or (raw.get("brand") or {}).get("name"), 60) or "Product"
    if not (raw.get("product") or raw.get("name")):
        warn.append("product name missing — using 'Product'")
    b["title"] = _text(raw.get("title"), 120) or f"{b['product']} promo"

    lang = str(raw.get("language") or "").lower()[:2]
    if lang not in LANGS:
        warn.append(f"language '{raw.get('language')}' not supported (uz|ru|en|ar) — using en")
        lang = "en"
    b["language"] = lang

    fmt = str(raw.get("format") or "9:16").strip().lower()
    fmt = {"portrait": "9:16", "vertical": "9:16", "landscape": "16:9", "horizontal": "16:9",
           "1080x1920": "9:16", "1920x1080": "16:9"}.get(fmt, fmt)
    if fmt not in FORMATS:
        warn.append(f"format '{raw.get('format')}' unknown — using 9:16")
        fmt = "9:16"
    b["format"] = fmt

    rb = raw.get("brand") if isinstance(raw.get("brand"), dict) else {}
    b["brand"] = {k: _hex(rb.get(k), d) for k, d in DEFAULT_BRAND.items()}
    for k in DEFAULT_BRAND:
        if rb.get(k) and b["brand"][k] != rb.get(k):
            warn.append(f"brand.{k} '{rb.get(k)}' is not a hex colour — using default")
    if _text(rb.get("logo")):
        b["brand"]["logo"] = _text(rb.get("logo"))

    rv = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    dm, dv = DEFAULT_VOICE[lang]
    b["voice"] = {"model": _text(rv.get("model"), 40) or dm, "voice": _text(rv.get("voice"), 80) or dv,
                  "rate": int(_num(rv.get("rate"), -50, 100, 0))}
    if _text(rv.get("style")):
        b["voice"]["style"] = _text(rv.get("style"), 40)
    b["captions"] = raw.get("captions") is not False

    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise BriefError(["brief needs a non-empty 'scenes' array"])
    if len(scenes) > MAX_SCENES:
        warn.append(f"{len(scenes)} scenes — keeping the first {MAX_SCENES}")
        scenes = scenes[:MAX_SCENES]
    out, seen = [], set()
    for i, s in enumerate(scenes):
        if not isinstance(s, dict):
            warn.append(f"scene {i + 1} is not an object — skipped")
            continue
        out.append(_scene(s, i, seen, warn, assets))
    if not out:
        raise BriefError(["no usable scenes"])
    b["scenes"] = out
    if "presenter" in raw:
        pr = _presenter_norm(raw.get("presenter"), [x["id"] for x in out], warn)
        if pr is not None:
            b["presenter"] = pr
    # keep extra top-level keys an AI may add (notes etc.), but not ours twice
    for k, v in raw.items():
        if k not in b and k not in ("name",) and isinstance(k, str) and len(k) < 40:
            b[k] = copy.deepcopy(v)
    if assets is not None:
        logo = b["brand"].get("logo")
        if logo and logo not in assets:
            warn.append(f"brand.logo '{logo}' is not an uploaded asset")
    return b, warn


def _scene(s: dict, i: int, seen: set, warn: list, assets) -> dict:
    sid = slug(s.get("id") or f"s{i + 1}", f"s{i + 1}")
    base, n = sid, 2
    while sid in seen:
        sid, n = f"{base}-{n}", n + 1
    seen.add(sid)
    layout = str(s.get("layout") or "hero").lower()
    if layout not in LAYOUTS:
        warn.append(f"scene '{sid}': layout '{layout}' unknown — using hero")
        layout = "hero"
    o = {"id": sid, "layout": layout, "say": _text(s.get("say") or s.get("voiceover") or s.get("vo"), 400) or ""}
    nw = len(o["say"].split())
    if nw > SAY_WORDS_SOFT + 6:
        warn.append(f"scene '{sid}': voice-over has {nw} words (aim for <= {SAY_WORDS_SOFT})")
    for k in ("kicker", "headline", "sub", "callout", "button", "url"):
        v = _text(s.get(k), 140)
        if v:
            o[k] = v
    if s.get("stat") is not None and _text(s.get("stat"), 12):
        o["stat"] = _text(s.get("stat"), 12)
    for k, lo, hi in (("pad", 0, 5), ("duration", 1, 30), ("zoom", 1, 4), ("trim_start", 0, 600)):
        v = _num(s.get(k), lo, hi)
        if v is not None:
            o[k] = round(v, 3)
    # keep unknown keys (forward compatible), but never overwrite normalized ones
    for k, v in s.items():
        skip = ("items", "focus", "device", "asset", "stat", "voiceover", "vo", "background")
        if k not in o and k not in skip and isinstance(k, str):
            o[k] = copy.deepcopy(v)
    if "background" in s:
        bg = _background_norm(s.get("background"), sid, warn)
        if bg:
            o["background"] = bg

    def check_asset(name, where):
        if assets is not None and name and name not in assets:
            warn.append(f"scene '{sid}': {where} '{name}' is not an uploaded asset")

    if layout in ("chips", "list"):
        items = [x for x in (_text(x, 60) for x in (s.get("items") or []) if not isinstance(x, dict)) if x]
        if not items:
            warn.append(f"scene '{sid}': {layout} needs 'items'")
        o["items"] = items[:6]
    if layout == "stat" and "stat" not in o:
        warn.append(f"scene '{sid}': stat layout needs 'stat'")
        o["stat"] = "1"
    if layout in ("screen", "screens"):
        dev = str(s.get("device") or "").lower()
        if dev not in DEVICES:
            dev = "phone"
            if s.get("device"):
                warn.append(f"scene '{sid}': device '{s.get('device')}' unknown — using phone")
        o["device"] = dev
    if layout == "screen":
        o["asset"] = _text(s.get("asset"), 120) or ""
        if not o["asset"]:
            warn.append(f"scene '{sid}': screen needs 'asset'")
        check_asset(o["asset"], "asset")
        f = s.get("focus")
        if isinstance(f, dict):
            fx, fy = _num(f.get("x"), 0, 1, 0), _num(f.get("y"), 0, 1, 0)
            fw, fh = _num(f.get("w"), 0, 1, 0), _num(f.get("h"), 0, 1, 0)
            fw, fh = min(fw, 1 - fx), min(fh, 1 - fy)
            if fw > 0.01 and fh > 0.01:
                o["focus"] = {"x": round(fx, 4), "y": round(fy, 4), "w": round(fw, 4), "h": round(fh, 4)}
            else:
                warn.append(f"scene '{sid}': focus ignored (w/h must be > 0, fractions 0..1)")
    if layout == "screens":
        items = []
        for x in s.get("items") or []:
            if isinstance(x, str):
                x = {"asset": x}
            if isinstance(x, dict) and _text(x.get("asset"), 120):
                it = {"asset": _text(x.get("asset"), 120)}
                if _text(x.get("label"), 40):
                    it["label"] = _text(x.get("label"), 40)
                check_asset(it["asset"], "item asset")
                items.append(it)
        if len(items) < 2:
            warn.append(f"scene '{sid}': screens works best with 2-3 items")
        if len(items) > 3:
            warn.append(f"scene '{sid}': screens shows at most 3 items")
        o["items"] = items[:3]
    return o


def _background_norm(v, sid: str, warn: list) -> dict | None:
    """{'type': 'brand'} (default, dropped) | {'type': 'broll', 'clip'?, 'prompt'?, 'dim', 'blur'}."""
    if v is None or v == "brand":
        return None
    if isinstance(v, str):  # a bare prompt string is taken as a suggestion
        v = {"type": "broll", "prompt": v}
    if not isinstance(v, dict):
        warn.append(f"scene '{sid}': background ignored (must be an object)")
        return None
    typ = str(v.get("type") or ("broll" if v.get("clip") or v.get("prompt") else "brand")).lower()
    if typ in ("brand", "gradient", "default"):
        return None
    if typ not in ("broll", "video", "ai"):
        warn.append(f"scene '{sid}': background type '{typ}' unknown — using the brand gradient")
        return None
    o = {"type": "broll"}
    clip = _text(v.get("clip"), 60)
    if clip:
        o["clip"] = clip
    prompt = _text(v.get("prompt"), 600)
    if prompt:
        o["prompt"] = prompt
    # no clip and no prompt yet (the editor's "AI clip" mode before a clip is picked): kept, renders as brand
    o["dim"] = round(_num(v.get("dim"), 0, 0.9, BG_DIM_DEFAULT), 3)
    o["blur"] = round(_num(v.get("blur"), 0, 24, 0), 2)
    return o


def _presenter_norm(v, scene_ids: list, warn: list) -> dict | None:
    if v is None or v is False:
        return None
    if v is True:
        v = {"enabled": True}
    if not isinstance(v, dict):
        warn.append("presenter ignored (must be an object)")
        return None
    o = {"enabled": bool(v.get("enabled", True))}
    if _text(v.get("portrait"), 120):
        o["portrait"] = _text(v.get("portrait"), 120)
    mode = str(v.get("mode") or "corner").lower()
    if mode not in PRESENTER_MODES:
        warn.append(f"presenter.mode '{mode}' unknown — using corner")
        mode = "corner"
    o["mode"] = mode
    sc = v.get("scenes", "all")
    if isinstance(sc, list):
        ids = [slug(x) for x in sc if isinstance(x, str)]
        bad = [x for x in ids if x not in scene_ids]
        if bad:
            warn.append(f"presenter.scenes: unknown scene id(s) {', '.join(bad)} — ignored")
        o["scenes"] = [x for x in ids if x in scene_ids]
    else:
        o["scenes"] = "all"
    q = str(v.get("quality") or "draft").lower()
    o["quality"] = q if q in PRESENTER_QUALITY else "draft"
    if _text(v.get("portrait_prompt"), 600):
        o["portrait_prompt"] = _text(v.get("portrait_prompt"), 600)
    return o


def rename_assets(brief: dict, mapping: dict) -> dict:
    """Rewrite asset references (e.g. demo.mp4 -> demo.webm after transcoding)."""
    if not mapping:
        return brief
    b = copy.deepcopy(brief)
    if b.get("brand", {}).get("logo") in mapping:
        b["brand"]["logo"] = mapping[b["brand"]["logo"]]
    if isinstance(b.get("presenter"), dict) and b["presenter"].get("portrait") in mapping:
        b["presenter"]["portrait"] = mapping[b["presenter"]["portrait"]]
    for s in b.get("scenes", []):
        if isinstance(s, dict):
            if s.get("asset") in mapping:
                s["asset"] = mapping[s["asset"]]
            for it in s.get("items") or []:
                if isinstance(it, dict) and it.get("asset") in mapping:
                    it["asset"] = mapping[it["asset"]]
    return b


def blank(product: str = "My product", language: str = "en", fmt: str = "9:16") -> dict:
    return normalize({
        "product": product, "language": language, "format": fmt,
        "scenes": [
            {"id": "hook", "layout": "hero", "headline": f"Meet {product}", "sub": "One line about the value",
             "say": f"Meet {product}."},
            {"id": "cta", "layout": "cta", "headline": f"Try {product}", "say": f"Try {product} today.", "pad": 1.0},
        ]})[0]
