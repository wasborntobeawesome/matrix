"""The brief: the video script in JSON, written by the source AI and edited in the studio.

`SCHEMA` is published at GET /api/brief-spec. `normalize()` is deliberately lenient
(AIs make small mistakes): it fixes what it can, returns warnings for the rest,
and raises BriefError only when the input is unusable.
"""
from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared"))
from voicelib import display_text  # noqa: E402

import templates as TPL  # noqa: E402

LANGS = ["uz", "ru", "en", "ar"]
FORMATS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
FORMAT_ALIASES = {"portrait": "9:16", "vertical": "9:16", "landscape": "16:9", "horizontal": "16:9", "square": "1:1",
                  "1080x1920": "9:16", "1920x1080": "16:9", "1080x1080": "1:1", "9x16": "9:16", "16x9": "16:9",
                  "1x1": "1:1"}
# fields that may be a plain string (all languages) or a map {"uz": "...", "ru": "..."}
TEXT_FIELDS = ("say", "kicker", "headline", "sub", "callout", "button", "url", "stat")
ON_SCREEN = ("kicker", "headline", "sub", "callout", "button")   # Uzbek apostrophes are fixed on these (+ items, labels)
MUSIC_DEFAULT = {"volume_db": -20.0, "duck": True, "duck_db": -10.0, "fade_in": 1.0, "fade_out": 2.5, "start": 0.0}
LAYOUTS = ["hero", "chips", "stat", "list", "cta", "screen", "screens", "presenter"]
BROLL_PROVIDERS = ["wan22-5b", "fastwan-5b", "wan22-a14b"]     # providers.py
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
_lmap = {"type": "object", "description": "translations: {language code: text}",
         "propertyNames": {"enum": LANGS}, "additionalProperties": {"type": "string"}}


