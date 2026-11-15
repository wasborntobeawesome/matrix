"""Render the shared promo timeline with MoviePy 2 + Pillow.

    video/.venv/Scripts/python video/moviepy/render.py [--out video/out/moviepy.mp4]
        [--workers N] [--frames 0,120,300 --preview-dir DIR]

Everything is drawn with Pillow into one custom MoviePy VideoClip:
  * static layers (text, cards, chips, pills, glows) are rendered once as RGBA sprites,
  * each frame = animated low-res gradient background (numpy, upscaled) + sprites
    composited with per-element transforms (translate / scale / opacity / blur),
  * a few live layers are drawn per frame (audio-reactive waveform from the voice-over,
    count-up number + progress ring, caption pill that morphs between chunks).
Frames are produced by a process pool (in order) and fed to MoviePy, which pipes them
to ffmpeg (libx264 / yuv420p) and muxes the voice-over as AAC.

Optional extras (see setup_deps.py): _dll/fribidi-0.dll enables Pillow's raqm layout
(proper Arabic shaping + RTL); _vendor/ holds arabic-reshaper + python-bidi as fallback.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
import wave
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parent
SHARED = VIDEO / "shared"

# --- optional native/text deps living in this folder ------------------------------------
if (HERE / "_dll").is_dir():  # fribidi for Pillow's bundled libraqm (loaded via PATH)
    os.environ["PATH"] = str(HERE / "_dll") + os.pathsep + os.environ.get("PATH", "")
if (HERE / "_vendor").is_dir():
    sys.path.insert(0, str(HERE / "_vendor"))
if (VIDEO / "bin" / "ffmpeg.exe").exists():
    os.environ.setdefault("FFMPEG_BINARY", str(VIDEO / "bin" / "ffmpeg.exe"))

import numpy as np  # noqa: E402
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, features  # noqa: E402

RAQM = features.check("raqm")
try:
    import arabic_reshaper  # type: ignore
    from bidi import get_display  # type: ignore
except Exception:  # pragma: no cover
    arabic_reshaper = None

INTER = str(SHARED / "fonts" / "Inter.ttf")
ARABIC = str(SHARED / "fonts" / "NotoSansArabic.ttf")
EMOJI = "C:/Windows/Fonts/seguiemj.ttf"
HAS_EMOJI = Path(EMOJI).exists()

TL: dict = {}
W = H = FPS = 0
DUR = 0.0
C: dict = {}


def hex2rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ======================================================================== easing / math
def clamp(x, a=0.0, b=1.0):
    return a if x < a else b if x > b else x


def ease_out_cubic(p):
    return 1 - (1 - p) ** 3


def ease_in_cubic(p):
    return p ** 3


def ease_in_out(p):
    return p * p * (3 - 2 * p)


def ease_out_back(p, s=1.70158):
    p -= 1
    return 1 + (s + 1) * p ** 3 + s * p ** 2


def ease_out_expo(p):
    return 1.0 if p >= 1 else 1 - 2 ** (-10 * p)


def lerp(a, b, k):
    return a + (b - a) * k


# ======================================================================== fonts & text
@lru_cache(maxsize=None)
def font(kind: str, size: int, wght: int = 700) -> ImageFont.FreeTypeFont:
    engine = ImageFont.Layout.RAQM if RAQM else ImageFont.Layout.BASIC
    if kind == "emoji":
        return ImageFont.truetype(EMOJI, size, layout_engine=engine)
    f = ImageFont.truetype(INTER if kind == "inter" else ARABIC, size, layout_engine=engine)
    try:
        axes = f.get_variation_axes()
        vals = []
        for ax in axes:
            name = ax["name"].decode() if isinstance(ax["name"], bytes) else ax["name"]
            if name.lower().startswith("weight"):
                vals.append(wght)
            elif name.lower().startswith("optical"):
                vals.append(clamp(size / 2.5, ax["minimum"], ax["maximum"]))
            else:
                vals.append(ax["default"])
        f.set_variation_by_axes(vals)
    except Exception:
        pass
    return f


def char_kind(ch: str):
    o = ord(ch)
    if 0x0600 <= o <= 0x06FF or 0x0750 <= o <= 0x077F or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
        return "arabic"
    if o >= 0x1F000 or 0x2600 <= o <= 0x27BF or o in (0xFE0F, 0x200D, 0x2B50):
        return "emoji" if HAS_EMOJI else "inter"
    if ch.isspace():
        return None
    return "inter"


def split_runs(text: str) -> list[list]:
    runs: list[list] = []
    for ch in text:
        k = char_kind(ch)
        if k is None:
            k = runs[-1][0] if runs and runs[-1][0] != "emoji" else "inter"
        if runs and runs[-1][0] == k:
            runs[-1][1] += ch
        else:
            runs.append([k, ch])
    return runs


def _run_args(kind: str, txt: str):
    """Return (text_to_draw, extra kwargs) for one run."""
    if kind == "arabic":
        if RAQM:
            return txt, {"direction": "rtl", "language": "ar"}
        if arabic_reshaper is not None:
            return get_display(arabic_reshaper.reshape(txt)), {}
        return txt, {}
    if kind == "emoji":
        return txt.replace("\ufe0f", ""), ({"language": "und-Zsye"} if RAQM else {})
    return txt, {}


def _run_font(kind, size, wght):
    if kind == "emoji":
        return font("emoji", int(size * 0.9))
    return font(kind, size, wght)


def line_width(text: str, size: int, wght: int, tracking: float = 0) -> float:
    w = 0.0
    for kind, txt in split_runs(text):
        f = _run_font(kind, size, wght)
        s, kw = _run_args(kind, txt)
        w += f.getlength(s, **kw)
        if tracking and kind == "inter":
            w += tracking * len(s)
    return w


def draw_line(mask: Image.Image, color: Image.Image, x: float, base: float, text: str, size: int,
              wght: int, tracking: float = 0) -> None:
    dm, dc = ImageDraw.Draw(mask), ImageDraw.Draw(color)
    for kind, txt in split_runs(text):
        f = _run_font(kind, size, wght)
        s, kw = _run_args(kind, txt)
        if kind == "emoji":
            dc.text((x, base), s, font=f, anchor="ls", embedded_color=True, **kw)
            x += f.getlength(s, **kw)
        elif tracking and kind == "inter":
            for ch in s:
                dm.text((x, base), ch, font=f, fill=255, anchor="ls")
                x += f.getlength(ch) + tracking
        else:
            dm.text((x, base), s, font=f, fill=255, anchor="ls", **kw)
            x += f.getlength(s, **kw)


def wrap(text: str, size: int, wght: int, max_w: float, tracking=0) -> list[str]:
    text = text.replace(" —", " —").replace(" →", " →")  # never start a line with — or →
    words, lines, cur = text.split(" "), [], ""
    for w in words:
        cand = (cur + " " + w).strip()
        if cur and line_width(cand, size, wght, tracking) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        lines.append(cur)
    return lines


def lin_grad(w: int, h: int, c1, c2, angle=90.0) -> Image.Image:
    a = math.radians(angle)
    x = np.linspace(0, 1, max(w, 1))[None, :]
    y = np.linspace(0, 1, max(h, 1))[:, None]
    d = x * math.cos(a) + y * math.sin(a)
    d = (d - d.min()) / (d.max() - d.min() + 1e-9)
    c = np.asarray(c1, float)[None, None, :] * (1 - d[..., None]) + np.asarray(c2, float)[None, None, :] * d[..., None]
    return Image.fromarray(np.clip(c, 0, 255).astype(np.uint8))


def colorize(mask: Image.Image, fill) -> Image.Image:
    if isinstance(fill, Image.Image):
        img = fill.convert("RGBA")
    else:
        img = Image.new("RGBA", mask.size, tuple(fill[:3]) + (255,))
    img.putalpha(mask)
    return img


def mul_alpha(img: Image.Image, k: float) -> Image.Image:
    if k >= 0.999:
        return img
    out = img.copy()
    out.putalpha(img.getchannel("A").point([int(i * k + 0.5) for i in range(256)]))
    return out


class Spr:
    """An RGBA sprite whose visual content is centred (symmetric padding)."""

    def __init__(self, img: Image.Image, cw: float | None = None, ch: float | None = None):
        self.img = img
        self.cw = img.width if cw is None else cw
        self.ch = img.height if ch is None else ch


def text_spr(text: str, size: int, wght=700, fill=(255, 255, 255), max_w=920, max_lines=3, lh=1.06,
             tracking=0.0, glow=None, min_size=24, shadow=False) -> Spr:
    """Multi-line centred text -> sprite. fill: rgb tuple or ('grad', c1, c2, angle)."""
    while True:
        lines = wrap(text, size, wght, max_w, tracking)
        widths = [line_width(l, size, wght, tracking) for l in lines]
        if (len(lines) <= max_lines and max(widths) <= max_w) or size <= min_size:
            break
        size -= 2
    if len(lines) > 1:  # balance line lengths: shrink the wrap width while the line count holds
        lo, hi = max(widths) * 0.5, max(widths)
        for _ in range(12):
            mid = (lo + hi) / 2
            if len(wrap(text, size, wght, mid, tracking)) <= len(lines):
                hi = mid
            else:
                lo = mid
        lines = wrap(text, size, wght, hi, tracking)
        widths = [line_width(l, size, wght, tracking) for l in lines]
    f = font("inter", size, wght)
    asc, desc = f.getmetrics()
    step = size * lh
    pad = int(size * 0.35) + (int(glow[1] * 2.5) if glow else 0) + (24 if shadow else 0)
    cw = int(max(widths)) + 2
    ch = int(asc + desc + step * (len(lines) - 1))
    bw, bh = cw + 2 * pad, ch + 2 * pad
    mask = Image.new("L", (bw, bh), 0)
    color = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    for i, (l, lw) in enumerate(zip(lines, widths)):
        draw_line(mask, color, pad + (cw - lw) / 2, pad + asc + i * step, l, size, wght, tracking)
    if isinstance(fill, tuple) and fill and fill[0] == "grad":
        _, c1, c2, ang = fill
        fill_img = Image.new("RGB", (bw, bh))
        fill_img.paste(lin_grad(cw, ch, c1, c2, ang), (pad, pad))
        txt = colorize(mask, fill_img)
    else:
        txt = colorize(mask, fill)
    out = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    if shadow:
        sh = colorize(mask.filter(ImageFilter.GaussianBlur(size * 0.12)).point(lambda v: int(v * 0.55)), (0, 0, 0))
        out.alpha_composite(sh, (0, int(size * 0.04)))
    if glow:
        gcol, grad, gk = glow
        g = mask.filter(ImageFilter.GaussianBlur(grad)).point(lambda v: int(min(255, v * gk)))
        out.alpha_composite(colorize(g, gcol))
    out.alpha_composite(txt)
    out.alpha_composite(color)
    return Spr(out, cw, ch)


# ======================================================================== shapes
def rr_mask(w: int, h: int, r: float, ss: int = 4, inset: float = 0) -> Image.Image:
    m = Image.new("L", (w * ss, h * ss), 0)
    i = inset * ss
    ImageDraw.Draw(m).rounded_rectangle((i, i, w * ss - 1 - i, h * ss - 1 - i), max(0, (r - inset) * ss), fill=255)
    return m.resize((w, h), Image.LANCZOS)


def card(w, h, r, fill, border=None, bw=2, shadow=0.45, grad=None, pad=48, glow=None) -> Spr:
    """Rounded card with optional gradient fill, hairline border, drop shadow, outer glow."""
    W_, H_ = w + 2 * pad, h + 2 * pad
    out = Image.new("RGBA", (W_, H_), (0, 0, 0, 0))
    m = rr_mask(w, h, r)
    if shadow:
        sm = Image.new("L", (W_, H_), 0)
        sm.paste(m, (pad, pad + 14))
        sm = sm.filter(ImageFilter.GaussianBlur(22)).point(lambda v: int(v * shadow))
        out.alpha_composite(colorize(sm, (2, 4, 12)))
    if glow:
        gcol, grad_r, gk = glow
        gm = Image.new("L", (W_, H_), 0)
        gm.paste(m, (pad, pad))
        gm = gm.filter(ImageFilter.GaussianBlur(grad_r)).point(lambda v: int(min(255, v * gk)))
        out.alpha_composite(colorize(gm, gcol))
    if grad:
        body = colorize(m, lin_grad(w, h, *grad))
    else:
        body = colorize(m.point(lambda v: v * fill[3] // 255), fill[:3])
    out.alpha_composite(body, (pad, pad))
    if border:
        inner = rr_mask(w, h, r, inset=bw)
        ring = ImageChops.subtract(m, inner).point(lambda v: v * border[3] // 255)
        out.alpha_composite(colorize(ring, border[:3]), (pad, pad))
    return Spr(out, w, h)


def compose(base: Spr, *parts) -> Spr:
    """Paste (Spr, dx, dy) parts centred at base centre + (dx, dy)."""
    img = base.img.copy()
    cx, cy = img.width / 2, img.height / 2
    for s, dx, dy in parts:
        im = s.img if isinstance(s, Spr) else s
        img.alpha_composite(im, (int(round(cx + dx - im.width / 2)), int(round(cy + dy - im.height / 2))))
    return Spr(img, base.cw, base.ch)


def logo_mark(size: int) -> Image.Image:
    """Brand mark: gradient squircle with a white 4-bar voice glyph."""
    ss = 4
    m = rr_mask(size, size, size * 0.3)
    img = colorize(m, lin_grad(size, size, C["primary"], C["accent"], 45))
    bars = Image.new("L", (size * ss, size * ss), 0)
    d = ImageDraw.Draw(bars)
    hs = [0.34, 0.62, 0.46, 0.24]
    bw, gap = size * 0.1, size * 0.07
    x0 = size / 2 - (4 * bw + 3 * gap) / 2
    for i, hh in enumerate(hs):
        x = x0 + i * (bw + gap)
        y0, y1 = size / 2 - hh * size / 2, size / 2 + hh * size / 2
        d.rounded_rectangle((x * ss, y0 * ss, (x + bw) * ss, y1 * ss), bw * ss / 2, fill=255)
    img.alpha_composite(colorize(bars.resize((size, size), Image.LANCZOS), (255, 255, 255)))
    return img


# ======================================================================== audio analysis
def audio_bands(path: Path, n_bands: int) -> np.ndarray:
    """Per video frame: n_bands log-spaced spectrum magnitudes in 0..1 (smoothed)."""
    with wave.open(str(path)) as w:
        sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dt = {2: np.int16, 4: np.int32}.get(sw, np.int16)
    x = np.frombuffer(raw, dt).astype(np.float32)
    x = x.reshape(-1, nch).mean(1) / float(np.iinfo(dt).max)
    n = 2048
    win = np.hanning(n).astype(np.float32)
    freqs = np.fft.rfftfreq(n, 1 / sr)
    edges = np.geomspace(90, 7000, n_bands + 1)
    idx = [(np.searchsorted(freqs, edges[i]), max(np.searchsorted(freqs, edges[i + 1]), np.searchsorted(freqs, edges[i]) + 1))
           for i in range(n_bands)]
    frames = int(round(DUR * FPS)) + 2
    out = np.zeros((frames, n_bands), np.float32)
    for f in range(frames):
        c = int(f / FPS * sr)
        seg = x[max(0, c - n // 2): c + n // 2]
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)))
        mag = np.abs(np.fft.rfft(seg * win))
        out[f] = [mag[a:b].mean() for a, b in idx]
    db = 20 * np.log10(out + 1e-6)
    ref = np.percentile(db, 97, axis=0)
    v = np.clip((db - (ref - 38)) / 38, 0, 1)
    sm = np.zeros_like(v)  # fast attack, slower release
    for f in range(frames):
        prev = sm[f - 1] if f else v[0] * 0
        sm[f] = np.where(v[f] > prev, lerp(prev, v[f], 0.75), lerp(prev, v[f], 0.28))
    return sm


# ======================================================================== scene graph
class El:
    def __init__(self, spr: Spr | None, cx, cy, t0, kind="rise", dur=0.6, scene=None, dyn=None, mod=None,
                 dist=70, exit=True):
        self.spr, self.cx, self.cy, self.t0, self.kind, self.dur = spr, cx, cy, t0, kind, dur
        self.scene, self.dyn, self.mod, self.dist, self.exit = scene, dyn, mod, dist, exit


EXIT = 0.34
PREROLL = 0.12
ELS: list[El] = []
CAPS: list[dict] = []
G: dict = {}


def stack(parts: list[tuple], cy: float) -> list[float]:
    """parts = [(height, gap_before)] -> centre y for each, whole stack centred on cy."""
    total = sum(h + g for h, g in parts)
    y = cy - total / 2
    out = []
    for h, g in parts:
        y += g
        out.append(y + h / 2)
        y += h
    return out


def build() -> None:
    global TL, W, H, FPS, DUR, C
    TL = json.loads((SHARED / "build" / "timeline.json").read_text(encoding="utf-8"))
    W, H, FPS, DUR = TL["width"], TL["height"], TL["fps"], TL["duration"]
    b = TL["brand"]
    C = {k: hex2rgb(v) for k, v in b.items() if isinstance(v, str) and v.startswith("#")}
    C["primary_l"] = tuple(int(lerp(c, 255, 0.25)) for c in C["primary"])
    ELS.clear()
    CX, CY = W / 2, 850  # content centre (safe zone: 220..1540)
    MAXW = W - 160
    head_grad = ("grad", C["text"], (196, 186, 255), 90)
    brand_grad = ("grad", C["primary_l"], C["accent"], 35)
    glow_p = (C["primary"], 38, 0.55)

    G["bands"] = audio_bands(SHARED / "build" / TL.get("audio", "voiceover.wav"), 16)

    for si, sc in enumerate(TL["scenes"]):
        s0 = sc["start"] - (PREROLL if si else 0.0)  # next scene starts entering while the last one leaves
        lay = sc["layout"]
        if lay == "hero":
            kick = text_spr(sc.get("kicker", ""), 34, 700, C["accent"], tracking=5)
            kp = card(int(kick.cw + 100), 72, 36, C["accent"] + (26,), border=C["accent"] + (110,), shadow=0)
            dot = card(14, 14, 7, C["accent"] + (255,), shadow=0, pad=12, glow=(C["accent"], 6, 1.0))
            kp = compose(kp, (kick, 14, 0), (dot, -kick.cw / 2 - 18, 0))
            hl = text_spr(sc["headline"], 156, 800, head_grad, max_lines=2, glow=glow_p, lh=1.0)
            sub = text_spr(sc.get("sub", ""), 54, 500, C["muted"], max_lines=2)
            ys = stack([(kp.ch, 0), (hl.ch, 46), (sub.ch, 34), (190, 70)], CY - 20)
            ELS.append(El(kp, CX, ys[0], s0 + 0.0, "pop", 0.5, si))
            ELS.append(El(hl, CX, ys[1], s0 + 0.12, "zoom", 0.75, si))
            ELS.append(El(sub, CX, ys[2], s0 + 0.45, "rise", 0.6, si))
            ELS.append(El(None, CX, ys[3], s0 + 0.6, "rise", 0.7, si, dyn=waveform_dyn(16, 13, 10, 180)))
        elif lay == "chips":
            hl = text_spr(sc["headline"], 230, 800, brand_grad, max_lines=1, glow=glow_p)
            items = sc.get("items", [])
            cw_, chh, gap = 430, 150, 26
            cols = 2
            rows = math.ceil(len(items) / cols)
            grid_h = rows * chh + (rows - 1) * gap
            ys = stack([(hl.ch, 0), (grid_h, 60)], CY)
            ELS.append(El(hl, CX, ys[0], s0 + 0.05, "zoom", 0.7, si))
            top = ys[1] - grid_h / 2
            for i, it in enumerate(items):
                r, c = divmod(i, cols)
                n_in_row = min(cols, len(items) - r * cols)
                row_w = n_in_row * cw_ + (n_in_row - 1) * gap
                x = CX - row_w / 2 + c * (cw_ + gap) + cw_ / 2
                y = top + r * (chh + gap) + chh / 2
                accent = C["primary"] if i % 2 == 0 else C["accent"]
                chip = card(cw_, chh, 44, C["surface"] + (235,), border=accent + (120,), bw=2,
                            glow=(accent, 26, 0.35))
                label = text_spr(it, 58, 650, C["text"], max_w=cw_ - 60, max_lines=1)
                pip = card(16, 16, 8, accent + (255,), shadow=0, pad=10, glow=(accent, 6, 1.0))
                lab_x = 14
                chip = compose(chip, (label, lab_x, 0), (pip, lab_x - label.cw / 2 - 26, 0))
                ELS.append(El(chip, x, y, s0 + 0.55 + i * 0.32, "pop", 0.55, si))
        elif lay == "stat":
            ring_d = 540
            hl = text_spr(sc["headline"], 104, 800, head_grad, max_lines=1)
            sub = text_spr(sc.get("sub", ""), 50, 500, C["muted"], max_lines=2, max_w=880)
            ys = stack([(ring_d, 0), (hl.ch, 44), (sub.ch, 18)], CY + 10)
            ELS.append(El(None, CX, ys[0], s0 + 0.0, "pop", 0.6, si, dyn=ring_dyn(ring_d, 24, s0 + 0.1, 1.5)))
            ELS.append(El(None, CX, ys[0] - 10, s0 + 0.05, "fade", 0.3, si, dyn=count_dyn(sc.get("stat", ""), 330, s0 + 0.1, 1.5)))
            ELS.append(El(hl, CX, ys[1], s0 + 0.35, "rise", 0.6, si))
            ELS.append(El(sub, CX, ys[2], s0 + 0.55, "rise", 0.6, si))
        elif lay == "list":
            hl = text_spr(sc["headline"], 104, 800, head_grad, max_lines=1, glow=glow_p, min_size=70)
            items = sc.get("items", [])
            iw, ih, gap = 860, 138, 24
            ys = stack([(hl.ch, 0)] + [(ih, 56 if i == 0 else gap) for i in range(len(items))], CY)
            ELS.append(El(hl, CX, ys[0], s0 + 0.05, "zoom", 0.7, si))
            for i, it in enumerate(items):
                runs = split_runs(it)
                icon = None
                if runs and runs[0][0] == "emoji":
                    icon, it = runs[0][1].strip(), it[len(runs[0][1]):].strip()
                accent = [C["primary"], C["accent"]][i % 2]
                cardi = card(iw, ih, 38, C["surface"] + (235,), border=(255, 255, 255, 22))
                parts = []
                tx = -iw / 2 + 40
                if icon:
                    tile = card(92, 92, 28, accent + (48,), border=accent + (90,), shadow=0, pad=8)
                    em = text_spr(icon, 58, 400, C["text"], max_lines=1)
                    parts += [(tile, tx + 46, 0), (em, tx + 46, 4)]
                    tx += 92 + 32
                lab = text_spr(it, 56, 650, C["text"], max_w=iw - 220, max_lines=1)
                parts.append((lab, tx + lab.cw / 2, 0))
                arrow = text_spr("→", 50, 500, C["muted"], max_lines=1)
                parts.append((arrow, iw / 2 - 56, 0))
                ELS.append(El(compose(cardi, *parts), CX, ys[i + 1], s0 + 0.45 + i * 0.26, "slide", 0.6, si, dist=160))
        elif lay == "cta":
            hl = text_spr(sc["headline"], 132, 800, head_grad, max_lines=2, glow=glow_p, lh=1.02)
            sub = text_spr(sc.get("sub", ""), 52, 500, C["muted"], max_lines=2)
            bw_, bh_ = 520, 156
            name = TL["brand"].get("name", "Ovoz")
            btn = card(bw_, bh_, bh_ / 2, None, grad=(C["primary"], C["accent"], 20), shadow=0.5,
                       glow=(C["primary"], 40, 0.6), pad=80)
            lab = text_spr(name, 78, 800, (255, 255, 255), max_lines=1, shadow=True)
            circ = card(92, 92, 46, (255, 255, 255, 245), shadow=0, pad=6)
            tri = Image.new("L", (92 * 4, 92 * 4), 0)
            ImageDraw.Draw(tri).polygon([(36 * 4, 26 * 4), (36 * 4, 66 * 4), (68 * 4, 46 * 4)], fill=255)
            tri = colorize(tri.resize((92, 92), Image.LANCZOS), C["primary"])
            circ.img.alpha_composite(tri, (6, 6))
            btn = compose(btn, (circ, -bw_ / 2 + 32 + 46, 0), (lab, 40, 0))
            ring = card(bw_, bh_, bh_ / 2, (0, 0, 0, 0), border=C["accent"] + (255,), bw=4, shadow=0, pad=120)
            ys = stack([(hl.ch, 0), (sub.ch, 30), (bh_, 90)], CY)
            ELS.append(El(hl, CX, ys[0], s0 + 0.0, "zoom", 0.7, si))
            ELS.append(El(sub, CX, ys[1], s0 + 0.2, "rise", 0.6, si))
            for k in range(2):
                ELS.append(El(ring, CX, ys[2], s0 + 0.7 + k * 0.55, "fade", 0.01, si, mod=ripple_mod(1.1)))
            ELS.append(El(btn, CX, ys[2], s0 + 0.35, "pop", 0.6, si, mod=pulse_mod()))

    # captions ---------------------------------------------------------------------------
    CAPS.clear()
    for c in TL["captions"]:
        s = text_spr(c["text"], 50, 600, C["text"], max_w=760, max_lines=1, min_size=34)
        CAPS.append({"t0": c["start"], "t1": c["end"], "spr": s})
    G["cap_bars_w"] = 44
    maxw = max(c["spr"].cw for c in CAPS) + G["cap_bars_w"] + 22 + 2 * 44
    G["pill"] = card(int(min(maxw, W - 100)), 104, 52, (17, 22, 42, 228), border=(255, 255, 255, 34), bw=2,
                     shadow=0.55, pad=40)
    # header -----------------------------------------------------------------------------
    mark = logo_mark(64)
    name = text_spr(TL["brand"].get("name", ""), 44, 800, C["text"], max_lines=1)
    hdr = Image.new("RGBA", (int(64 + 18 + name.img.width), max(64, name.img.height)), (0, 0, 0, 0))
    hdr.alpha_composite(mark, (0, (hdr.height - 64) // 2))
    hdr.alpha_composite(name.img, (64 + 18 - (name.img.width - int(name.cw)) // 2, (hdr.height - name.img.height) // 2))
    G["hdr"] = hdr
    G["counter"] = {i: text_spr(f"{i + 1:02d} / {len(TL['scenes']):02d}", 32, 600, C["muted"], max_lines=1)
                    for i in range(len(TL["scenes"]))}
    # background -------------------------------------------------------------------------
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 7, (H, W, 1), dtype=np.uint8).repeat(3, 2)
    yy, xx = np.mgrid[0:H, 0:W]
    dots = ((xx % 54 == 27) & (yy % 54 == 27)) | ((xx % 54 == 28) & (yy % 54 == 27)) | \
           ((xx % 54 == 27) & (yy % 54 == 28)) | ((xx % 54 == 28) & (yy % 54 == 28))
    fade = np.clip(1.2 - np.hypot((xx - W / 2) / (W * 0.6), (yy - H * 0.45) / (H * 0.5)), 0, 1)
    noise = noise + (dots * fade * 16).astype(np.uint8)[..., None]
    G["noise"] = Image.fromarray(noise)
    lw, lh_ = W // 6, H // 6
    ly, lx = np.mgrid[0:lh_, 0:lw].astype(np.float32)
    G["lx"], G["ly"] = (lx + 0.5) / lw, (ly + 0.5) / lh_ * (H / W)
    G["vign"] = np.clip(1.08 - 0.55 * np.hypot(G["lx"] - 0.5, (G["ly"] - H / W * 0.5) * W / H * 0.9) ** 1.6, 0, 1)


# per-scene glow anchors (x, y in width units; y spans 0..H/W)
ANCHORS = [
    [(0.15, 0.35), (0.95, 1.25), (0.5, 1.75)],
    [(0.9, 0.35), (0.1, 1.15), (0.6, 1.7)],
    [(0.5, 0.65), (0.15, 1.45), (0.9, 1.2)],
    [(0.1, 0.9), (0.9, 0.45), (0.5, 1.7)],
    [(0.5, 1.05), (0.2, 0.4), (0.85, 1.55)],
]


def background(t: float) -> Image.Image:
    sc = TL["scenes"]
    # blend anchors of neighbouring scenes around each boundary
    idx, k = 0, 0.0
    for i, s in enumerate(sc):
        if t >= s["start"]:
            idx = i
    if idx + 1 < len(sc):
        k = ease_in_out(clamp((t - (sc[idx + 1]["start"] - 0.6)) / 1.2))
    if idx > 0 and t < sc[idx]["start"] + 0.6:
        idx, k = idx - 1, ease_in_out(clamp((t - (sc[idx]["start"] - 0.6)) / 1.2))
    a0 = ANCHORS[idx % len(ANCHORS)]
    a1 = ANCHORS[(idx + 1) % len(ANCHORS)]
    lx, ly = G["lx"], G["ly"]
    col = np.empty(lx.shape + (3,), np.float32)
    col[:] = np.asarray(C["background"], np.float32)
    blobs = [(C["primary"], 0.62, 0.55), (C["accent"], 0.5, 0.30), ((60, 90, 230), 0.65, 0.32)]
    for j, (c, rad, inten) in enumerate(blobs):
        px = lerp(a0[j][0], a1[j][0], k) + 0.06 * math.sin(t * 0.7 + j * 2.1)
        py = lerp(a0[j][1], a1[j][1], k) + 0.06 * math.cos(t * 0.55 + j * 1.3)
        r = rad * (1 + 0.08 * math.sin(t * 0.9 + j))
        g = np.exp(-((lx - px) ** 2 + (ly - py) ** 2) / (r * r) * 2.2) * inten
        col += g[..., None] * np.asarray(c, np.float32)[None, None, :] * 0.55
    col *= G["vign"][..., None]
    low = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8))
    img = low.resize((W, H), Image.BICUBIC)
    img = ImageChops.add(img, G["noise"], 1.0, -3)
    return img.convert("RGBA")


# ======================================================================== dynamic layers
def waveform_dyn(n, bw, gap, hmax):
    """Mirrored, audio-reactive bar waveform driven by the voice-over spectrum."""
    ss = 2
    total = 2 * n
    w = int(total * bw + (total - 1) * gap) + 8
    h = hmax + 8
    grad = lin_grad(w, h, C["primary_l"], C["accent"], 0)

    def fn(t):
        f = min(int(round(t * FPS)), len(G["bands"]) - 1)
        v = G["bands"][f]
        m = Image.new("L", (w * ss, h * ss), 0)
        d = ImageDraw.Draw(m)
        for i in range(total):
            bi = abs(i - (n - 0.5)) - 0.5  # 0 at centre
            bi = int(bi)
            idle = 0.06 + 0.04 * math.sin(t * 3.1 + i * 0.6)
            val = max(idle, v[min(bi, n - 1)] * (1.0 - 0.35 * bi / n))
            bh = max(bw, val * hmax)
            x = 4 + i * (bw + gap)
            d.rounded_rectangle((x * ss, (h / 2 - bh / 2) * ss, (x + bw) * ss, (h / 2 + bh / 2) * ss), bw * ss / 2, fill=255)
        m = m.resize((w, h), Image.BILINEAR)
        return colorize(m, grad)
    return fn


def ring_dyn(d, stroke, t0, dur):
    pad = 60
    S = d + 2 * pad
    ss = 3
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    ang = (np.degrees(np.arctan2(xx - S / 2, -(yy - S / 2))) + 360) % 360  # 0 at top, clockwise
    track = Image.new("L", (S * ss, S * ss), 0)
    ImageDraw.Draw(track).ellipse((pad * ss, pad * ss, (pad + d) * ss, (pad + d) * ss), outline=255, width=stroke * ss)
    track = track.resize((S, S), Image.LANCZOS)
    base = colorize(track.point(lambda v: v * 22 // 255), (255, 255, 255))
    arc_col = lin_grad(S, S, C["primary_l"], C["accent"], 45)
    glow_src = track.filter(ImageFilter.GaussianBlur(18))
    tr = np.asarray(track, np.float32)
    gl = np.asarray(glow_src, np.float32)
    cache = {}

    def fn(t):
        p = ease_out_cubic(clamp((t - t0) / dur))
        key = round(p, 3)
        if key in cache:
            return cache[key]
        theta = p * 360
        m = np.clip((theta - ang) / 2.0 + 1, 0, 1) if p > 0 else np.zeros_like(ang)
        img = base.copy()
        # round cap at the arc head
        r_mid = d / 2 - stroke / 2 + stroke / 2
        cap = Image.new("L", (S, S), 0)
        if 0 < p:
            a = math.radians(theta)
            hx, hy = S / 2 + math.sin(a) * (d / 2 - stroke / 2), S / 2 - math.cos(a) * (d / 2 - stroke / 2)
            cs = Image.new("L", (S * ss, S * ss), 0)
            ImageDraw.Draw(cs).ellipse(((hx - stroke / 2) * ss, (hy - stroke / 2) * ss, (hx + stroke / 2) * ss, (hy + stroke / 2) * ss), fill=255)
            cap = cs.resize((S, S), Image.BILINEAR)
        _ = r_mid
        arc = np.maximum(tr * m, np.asarray(cap, np.float32))
        glow = gl * m * 0.9
        img.alpha_composite(colorize(Image.fromarray(np.clip(glow, 0, 255).astype(np.uint8)), C["primary"]))
        img.alpha_composite(colorize(Image.fromarray(arc.astype(np.uint8)), arc_col))
        if len(cache) < 400:
            cache[key] = img
        return img
    return fn


def count_dyn(stat: str, size, t0, dur):
    grad = ("grad", C["text"], (205, 195, 255), 90)
    glow = (C["primary"], 40, 0.6)
    target = int(stat) if stat.strip().isdigit() else None
    cache = {}

    def get(s):
        if s not in cache:
            cache[s] = text_spr(s, size, 800, grad, max_lines=1, glow=glow).img
        return cache[s]

    def fn(t):
        if target is None:
            return get(stat)
        p = ease_out_cubic(clamp((t - t0) / dur))
        val = int(math.floor(p * target + 1e-6)) if p < 1 else target
        img = get(str(val))
        # little pop when the final value lands
        lt = t - (t0 + dur)
        if 0 <= lt < 0.35:
            s = 1 + 0.08 * math.sin(math.pi * lt / 0.35)
            img = img.resize((int(img.width * s), int(img.height * s)), Image.BILINEAR)
        return img
    return fn


def ripple_mod(period):
    def fn(lt):
        ph = (lt % period) / period
        return 0, 0, 1 + 0.32 * ease_out_cubic(ph), 0.7 * (1 - ph) ** 1.5
    return fn


def pulse_mod(period=1.1, delay=0.6):
    def fn(lt):
        return 0, 0, 1 + 0.035 * math.sin(max(0.0, lt - delay) * 2 * math.pi / period), 1.0
    return fn


# ======================================================================== compositing
def paste(frame: Image.Image, img: Image.Image, cx: float, cy: float, scale=1.0, alpha=1.0, blur=0.0):
    if alpha <= 0.004:
        return
    if abs(scale - 1) > 0.002:
        nw, nh = max(1, int(img.width * scale)), max(1, int(img.height * scale))
        img = img.resize((nw, nh), Image.BILINEAR)
    if blur > 0.4:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    img = mul_alpha(img, alpha)
    x, y = int(round(cx - img.width / 2)), int(round(cy - img.height / 2))
    sx, sy = max(0, -x), max(0, -y)
    if sx >= img.width or sy >= img.height or x >= frame.width or y >= frame.height:
        return
    frame.alpha_composite(img, (max(0, x), max(0, y)), (sx, sy))


def scene_at(t):
    idx = 0
    for i, s in enumerate(TL["scenes"]):
        if t >= s["start"]:
            idx = i
    return idx


def el_state(el: El, t: float):
    lt = t - el.t0
    if lt < 0:
        return None
    sc = TL["scenes"][el.scene]
    last = el.scene == len(TL["scenes"]) - 1
    if not last and t >= sc["end"]:
        return None
    p = clamp(lt / el.dur)
    dx = dy = blur = 0.0
    s = a = 1.0
    if el.kind == "rise":
        e = ease_out_cubic(p)
        dy, a = (1 - e) * el.dist, clamp(p * 1.8)
    elif el.kind == "pop":
        s, a = 0.55 + 0.45 * ease_out_back(p, 2.2), clamp(p * 3)
    elif el.kind == "slide":
        e = ease_out_expo(p)
        dx, a = (1 - e) * el.dist, clamp(p * 2.2)
    elif el.kind == "zoom":
        e = ease_out_expo(p)
        s, a, blur = 1.18 - 0.18 * e, clamp(p * 2.2), (1 - e) * 14
    elif el.kind == "fade":
        a = ease_out_cubic(p)
    if el.mod:
        mdx, mdy, ms, ma = el.mod(lt)
        dx, dy = dx + mdx, dy + mdy
        s *= ms if ms is not None else 1
        a *= ma if ma is not None else 1
    if not last and el.exit:
        q = clamp((t - (sc["end"] - EXIT)) / EXIT)
        if q > 0:
            e = ease_in_cubic(q)
            a *= 1 - e
            dy -= 60 * e
            s *= 1 - 0.05 * e
            blur = max(blur, 10 * e)
    return dx, dy, s, a, blur


def draw_header(frame: Image.Image, t: float):
    a = clamp(t / 0.5)
    # stories-style segmented progress bar
    x0, x1, y, hh, gap = 80, W - 80, 236, 6, 10
    sc = TL["scenes"]
    total_w = (x1 - x0) - gap * (len(sc) - 1)
    bar = Image.new("RGBA", (x1 - x0, hh), (0, 0, 0, 0))
    ss = 4
    track = Image.new("L", ((x1 - x0) * ss, hh * ss), 0)
    fill = Image.new("L", ((x1 - x0) * ss, hh * ss), 0)
    dt, df = ImageDraw.Draw(track), ImageDraw.Draw(fill)
    x = 0.0
    for s in sc:
        sw = total_w * (s["end"] - s["start"]) / DUR
        dt.rounded_rectangle((x * ss, 0, (x + sw) * ss - 1, hh * ss - 1), hh * ss / 2, fill=255)
        p = clamp((t - s["start"]) / (s["end"] - s["start"]))
        if p > 0:
            df.rounded_rectangle((x * ss, 0, max(x * ss + hh * ss, (x + sw * p) * ss) - 1, hh * ss - 1), hh * ss / 2, fill=255)
        x += sw + gap
    track = track.resize((x1 - x0, hh), Image.LANCZOS)
    fill = fill.resize((x1 - x0, hh), Image.LANCZOS)
    bar.alpha_composite(colorize(track.point(lambda v: v * 40 // 255), (255, 255, 255)))
    bar.alpha_composite(colorize(fill, G.setdefault("bar_grad", lin_grad(x1 - x0, hh, C["primary_l"], C["accent"], 0))))
    paste(frame, bar, (x0 + x1) / 2, y + hh / 2, 1, a)
    hdr = G["hdr"]
    paste(frame, hdr, 80 + hdr.width / 2, 300, 1, a)
    i = scene_at(t)
    cnt = G["counter"][i]
    paste(frame, cnt.img, W - 80 - cnt.cw / 2, 300, 1, a)


def mini_bars(t: float, h=44) -> Image.Image:
    f = min(int(round(t * FPS)), len(G["bands"]) - 1)
    v = G["bands"][f]
    ss, n, bw, gap = 3, 4, 6, 6
    w = n * bw + (n - 1) * gap
    m = Image.new("L", (w * ss, h * ss), 0)
    d = ImageDraw.Draw(m)
    for i in range(n):
        val = max(0.12, float(v[[1, 3, 5, 8][i]]))
        bh = val * h
        x = i * (bw + gap)
        d.rounded_rectangle((x * ss, (h - bh) / 2 * ss, (x + bw) * ss, (h + bh) / 2 * ss), bw * ss / 2, fill=255)
    return colorize(m.resize((w, h), Image.LANCZOS), C["accent"])


def draw_captions(frame: Image.Image, t: float):
    CY_ = 1420
    padx, bars_w, gap = 44, G["cap_bars_w"], 22
    cur = None
    for i, c in enumerate(CAPS):
        if c["t0"] <= t < c["t1"]:
            cur = i
    # pill visibility (fade in at a chunk after a gap, fade out after the last chunk of a run)
    prev = None
    if cur is None:
        # in a gap: find the chunk that just ended
        ended = [i for i, c in enumerate(CAPS) if c["t1"] <= t]
        if not ended:
            return
        prev = ended[-1]
        out_p = (t - CAPS[prev]["t1"]) / 0.18
        if out_p >= 1:
            return
        vis, k_scale = 1 - ease_in_cubic(clamp(out_p)), 1 - 0.06 * clamp(out_p)
        show = prev
        width = CAPS[show]["spr"].cw + bars_w + gap + 2 * padx
        text_alpha, text_dy, old = 1.0, 0, None
    else:
        show = cur
        c = CAPS[cur]
        lt = t - c["t0"]
        contiguous = cur > 0 and c["t0"] - CAPS[cur - 1]["t1"] < 0.05
        w_now = c["spr"].cw + bars_w + gap + 2 * padx
        if contiguous:
            vis, k_scale = 1.0, 1.0
            w_prev = CAPS[cur - 1]["spr"].cw + bars_w + gap + 2 * padx
            width = lerp(w_prev, w_now, ease_out_cubic(clamp(lt / 0.16)))
            old = cur - 1
        else:
            pin = clamp(lt / 0.22)
            vis, k_scale = clamp(pin * 2), 0.85 + 0.15 * ease_out_back(pin, 2.0)
            width, old = w_now, None
        text_alpha = ease_out_cubic(clamp((lt - 0.02) / 0.16))
        text_dy = (1 - ease_out_cubic(clamp(lt / 0.2))) * 16
    pill = G["pill"]
    pw = pill.img.width
    pad = (pw - pill.cw) // 2
    width = int(min(width, pill.cw))
    full = int(width + 2 * pad)
    half = full // 2
    img = Image.new("RGBA", (full, pill.img.height), (0, 0, 0, 0))
    img.alpha_composite(pill.img.crop((0, 0, half, pill.img.height)), (0, 0))
    img.alpha_composite(pill.img.crop((pw - (full - half), 0, pw, pill.img.height)), (half, 0))
    # contents
    left = pad + padx
    mid = img.height / 2
    bars = mini_bars(t)
    img.alpha_composite(bars, (int(left), int(mid - bars.height / 2)))
    tx = left + bars_w + gap
    # text goes on its own layer, clipped to the pill interior (the pill morphs between chunk widths)
    layer = Image.new("RGBA", (img.width + 400, img.height), (0, 0, 0, 0))

    def put(spr_img, cw, alpha, dy):
        im = mul_alpha(spr_img, alpha)
        x = int(tx - (im.width - cw) / 2)
        y = int(mid - im.height / 2 + dy)
        layer.alpha_composite(im, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))

    if old is not None:
        oa = 1 - clamp((t - CAPS[cur]["t0"]) / 0.1)
        if oa > 0:
            put(CAPS[old]["spr"].img, CAPS[old]["spr"].cw, oa, -10 * (1 - oa))
    s = CAPS[show]["spr"]
    put(s.img, s.cw, text_alpha, text_dy)
    x0, x1 = int(tx - 6), int(full - pad - padx * 0.55)
    if x1 > x0:
        img.alpha_composite(layer, (x0, 0), (x0, 0, x1, img.height))
    paste(frame, img, W / 2, CY_, k_scale, vis)


def render_frame(i: int) -> np.ndarray:
    t = i / FPS
    frame = background(t)
    draw_header(frame, t)
    for el in ELS:
        st = el_state(el, t)
        if st is None:
            continue
        dx, dy, s, a, blur = st
        img = el.dyn(t) if el.dyn else el.spr.img
        paste(frame, img, el.cx + dx, el.cy + dy, s, a, blur)
    draw_captions(frame, t)
    return np.asarray(frame.convert("RGB"))


# ======================================================================== main
def _init_worker():
    build()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(VIDEO / "out" / "moviepy.mp4"))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--frames", help="comma separated frame numbers -> PNG previews only")
    ap.add_argument("--preview-dir", default=str(HERE / "preview"))
    args = ap.parse_args()

    t_start = time.perf_counter()
    build()
    print(f"raqm={RAQM} reshaper={'yes' if arabic_reshaper else 'no'} emoji={HAS_EMOJI} "
          f"build={time.perf_counter() - t_start:.2f}s")

    if args.frames:
        Path(args.preview_dir).mkdir(parents=True, exist_ok=True)
        for f in [int(x) for x in args.frames.split(",")]:
            Image.fromarray(render_frame(f)).save(Path(args.preview_dir) / f"frame_{f:04d}.png")
        print("previews written to", args.preview_dir)
        return

    from moviepy import AudioFileClip, VideoClip

    n_frames = int(TL.get("frames") or math.ceil(DUR * FPS - 1e-6))
    pool = it = None
    if args.workers > 1:
        import multiprocessing as mp
        pool = mp.get_context("spawn").Pool(args.workers, initializer=_init_worker)
        it = pool.imap(render_frame, range(n_frames), chunksize=2)
    state = {"next": 0}

    def make_frame(t):
        i = min(int(round(t * FPS)), n_frames - 1)
        if it is not None and i == state["next"]:
            state["next"] += 1
            return next(it)
        return render_frame(i)

    clip = VideoClip(make_frame, duration=n_frames / FPS)
    audio = AudioFileClip(str(SHARED / "build" / TL.get("audio", "voiceover.wav")))
    clip = clip.with_audio(audio.with_duration(min(audio.duration, clip.duration)))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    clip.write_videofile(
        args.out, fps=FPS, codec="libx264", audio_codec="aac", audio_fps=48000, audio_bitrate="192k",
        preset="medium", threads=os.cpu_count(), temp_audiofile_path=tempfile.gettempdir(),
        ffmpeg_params=["-crf", "18", "-movflags", "+faststart", "-profile:v", "high"],
        pixel_format="yuv420p", logger="bar",
    )
    if pool:
        pool.close()
        pool.join()
    print(f"done: {args.out}  frames={n_frames}  render_time={time.perf_counter() - t_start:.1f}s")


if __name__ == "__main__":
    main()