def _ls(desc: str) -> dict:
    """A text field: a plain string (used for every language) or a map {"uz": "...", "ru": "..."}."""
    return {"description": desc + " — a string (all languages) or {lang: text}", "anyOf": [_str, _lmap]}


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
            "provider": {"enum": BROLL_PROVIDERS, "description": "Video generator for this scene's clip (default: the "
                         "project's broll_provider): wan22-5b (original), fastwan-5b (fast drafts), wan22-a14b (best quality)"},
            "image": {**_str, "description": "Optional: animate this uploaded image asset (image-to-video) instead of "
                                             "generating from text only; 'prompt' then describes the motion"},
            "dim": {"type": "number", "minimum": 0, "maximum": 0.9, "default": BG_DIM_DEFAULT,
                    "description": "Darkening 0..0.9"},
            "blur": {"type": "number", "minimum": 0, "maximum": 24, "default": 0, "description": "Blur in px"},
        }},
    ],
}
_transition = {
    "description": "Scene transition: a built-in (lift = the original look, fade, slide, zoom, wipe, blur, cut) or one the "
                   "template adds (e.g. bold's punch / swipe); optionally with a duration in seconds",
    "anyOf": [{"type": "string", "pattern": "^[a-z][a-z0-9-]{1,30}$"},
              {"type": "object", "required": ["type"], "properties": {
                  "type": {"type": "string"}, "duration": {"type": "number", "minimum": 0.1, "maximum": 2.0}}}],
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
        "language": {"enum": LANGS, "description": "Main language (the first of `languages`). Voice-over and on-screen text"},
        "languages": {"type": "array", "items": {"enum": LANGS}, "uniqueItems": True, "minItems": 1,
                      "description": "Every language to make the video in (default [language]). Text fields may then be "
                                     "maps {lang: text}; each language gets its own voice-over, timing and renders"},
        "format": {"enum": list(FORMATS), "default": "9:16",
                   "description": "Main format: 9:16 = 1080x1920 (Reels/Shorts/TikTok), 1:1 = 1080x1080 (feed), 16:9 = 1920x1080"},
        "formats": {"type": "array", "items": {"enum": list(FORMATS)}, "uniqueItems": True, "minItems": 1,
                    "description": "Every output format (default [format]); a render makes one MP4 per language and format"},
        "voices": {"type": "object", "propertyNames": {"enum": LANGS},
                   "description": "Voice per language: {\"ru\": {model, voice, rate?, style?}}. Default: a Microsoft "
                                  "voice per language. `voice` is the main language's voice",
                   "additionalProperties": {"type": "object", "properties": {"model": _str, "voice": _str,
                                            "rate": {"type": "integer", "minimum": -50, "maximum": 100}, "style": _str}}},
        "music": {"type": "object", "description": "Background music from the studio's music library (ducked under the voice)",
                  "properties": {"track": {**_str, "description": "music library id (picked in the studio)"},
                                 "volume_db": {"type": "number", "minimum": -40, "maximum": 0, "default": -20,
                                               "description": "music level (dB); the track is loudness-matched first"},
                                 "duck": {"type": "boolean", "default": True},
                                 "duck_db": {"type": "number", "minimum": -30, "maximum": 0, "default": -10,
                                             "description": "extra reduction while the voice speaks (dB)"},
                                 "fade_in": {"type": "number", "minimum": 0, "maximum": 10, "default": 1},
                                 "fade_out": {"type": "number", "minimum": 0, "maximum": 15, "default": 2.5},
                                 "start": {"type": "number", "minimum": 0, "description": "start offset into the track (s)"},
                                 "mood": {**_str, "description": "suggestion for the studio user, e.g. 'calm, warm'"},
                                 "generate": {"type": "object", "description": "Generate an instrumental track locally "
                                              "(ACE-Step) when the project has none yet; 'Make video' does it in its AI stage",
                                              "properties": {"prompt": {**_str, "description": "what the music sounds like, "
                                                                        "e.g. 'warm modern tech background, soft synths, light drums'"},
                                                             "genre": _str, "mood": _str,
                                                             "bpm": {"type": "integer", "minimum": 50, "maximum": 200},
                                                             "duration": {"type": "number", "minimum": 10, "maximum": 240,
                                                                          "description": "seconds (default: the video length)"},
                                                             "seed": {"type": "integer"}}}}},
        "broll_provider": {"enum": BROLL_PROVIDERS, "default": "wan22-5b",
                           "description": "Default video generator for scene b-roll (a scene's background.provider wins)"},
        "cover": {"type": "object", "description": "Cover image moment (default: the end of the first scene)",
                  "properties": {"scene": _str, "t": {"type": "number", "minimum": 0,
                                                          "description": "seconds from the scene start"}}},
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
        "template": {**_str, "default": "aurora", "description": "Look of the video: a template id from video/templates "
                     "(built in: aurora = dark glow, clean = light minimal, bold = high-contrast social). The studio user "
                     "usually picks it; brand colours still apply"},
        "template_options": {"type": "object", "description": "Settings the chosen template declares in its manifest "
                             "(options[].key -> value)"},
        "transition": {**_transition, "description": "Default transition between scenes (default: the template's)"},
        "presenter": _presenter,
        "scenes": {
            "type": "array", "minItems": 1, "maxItems": MAX_SCENES,
            "items": {
                "type": "object",
                "required": ["id", "layout"],
                "properties": {
                    "id": {**_str, "pattern": "^[a-z0-9][a-z0-9-]*$"},
                    "layout": {"enum": LAYOUTS},
                    "say": _ls(f"Voice-over line, <= ~{SAY_WORDS_SOFT} words"),
                    "pad": {"type": "number", "minimum": 0, "maximum": 5, "description": "Silence after the line (s), default 0.4"},
                    "duration": {"type": "number", "minimum": 1, "maximum": 30, "description": "Minimum scene length (s)"},
                    "background": _background,
                    "transition": {**_transition, "description": "Transition into this scene (overrides the project default)"},
                    "kicker": _ls("hero/screen/presenter: small label above the headline"),
                    "headline": _ls("Big on-screen text, 1-5 words"),
                    "sub": _ls("Smaller line under the headline"),
                    "items": {"description": "chips/list: 2-6 short strings (each a string or {lang: text}), or one "
                                             "map of lists {lang: [strings]}; screens: 2-3 {asset, label}",
                              "anyOf": [
                                  {"type": "array", "maxItems": 6,
                                   "items": {"anyOf": [_str, _lmap, {"type": "object", "required": ["asset"],
                                                                     "properties": {"asset": _str, "label": _ls("label")}}]}},
                                  {"type": "object", "propertyNames": {"enum": LANGS},
                                   "additionalProperties": {"type": "array", "maxItems": 6, "items": _str}}]},
                    "stat": _ls("stat: the number, e.g. \"3x\", \"50%\", \"9\""),
                    "button": _ls("cta: button text (default: product name)"),
                    "url": _ls("cta: shown under the button; screen/browser: URL bar text"),
                    "device": {"enum": DEVICES, "description": "screen/screens: frame around the screenshot"},
                    "asset": {**_str, "description": "screen: screenshot or recording filename"},
                    "focus": _focus,
                    "callout": _ls("screen: short label pointing at the focus area"),
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

    langs = []
    rl = raw.get("languages")
    if isinstance(rl, str):
        rl = re.split(r"[\s,;]+", rl)
    for x in rl if isinstance(rl, list) else []:
        x = str(x or "").lower()[:2]
        if x in LANGS and x not in langs:
            langs.append(x)
        elif x and x not in LANGS:
            warn.append(f"languages: '{x}' not supported (uz|ru|en|ar) — ignored")
    lang = str(raw.get("language") or "").lower()[:2]
    if lang not in LANGS:
        if langs:
            lang = langs[0]
        else:
            warn.append(f"language '{raw.get('language')}' not supported (uz|ru|en|ar) — using en")
            lang = "en"
    if lang in langs:
        langs.remove(lang)
    langs.insert(0, lang)                         # the main language is always first
    b["language"] = lang
    b["languages"] = langs

    def fmt_of(v):
        f = str(v or "").strip().lower()
        return FORMAT_ALIASES.get(f, f)
    fmts = []
    rf = raw.get("formats")
    if isinstance(rf, str):
        rf = re.split(r"[\s,;]+", rf)
    for x in rf if isinstance(rf, list) else []:
        f = fmt_of(x)
        if f in FORMATS and f not in fmts:
            fmts.append(f)
        elif f and f not in FORMATS:
            warn.append(f"formats: '{x}' unknown (9:16|1:1|16:9) — ignored")
    fmt = fmt_of(raw.get("format") or (fmts[0] if fmts else "9:16"))
    if fmt not in FORMATS:
        warn.append(f"format '{raw.get('format')}' unknown — using 9:16")
        fmt = fmts[0] if fmts else "9:16"
    if fmt in fmts:
        fmts.remove(fmt)
    fmts.insert(0, fmt)
    b["format"] = fmt
    b["formats"] = fmts

    rb = raw.get("brand") if isinstance(raw.get("brand"), dict) else {}
    b["brand"] = {k: _hex(rb.get(k), d) for k, d in DEFAULT_BRAND.items()}
    for k in DEFAULT_BRAND:
        if rb.get(k) and b["brand"][k] != rb.get(k):
            warn.append(f"brand.{k} '{rb.get(k)}' is not a hex colour — using default")
    if _text(rb.get("logo")):
        b["brand"]["logo"] = _text(rb.get("logo"))

    rvs = raw.get("voices") if isinstance(raw.get("voices"), dict) else {}
    voices = {}
    for lg in langs:
        rv = rvs.get(lg) if isinstance(rvs.get(lg), dict) else {}
        if lg == lang and isinstance(raw.get("voice"), dict):
            rv = raw["voice"]                     # `voice` is the main language's voice (backward compatible)
        dm, dv = DEFAULT_VOICE[lg]
        model = _text(rv.get("model"), 40)
        vo = {"model": model or dm, "voice": _text(rv.get("voice"), 80) or (dv if not model or model == dm else ""),
              "rate": int(_num(rv.get("rate"), -50, 100, 0))}
        if not vo["voice"]:
            vo["voice"] = dv
        if _text(rv.get("style")):
            vo["style"] = _text(rv.get("style"), 40)
        voices[lg] = vo
    b["voice"] = voices[lang]
    b["voices"] = voices
    b["captions"] = raw.get("captions") is not False
    if "music" in raw:
        mu = _music_norm(raw.get("music"), warn)
        if mu:
            b["music"] = mu
    if isinstance(raw.get("cover"), dict):
        cv = {}
        if _text(raw["cover"].get("scene"), 60):
            cv["scene"] = slug(raw["cover"]["scene"])
        t = _num(raw["cover"].get("t"), 0, 60)
        if t is not None:
            cv["t"] = round(t, 2)
        if cv:
            b["cover"] = cv

    if raw.get("template") not in (None, ""):
        tid = slug(raw.get("template"), "")
        if not tid:
            warn.append(f"template '{raw.get('template')}' is not a valid id — using {TPL.DEFAULT}")
        else:
            b["template"] = tid
            if not TPL.exists(tid):
                warn.append(f"template '{tid}' is not installed — the video uses {TPL.DEFAULT} until it is imported")
    if isinstance(raw.get("template_options"), dict):
        to = {str(k)[:40]: v for k, v in raw["template_options"].items() if isinstance(v, (str, int, float, bool))}
        if to:
            b["template_options"] = to
            if b.get("template") and TPL.exists(b["template"]):
                try:
                    _, tw = TPL.option_values(TPL.load(b["template"]), to)
                    warn += tw
                except TPL.TemplateError:
                    pass
    if raw.get("broll_provider") not in (None, ""):
        bp = str(raw.get("broll_provider")).strip().lower()
        if bp in BROLL_PROVIDERS:
            b["broll_provider"] = bp
        else:
            warn.append(f"broll_provider '{bp}' unknown ({', '.join(BROLL_PROVIDERS)}) — using {BROLL_PROVIDERS[0]}")
    if raw.get("transition") not in (None, ""):
        tr = _transition_norm(raw.get("transition"), "transition", warn)
        if tr:
            b["transition"] = tr

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
        if k not in b and k not in ("name", "template", "template_options", "transition") and isinstance(k, str) and len(k) < 40:
            b[k] = copy.deepcopy(v)
    if len(b["languages"]) > 1:
        miss = missing_translations(b)
        for lg in b["languages"]:
            ids = [sid for sid, m in miss.items() if lg in m]
            if ids:
                warn.append(f"{lg}: missing translations in {len(ids)} scene(s) ({', '.join(ids[:6])}) — "
                            f"the main language ({b['language']}) is used there")
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
    o = {"id": sid, "layout": layout, "say": _ltext(s.get("say") or s.get("voiceover") or s.get("vo"), 400) or ""}
    for lg, line in (o["say"].items() if isinstance(o["say"], dict) else [(None, o["say"])]):
        nw = len(line.split())
        if nw > SAY_WORDS_SOFT + 6:
            warn.append(f"scene '{sid}'{f' ({lg})' if lg else ''}: voice-over has {nw} words (aim for <= {SAY_WORDS_SOFT})")
    for k in ("kicker", "headline", "sub", "callout", "button", "url"):
        v = _ltext(s.get(k), 140)
        if v:
            o[k] = v
    if s.get("stat") is not None and _ltext(s.get("stat"), 12):
        o["stat"] = _ltext(s.get("stat"), 12)
    for k, lo, hi in (("pad", 0, 5), ("duration", 1, 30), ("zoom", 1, 4), ("trim_start", 0, 600)):
        v = _num(s.get(k), lo, hi)
        if v is not None:
            o[k] = round(v, 3)
    # keep unknown keys (forward compatible), but never overwrite normalized ones
    for k, v in s.items():
        skip = ("items", "focus", "device", "asset", "stat", "voiceover", "vo", "background", "transition")
        if k not in o and k not in skip and isinstance(k, str):
            o[k] = copy.deepcopy(v)
    if "background" in s:
        bg = _background_norm(s.get("background"), sid, warn)
        if bg:
            o["background"] = bg
    if s.get("transition") not in (None, ""):
        tr = _transition_norm(s.get("transition"), f"scene '{sid}': transition", warn)
        if tr:
            o["transition"] = tr

    def check_asset(name, where):
        if assets is not None and name and name not in assets:
            warn.append(f"scene '{sid}': {where} '{name}' is not an uploaded asset")

    if layout in ("chips", "list"):
        raw_items = s.get("items") or []
        if _is_lmap(raw_items):                    # {"uz": [...], "ru": [...]} -> one map per item
            n = max(len(v) for v in raw_items.values() if isinstance(v, list)) if any(
                isinstance(v, list) for v in raw_items.values()) else 0
            raw_items = [{lg: v[i] for lg, v in raw_items.items() if isinstance(v, list) and i < len(v)} for i in range(n)]
        items = [x for x in (_ltext(x, 60) for x in raw_items if not isinstance(x, dict) or _is_lmap(x)) if x]
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
                if _ltext(x.get("label"), 40):
                    it["label"] = _ltext(x.get("label"), 40)
                check_asset(it["asset"], "item asset")
                items.append(it)
        if len(items) < 2:
            warn.append(f"scene '{sid}': screens works best with 2-3 items")
        if len(items) > 3:
            warn.append(f"scene '{sid}': screens shows at most 3 items")
        o["items"] = items[:3]
    return o


def _is_lmap(v) -> bool:
    return isinstance(v, dict) and bool(v) and all(isinstance(k, str) and k.lower()[:2] in LANGS and len(k) <= 5 for k in v)


def _ltext(v, maxlen=300):
    """A text field: str (all languages) | {lang: str} | None. A map with one language stays a map."""
    if _is_lmap(v):
        out = {}
        for k, x in v.items():
            t = _text(x, maxlen)
            if t and k.lower()[:2] not in out:
                out[k.lower()[:2]] = t
        return out or None
    return _text(v, maxlen)


def _music_norm(v, warn: list) -> dict | None:
    if not v:
        return None
    if isinstance(v, str):
        v = {"track": v}
    if not isinstance(v, dict):
        warn.append("music ignored (must be an object)")
        return None
    o = {}
    if _text(v.get("track"), 60):
        o["track"] = _text(v.get("track"), 60)
    for k, lo, hi in (("volume_db", -40, 0), ("duck_db", -30, 0), ("fade_in", 0, 10), ("fade_out", 0, 15), ("start", 0, 3600)):
        o[k] = round(_num(v.get(k), lo, hi, MUSIC_DEFAULT[k]), 2)
    o["duck"] = v.get("duck") is not False
    if _text(v.get("mood"), 80):
        o["mood"] = _text(v.get("mood"), 80)
    g = v.get("generate")
    if isinstance(g, str):
        g = {"prompt": g}
    if isinstance(g, dict):
        go = {}
        for k, n in (("prompt", 400), ("genre", 60), ("mood", 80)):
            if _text(g.get(k), n):
                go[k] = _text(g.get(k), n)
        bpm = _num(g.get("bpm"), 50, 200)
        if bpm is not None:
            go["bpm"] = int(round(bpm))
        d = _num(g.get("duration"), 10, 240)
        if d is not None:
            go["duration"] = round(d, 1)
        sd = _num(g.get("seed"), 0, 2**31 - 1)
        if sd is not None:
            go["seed"] = int(sd)
        if go.get("prompt") or go.get("genre") or go.get("mood"):
            o["generate"] = go
        else:
            warn.append("music.generate ignored (give a prompt, genre or mood)")
    return o


def pick(v, lang: str, main: str):
    """Resolve a text field for one language: map -> that language, else the main language, else any."""
    if isinstance(v, dict):
        return v.get(lang) or v.get(main) or next((x for x in v.values() if x), None)
    return v


def localize(brief: dict, lang: str | None = None) -> dict:
    """A single-language copy of the brief for voice-over, timeline and renderer.

    Text maps are resolved (missing translations fall back to the main language), `voice` becomes
    that language's voice, and for Uzbek the on-screen text gets proper oʻ gʻ ʼ (the stored brief
    keeps what the author typed; `say` is left as written for the TTS)."""
    main = brief.get("language") or "en"
    lang = lang if lang in LANGS else main
    if brief.get("_lang") == lang:
        return brief
    b = copy.deepcopy(brief)
    b["_lang"] = lang
    b["language"] = lang
    voices = b.get("voices") or {}
    if lang != main or not b.get("voice"):
        dm, dv = DEFAULT_VOICE[lang]
        b["voice"] = voices.get(lang) or {"model": dm, "voice": dv, "rate": 0}
    product = b.get("product") or ""
    fix = (lambda t: display_text(t, "uz", product) if isinstance(t, str) else t) if lang == "uz" else (lambda t: t)
    for sc in b.get("scenes", []):
        for k in TEXT_FIELDS:
            if k in sc:
                v = pick(sc[k], lang, main)
                sc[k] = fix(v) if k in ON_SCREEN else (v if v is not None else "")
        items = sc.get("items")
        if isinstance(items, list):
            out = []
            for it in items:
                if isinstance(it, dict) and not _is_lmap(it):
                    it = dict(it)
                    if "label" in it:
                        it["label"] = fix(pick(it["label"], lang, main))
                    out.append(it)
                else:
                    out.append(fix(pick(it, lang, main)))
            sc["items"] = out
    if lang == "uz":
        b["product_display"] = display_text(product, "uz", product)
    return b


def missing_translations(brief: dict) -> dict:
    """{scene id: {lang: [field, ...]}} for every language of the brief (the main language included).

    A map without that language is missing. In a multi-language brief, a plain-string `say` counts
    as missing for the other languages (it would be read out in the wrong language)."""
    langs = brief.get("languages") or [brief.get("language")]
    main = brief.get("language")
    out: dict = {}
    for sc in brief.get("scenes", []):
        miss: dict = {}
        for lg in langs:
            f = []
            for k in TEXT_FIELDS:
                v = sc.get(k)
                if isinstance(v, dict) and not (v.get(lg) or "").strip():
                    f.append(k)
                elif k == "say" and isinstance(v, str) and v.strip() and lg != main and len(langs) > 1:
                    f.append(k)
            for i, it in enumerate(sc.get("items") or []):
                if _is_lmap(it) and not it.get(lg):
                    f.append(f"items[{i + 1}]")
                elif isinstance(it, dict) and isinstance(it.get("label"), dict) and not it["label"].get(lg):
                    f.append(f"items[{i + 1}].label")
            if f:
                miss[lg] = f
        if miss:
            out[sc["id"]] = miss
    return out


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
    prov = str(v.get("provider") or "").strip().lower()
    if prov:
        if prov in BROLL_PROVIDERS:
            o["provider"] = prov
        else:
            warn.append(f"scene '{sid}': background.provider '{prov}' unknown — using the project default")
    img = _text(v.get("image"), 120)
    if img:
        o["image"] = img
    # no clip and no prompt yet (the editor's "AI clip" mode before a clip is picked): kept, renders as brand
    o["dim"] = round(_num(v.get("dim"), 0, 0.9, BG_DIM_DEFAULT), 3)
    o["blur"] = round(_num(v.get("blur"), 0, 24, 0), 2)
    return o


def _transition_norm(v, where: str, warn: list):
    """'fade' | {'type': 'fade', 'duration': 0.6}. Unknown ids are dropped with a warning; ids that only some
    installed template provides are kept (the renderer falls back to the template default elsewhere)."""
    dur = None
    if isinstance(v, dict):
        dur = _num(v.get("duration"), 0.1, 2.0)
        v = v.get("type")
    name = slug(v, "") if isinstance(v, str) else ""
    if not name:
        warn.append(f"{where}: must be a transition name like 'fade' — ignored")
        return None
    if name not in TPL.all_transition_ids():
        warn.append(f"{where}: '{name}' unknown (built in: {', '.join(TPL.BUILTIN_TRANSITIONS)}) — ignored")
        return None
    return {"type": name, "duration": round(dur, 2)} if dur else name


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
            bg = s.get("background")
            if isinstance(bg, dict) and bg.get("image") in mapping:
                bg["image"] = mapping[bg["image"]]
    return b


def blank(product: str = "My product", language: str = "en", fmt: str = "9:16", languages=None, formats=None) -> dict:
    return normalize({
        "product": product, "language": language, "format": fmt, "languages": languages or [language],
        "formats": formats or [fmt],
        "scenes": [
            {"id": "hook", "layout": "hero", "headline": f"Meet {product}", "sub": "One line about the value",
             "say": f"Meet {product}."},
            {"id": "cta", "layout": "cta", "headline": f"Try {product}", "say": f"Try {product} today.", "pad": 1.0},
        ]})[0]
