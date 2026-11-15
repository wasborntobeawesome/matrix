"""Check Video Studio templates: manifest, files, and real renders of every layout x format.

    video/.venv/Scripts/python video/templates/validate.py                 # every installed template
    video/.venv/Scripts/python video/templates/validate.py clean my-brand  # some
    video/.venv/Scripts/python video/templates/validate.py ./my-brand      # a folder that is not installed yet
    options:
      --quick        English + Arabic only (default: en, ar, ru, uz)
      --previews     also (re)write the template's preview-9x16/1x1/16x9.png (listed in manifest "previews")
      --sheet DIR    write a contact sheet PNG per template + language into DIR
      --json         machine-readable report on stdout
      --strict       warnings fail too

What it checks
  1. manifest.json against the schema and the rules (ids, kinds, tokens, colours, files, fonts, options).
  2. template.js syntax (with Node, when it is installed).
  3. Renders a test video (every layout, long-ish texts) in 9:16, 1:1 and 16:9 and en / ar (RTL) / ru
     (Cyrillic) / uz (oʻ gʻ ʼ) with headless Chromium and, on a settled frame of every scene, flags:
       - template.css / template.js / font load errors and exceptions (window.__tplErrors, page errors)
       - text outside the frame or the format's safe zone, text clipped by an overflow:hidden box,
         scene text overlapping the caption pill or the brand header
       - declared fonts that did not load
  4. Contrast over footage: every layout x format (first language) again with an AI b-roll background (the sample
     frame video/html/broll-placeholder.jpg, or --frame <image|clip>; dim 0.5) and, per text element, the WCAG
     contrast ratio of its colour against the pixels behind it (a second screenshot with the text hidden; halos and
     scrims stay). The 10th percentile must reach 4.5:1 (3:1 for text >= 66 px, or >= 50 px semi-bold). Text on
     its own opaque or gradient box (a button) is skipped. --no-contrast skips it; --sheet DIR also writes the
     frames to DIR/contrast/.
  5. Determinism: renders the same moments twice, in a fresh page and in a different order, and compares
     the pixels (rendering uses the software rasteriser here so equal input gives equal pixels).
Exit code 0 = no errors.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parent
sys.path.insert(0, str(VIDEO / "studio"))
sys.path.insert(0, str(VIDEO / "shared"))
sys.path.insert(0, str(VIDEO / "html"))

import render as R  # noqa: E402  (the renderer's isolated browser: virtual origin, no file:// access, no network)
import templates as T  # noqa: E402
import voicelib as V  # noqa: E402

EXAMPLE = VIDEO / "studio" / "example"
RENDERER = VIDEO / "html" / "index.html"
FORMATS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
SCENE_LEN = 4.6

# Test brief: every layout, realistic but long-ish copy, per language.
TEXT = {
    "en": {
        "hero": ("NEW APP", "Meet Nimbo today", "Budgeting that finally feels calm"),
        "screen": ("Everything at a glance", "All accounts in one place", "HOME"),
        "screens": ("Save with a plan", "Goals", "Insights"),
        "chips": ("Works everywhere", ["iPhone", "Android", "Web app", "Shared budgets"]),
        "stat": ("3x", "faster saving", "according to our users"),
        "list": ("Why people love Nimbo", ["🔒 Private and secure", "⚡ Instant sync", "👨‍👩‍👧 Family budgets"]),
        "cta": ("Try Nimbo free", "No card needed", "Get Nimbo", "nimbo.example"),
        "presenter": ("HELLO", "Your money, finally calm", "A friendly guide in every scene"),
        "say": "See every account and card in one simple dashboard, then plan the whole month.",
    },
    "ar": {
        "hero": ("تطبيق جديد", "تعرّف على نيمبو اليوم", "ميزانية هادئة أخيرًا"),
        "screen": ("كل شيء في لمحة", "كل حساباتك في مكان واحد", "الرئيسية"),
        "screens": ("ادّخر بخطة واضحة", "الأهداف", "التحليلات"),
        "chips": ("يعمل في كل مكان", ["آيفون", "أندرويد", "تطبيق الويب", "ميزانية مشتركة"]),
        "stat": ("3x", "ادخار أسرع", "بحسب مستخدمينا"),
        "list": ("لماذا يحب الناس نيمبو", ["🔒 آمن وخاص", "⚡ مزامنة فورية", "👨‍👩‍👧 ميزانية العائلة"]),
        "cta": ("جرّب نيمبو مجانًا", "بدون بطاقة", "احصل على نيمبو", "nimbo.example"),
        "presenter": ("مرحبًا", "أموالك بهدوء أخيرًا", "مرشد ودود في كل مشهد"),
        "say": "شاهد كل حساباتك وبطاقاتك في شاشة واحدة بسيطة، ثم خطط للشهر كله.",
    },
    "ru": {
        "hero": ("НОВОЕ ПРИЛОЖЕНИЕ", "Знакомьтесь, Nimbo", "Бюджет без стресса и лишних таблиц"),
        "screen": ("Всё под рукой", "Все счета в одном месте", "ГЛАВНАЯ"),
        "screens": ("Копите по плану", "Цели", "Аналитика"),
        "chips": ("Работает везде", ["iPhone", "Android", "Веб-версия", "Общий бюджет"]),
        "stat": ("3x", "быстрее копят", "по словам пользователей"),
        "list": ("Почему выбирают Nimbo", ["🔒 Надёжно и приватно", "⚡ Мгновенная синхронизация", "👨‍👩‍👧 Семейный бюджет"]),
        "cta": ("Попробуйте Nimbo", "Без банковской карты", "Скачать Nimbo", "nimbo.example"),
        "presenter": ("ПРИВЕТ", "Ваши деньги под контролем", "Дружелюбный гид в каждой сцене"),
        "say": "Все счета и карты — на одном простом экране, а весь месяц — в одном плане.",
    },
    "uz": {
        "hero": ("YANGI ILOVA", "Nimbo'ni ko'ring", "Byudjet endi tinch va oson"),
        "screen": ("Hammasi bir qarashda", "Barcha hisoblar bir joyda", "BOSH SAHIFA"),
        "screens": ("Reja bilan jamg'aring", "Maqsadlar", "Tahlil"),
        "chips": ("Hamma joyda ishlaydi", ["iPhone", "Android", "Veb-ilova", "Umumiy byudjet"]),
        "stat": ("3x", "tezroq jamg'arish", "foydalanuvchilarimiz so'ziga ko'ra"),
        "list": ("Nega aynan Nimbo", ["🔒 Xavfsiz va maxfiy", "⚡ Tezkor sinxronlash", "👨‍👩‍👧 Oila byudjeti"]),
        "cta": ("Nimbo'ni bepul sinab ko'ring", "Karta kerak emas", "Nimbo'ni oling", "nimbo.example"),
        "presenter": ("SALOM", "Pulingiz endi xotirjam", "Har bir sahnada do'stona yo'lboshchi"),
        "say": "Barcha hisob va kartalaringizni bitta oddiy ekranda ko'ring va butun oyni rejalang.",
    },
}


def test_scenes(lang: str) -> list[dict]:
    x = TEXT[lang]
    fix = (lambda s: V.display_text(s, "uz", "Nimbo")) if lang == "uz" else (lambda s: s)
    h, sc, ss, ch, st, li, cta, pr = (x[k] for k in ("hero", "screen", "screens", "chips", "stat", "list", "cta", "presenter"))
    return [
        {"id": "hero", "layout": "hero", "kicker": fix(h[0]), "headline": fix(h[1]), "sub": fix(h[2])},
        {"id": "screen", "layout": "screen", "device": "phone", "asset": "home.png", "headline": fix(sc[0]),
         "callout": fix(sc[1]), "kicker": fix(sc[2]), "focus": {"x": 0.05, "y": 0.135, "w": 0.9, "h": 0.215}},
        {"id": "browser", "layout": "screen", "device": "browser", "asset": "web-dashboard.png", "headline": fix(sc[0]),
         "callout": fix(sc[1]), "url": "app.nimbo.example", "focus": {"x": 0.18, "y": 0.235, "w": 0.48, "h": 0.36}},
        {"id": "screens", "layout": "screens", "device": "phone", "headline": fix(ss[0]),
         "items": [{"asset": "goals.png", "label": fix(ss[1])}, {"asset": "insights.png", "label": fix(ss[2])}]},
        {"id": "chips", "layout": "chips", "headline": fix(ch[0]), "items": [fix(i) for i in ch[1]]},
        {"id": "stat", "layout": "stat", "stat": st[0], "headline": fix(st[1]), "sub": fix(st[2])},
        {"id": "list", "layout": "list", "headline": fix(li[0]), "items": [fix(i) for i in li[1]]},
        {"id": "presenter", "layout": "presenter", "kicker": fix(pr[0]), "headline": fix(pr[1]), "sub": fix(pr[2])},
        {"id": "cta", "layout": "cta", "headline": fix(cta[0]), "sub": fix(cta[1]), "button": fix(cta[2]), "url": cta[3]},
    ]


def test_timeline(info: dict, lang: str, fmt: str) -> dict:
    from PIL import Image
    W, H = FORMATS[fmt]
    assets = {}
    for f in ("home.png", "goals.png", "insights.png", "web-dashboard.png"):
        with Image.open(EXAMPLE / f) as im:
            assets[f] = {"type": "image", "w": im.size[0], "h": im.size[1]}
    scenes, caps, t = [], [], 0.0
    say = TEXT[lang]["say"]
    for s in test_scenes(lang):
        speech = SCENE_LEN - 0.6
        scenes.append({**s, "say": say, "start": round(t, 3), "end": round(t + SCENE_LEN, 3), "speech_end": round(t + speech, 3)})
        caps += V.caption_chunks(say, t, speech, lang=lang, product="Nimbo")
        t += SCENE_LEN
    tl = {"title": "template check", "language": lang, "format": fmt, "width": W, "height": H, "fps": 30,
          "duration": round(t, 3), "frames": round(t * 30), "brand": {"name": "Nimbo", "primary": "#3D7BFF",
          "accent": "#34D399", "background": "#0A1024"}, "assets": assets, "asset_base": EXAMPLE.resolve().as_uri() + "/",
          "scenes": scenes, "captions": caps, "audio": None, "template": info}
    if lang == "ar":
        tl["rtl"] = True
    return tl


def settle_time(s: dict) -> float:
    return round(s["start"] + min(3.2, (s["end"] - s["start"]) * 0.72), 3)


# ------------------------------------------------------------------ contrast over footage
# A sample "AI b-roll" frame: busy, mid-tone, with bright warm bokeh and light ribbons — the worst case for
# text (the QA's Wan 2.2 hook clip measured mean luma 0.43, p90 0.88). The same still is the editor's
# placeholder for a clip that is not generated yet.
SAMPLE_FRAME = VIDEO / "html" / "broll-placeholder.jpg"
CONTRAST_MIN = {"normal": 4.5, "large": 3.0}     # WCAG AA; "large" = >= 66 stage px (24 css px on a phone)
LARGE_PX = 66


def make_sample_frame(out: Path = SAMPLE_FRAME, size: int = 1280, seed: int = 7) -> Path:
    """Write the deterministic sample frame (numpy + Pillow; no AI model needed)."""
    import numpy as np
    from PIL import Image, ImageFilter
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / size
    img = np.zeros((size, size, 3), np.float32)
    top, bot = np.array([18, 52, 46]), np.array([52, 98, 88])
    img[:] = (top + (bot - top) * yy[..., None]) / 255.0
    img += 0.10 * np.exp(-((xx - 0.3) ** 2 + (yy - 0.6) ** 2) / 0.05)[..., None] * np.array([0.9, 0.7, 0.4])
    # glowing ribbons (sound waves)
    for k in range(4):
        amp, fr, ph, x0 = rng.uniform(0.06, 0.16), rng.uniform(1.5, 3.2), rng.uniform(0, 6.3), rng.uniform(0.25, 0.75)
        cx = x0 + amp * np.sin(2 * np.pi * fr * yy + ph)
        d = np.abs(xx - cx)
        w = rng.uniform(0.008, 0.02)
        col = np.array([0.45, 0.95, 0.85]) if k % 2 == 0 else np.array([0.6, 0.7, 1.0])
        img += (np.exp(-(d / w) ** 2) * 0.85)[..., None] * col
    # bokeh discs, warm and white, soft edged
    for _ in range(90):
        cx, cy, r = rng.uniform(0, 1), rng.uniform(0, 1), rng.uniform(0.02, 0.075)
        a = rng.uniform(0.35, 0.95)
        col = np.array([1.0, 0.86, 0.6]) if rng.uniform() < 0.75 else np.array([1.0, 1.0, 1.0])
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
        m = np.clip((1.0 - d) * 6.0, 0, 1)
        img = img * (1 - (m * a * 0.85)[..., None]) + (m * a)[..., None] * col
    img = np.clip(img, 0, 1)
    im = Image.fromarray((img * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=84, optimize=True)
    return out


def frame_from(src: Path | None, tmpdir: Path) -> Path:
    """The background still for the contrast check: the sample, an image, or a frame of a clip (ffmpeg)."""
    if not src:
        if not SAMPLE_FRAME.exists():
            make_sample_frame()
        return SAMPLE_FRAME
    src = Path(src)
    if src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        return src
    import subprocess
    out = tmpdir / "contrast-frame.png"
    tmpdir.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(VIDEO / "bin" / "ffmpeg.exe"), "-y", "-loglevel", "error", "-ss", "1.5", "-i", str(src),
                    "-frames:v", "1", str(out)], check=True, capture_output=True)
    return out


def _lin(c):
    import numpy as np
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb):
    """WCAG relative luminance of sRGB values (0..255), any array shape [..., 3]."""
    lin = _lin(rgb)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def contrast_ratio(a, b):
    la, lb = luminance(a), luminance(b)
    import numpy as np
    hi, lo = np.maximum(la, lb), np.minimum(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def text_contrast(bg_rgb, color: tuple, box: tuple, pct: float = 10.0) -> dict:
    """Contrast of a text colour (r, g, b[, a]) against the background pixels inside box (x0, y0, x1, y1).

    Returns {p10, mean_bg_luma}: p10 = the contrast that 90 % of the background behind the text reaches
    (a few bright bokeh spots behind letters are what makes text unreadable, so the mean is not enough)."""
    import numpy as np
    h, w = bg_rgb.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return {"p10": None, "mean_bg_luma": None}
    px = bg_rgb[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
    r, g, b = color[:3]
    a = color[3] if len(color) > 3 else 1.0
    fg = a * np.array([r, g, b], np.float64) + (1 - a) * px          # semi-transparent text over each pixel
    cr = contrast_ratio(fg, px)
    return {"p10": float(np.percentile(cr, pct)), "mean_bg_luma": float(luminance(px).mean())}


# text elements of the visible scene: tight rects, colour, size (gradient-filled text is skipped)
TEXT_JS = r"""
() => {
  const st = document.getElementById("stage"), S = st.getBoundingClientRect();
  const scene = [...document.querySelectorAll("#scenes > .scene")].find(s => s.style.display !== "none" && parseFloat(s.style.opacity || "1") > 0.6);
  if (!scene) return [];
  const out = [], seen = new Set();
  // any CSS colour (rgb(), color(srgb …) from color-mix, oklch …) -> [r, g, b, a] via a 1x1 canvas
  const cv = document.createElement("canvas"); cv.width = cv.height = 1;
  const g2 = cv.getContext("2d", { willReadFrequently: true });
  const rgbaOf = c => { g2.clearRect(0, 0, 1, 1); g2.fillStyle = "#000"; g2.fillStyle = c; g2.fillRect(0, 0, 1, 1);
    const d = g2.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2], d[3] / 255]; };
  const visible = el => { for (let e = el; e && e !== st; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) < 0.5) return false; } return true; };
  const tw = document.createTreeWalker(scene, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const txt = n.textContent.trim(); if (!txt || !/[\p{L}\p{N}]/u.test(txt)) continue;   // emoji-only: colour glyphs
    const el = n.parentElement;
    if (!el || el.closest(".device, .callout, .dev-label, .cl-icon, .pb-ring") || !visible(el)) continue;
    const cs = getComputedStyle(el);
    if (/text/.test(cs.webkitBackgroundClip || cs.backgroundClip || "") || cs.webkitTextFillColor === "rgba(0, 0, 0, 0)") continue;
    const r = document.createRange(); r.selectNodeContents(n);
    const rs = [...r.getClientRects()].filter(x => x.width > 1 && x.height > 1);
    if (!rs.length) continue;
    // text on its own opaque or gradient box (a button) does not depend on the footage: not part of this check
    let boxed = false;
    for (let e = el; e && e !== scene; e = e.parentElement) { const bs = getComputedStyle(e); if (rgbaOf(bs.backgroundColor)[3] >= 0.9 || /gradient|url\(/.test(bs.backgroundImage)) { boxed = true; break; } }
    if (boxed) continue;
    const m = rgbaOf(cs.color);
    const key = el.className + txt; if (seen.has(key)) continue; seen.add(key);
    out.push({ text: txt.slice(0, 40), cls: (typeof el.className === "string" ? el.className : "").split(/\s+/)[0] || el.tagName.toLowerCase(),
      color: m, size: parseFloat(cs.fontSize), weight: parseInt(cs.fontWeight) || 400,
      rects: rs.map(x => [x.left - S.left, x.top - S.top, x.right - S.left, x.bottom - S.top]) });
  }
  return out;
}
"""
HIDE_TEXT_CSS = "#scenes, #scenes * { color: transparent !important; -webkit-text-fill-color: transparent !important; -webkit-text-stroke-color: transparent !important; }"


def contrast_level(size: float, weight: int) -> str:
    return "large" if size >= LARGE_PX or (size >= 0.75 * LARGE_PX and weight >= 600) else "normal"


async def contrast_checks(ctx_factory, info: dict, frame: Path, langs: list[str], dim: float = 0.5,
                          dump: Path | None = None) -> list[dict]:
    """Render every text layout over `frame` as a b-roll background (dim like the QA's hook: 0.5) and measure
    each text element's contrast against what is behind it (the frame re-rendered with the text hidden; text
    shadows / halos stay, they are part of the treatment). Returns [{where, text, ratio, need, ok}]."""
    from io import BytesIO
    import numpy as np
    from PIL import Image
    out = []
    for lang in langs:
        for fmt in FORMATS:
            tl = test_timeline(info, lang, fmt)
            for s in tl["scenes"]:
                s["background"] = {"type": "broll", "placeholder": True, "dim": dim, "blur": 0}
            tl["broll"] = True
            tl["broll_placeholder"] = frame.resolve().as_uri()
            pg, _ = await ctx_factory(tl, f"contrast-{lang}-{fmt.replace(':', 'x')}")
            for s in tl["scenes"]:
                if s["layout"] in ("presenter",):
                    continue
                t = settle_time(s)
                await pg.evaluate("t => window.seek(t)", t)
                await pg.evaluate("t => window.seek(t)", t)
                items = await pg.evaluate(TEXT_JS)
                if not items:
                    continue
                sty = await pg.add_style_tag(content=HIDE_TEXT_CSS)
                png = await pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": tl["width"], "height": tl["height"]})
                await sty.evaluate("e => e.remove()")
                bg = np.asarray(Image.open(BytesIO(png)).convert("RGB"))
                if dump:
                    dump.mkdir(parents=True, exist_ok=True)
                    stem = f"contrast-{lang}-{fmt.replace(':', 'x')}-{s['id']}"
                    (dump / f"{stem}.png").write_bytes(await pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": tl["width"], "height": tl["height"]}))
                    (dump / f"{stem}-behind.png").write_bytes(png)
                for it in items:
                    worst = None
                    for rc in it["rects"]:
                        r = text_contrast(bg, tuple(it["color"]), rc)
                        if r["p10"] is not None and (worst is None or r["p10"] < worst):
                            worst = r["p10"]
                    if worst is None:
                        continue
                    lvl = contrast_level(it["size"], it["weight"])
                    need = CONTRAST_MIN[lvl]
                    out.append({"where": f"{lang} {fmt} {s['layout']}{' (' + s['id'] + ')' if s['id'] != s['layout'] else ''}",
                                "text": it["text"], "cls": it["cls"], "ratio": round(worst, 2), "need": need,
                                "ok": worst >= need})
            await pg.close()
    return out


CHECK_JS = r"""
() => {
  const st = document.getElementById("stage");
  const W = st.offsetWidth, H = st.offsetHeight;
  const fmt = W > H ? "16:9" : W === H ? "1:1" : "9:16";
  const SAFE = { "9:16": [50, 220, 1030, 1560], "1:1": [30, 20, 1050, 1060], "16:9": [60, 20, 1860, 1060] }[fmt];
  const out = [];
  const visible = el => { for (let e = el; e && e !== st; e = e.parentElement) { const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden" || parseFloat(cs.opacity) < 0.05) return false; } return true; };
  const rectOf = node => { const r = document.createRange(); r.selectNodeContents(node); const rs = [...r.getClientRects()].filter(x => x.width > 0.5 && x.height > 0.5);
      if (!rs.length) return null; return rs.reduce((a, b) => ({ left: Math.min(a.left, b.left), top: Math.min(a.top, b.top), right: Math.max(a.right, b.right), bottom: Math.max(a.bottom, b.bottom) })); };
  const S = st.getBoundingClientRect();
  const rel = r => ({ x0: r.left - S.left, y0: r.top - S.top, x1: r.right - S.left, y1: r.bottom - S.top });
  const hit = (a, b, m = 0) => a.x0 < b.x1 - m && a.x1 > b.x0 + m && a.y0 < b.y1 - m && a.y1 > b.y0 + m;
  const cap = [...document.querySelectorAll("#captions .cap")].find(c => c.style.display !== "none" && parseFloat(c.style.opacity || "1") > 0.5);
  const capR = cap ? rel(cap.getBoundingClientRect()) : null;
  const brand = document.getElementById("brand");
  const brandR = brand && visible(brand) && brand.offsetWidth ? rel(brand.getBoundingClientRect()) : null;
  const scene = [...document.querySelectorAll("#scenes > .scene")].find(s => s.style.display !== "none" && parseFloat(s.style.opacity || "1") > 0.6);
  const label = el => (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : el.tagName.toLowerCase());
  const seen = new Set();
  const check = (root, kind) => {
    const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      if (!n.textContent.trim()) continue;
      const el = n.parentElement;
      if (!el || el.closest(".device") || !visible(el)) continue;
      const r0 = rectOf(n); if (!r0) continue;
      const r = rel(r0);
      const txt = n.textContent.trim().slice(0, 40);
      const key = label(el) + txt; if (seen.has(key)) continue; seen.add(key);
      const tag = `${kind} ${label(el)} “${txt}”`;
      if (r.x0 < -2 || r.y0 < -2 || r.x1 > W + 2 || r.y1 > H + 2) out.push({ level: "error", what: `${tag} is outside the frame` });
      else if (r.x0 < SAFE[0] - 2 || r.y0 < SAFE[1] - 2 || r.x1 > SAFE[2] + 2 || r.y1 > SAFE[3] + 2)
        out.push({ level: "error", what: `${tag} is outside the ${fmt} safe zone (${r.x0.toFixed(0)},${r.y0.toFixed(0)}–${r.x1.toFixed(0)},${r.y1.toFixed(0)})` });
      for (let a = el; a && a !== st; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (cs.overflow !== "visible" || cs.overflowX !== "visible" || cs.textOverflow === "ellipsis") {
          const ar = rel(a.getBoundingClientRect());
          if (r.x0 < ar.x0 - 2 || r.x1 > ar.x1 + 2 || r.y0 < ar.y0 - 2 || r.y1 > ar.y1 + 2) { out.push({ level: "error", what: `${tag} is clipped by ${label(a)}` }); break; }
        }
      }
      if (kind === "scene" && capR && hit(r, capR, 4)) out.push({ level: "error", what: `${tag} overlaps the caption` });
      if (kind === "scene" && brandR && hit(r, brandR, 4)) out.push({ level: "error", what: `${tag} overlaps the brand header` });
    }
  };
  if (scene) check(scene, "scene");
  if (cap) check(cap, "caption");
  if (brandR) check(brand, "header");
  const tf = document.getElementById("tpl-fg"); if (tf) check(tf, "overlay");
  return { issues: out, errors: window.__tplErrors || [] };
}
"""

FONTS_JS = r"""
fams => fams.map(f => ({ family: f, ok: document.fonts.check(`700 40px "${f}"`) &&
  [...document.fonts].filter(x => x.family.replace(/"/g, "") === f).every(x => x.status === "loaded") }))
"""


async def render_checks(info: dict, langs: list[str], sheet_dir: Path | None, previews_dir: Path | None, tid: str,
                        contrast_frame: Path | None = None) -> dict:
    from playwright.async_api import async_playwright
    rep = {"issues": [], "errors": [], "fonts": [], "determinism": [], "frames": 0, "contrast": []}
    fams = set()
    F = (info.get("tokens") or {}).get("fonts") or {}
    for k in ("display", "body"):
        if F.get(k):
            fams.add(F[k])
    for f in F.get("files") or []:
        if isinstance(f, dict) and f.get("family"):
            fams.add(f["family"])
    shots: dict = {}
    async with async_playwright() as p:
        # the template's code runs in the same sandbox as in a real render: only the renderer, its fonts, the
        # example assets and the template folder are readable, and there is no network
        mounts = R.Mounts()
        mounts.add(R.HERE)
        mounts.add(VIDEO / "shared" / "fonts")
        if contrast_frame:
            mounts.add(contrast_frame.resolve().parent)
        browser, ctx = await R.open_context(p, 1080, 1920, mounts)
        tmp = Path(__file__).resolve().parent / ".validate-tmp"
        tmp.mkdir(exist_ok=True)

        async def page_for(tl, name):
            W, H = tl["width"], tl["height"]
            tl = mounts.rewrite(tl)
            mounts.mem[f"/__validate/{name}.js"] = (("window.TIMELINE = " + json.dumps(tl, ensure_ascii=False) +
                                                    ";\nwindow.AUDIO = null;\n").encode("utf-8"), "text/javascript")
            pg = await ctx.new_page()
            await pg.set_viewport_size({"width": W, "height": H})
            errs = []
            pg.on("pageerror", lambda e: errs.append(f"page error: {e}"))
            pg.on("console", lambda m: errs.append(f"console: {m.text}") if m.type == "error" else None)
            await pg.goto(mounts.url_of(RENDERER) + "?render&data=" + R.VIRT + f"/__validate/{name}.js")
            await pg.wait_for_function("window.__ready === true", timeout=60000)
            return pg, errs

        try:
            for lang in langs:
                for fmt in FORMATS:
                    tl = test_timeline(info, lang, fmt)
                    pg, errs = await page_for(tl, f"{tid}-{lang}-{fmt.replace(':', 'x')}")
                    for s in tl["scenes"]:
                        t = settle_time(s)
                        await pg.evaluate("t => window.seek(t)", t)
                        await pg.evaluate("t => window.seek(t)", t)
                        res = await pg.evaluate(CHECK_JS)
                        where = f"{lang} {fmt} {s['layout']}{' (' + s['id'] + ')' if s['id'] != s['layout'] else ''}"
                        for it in res["issues"]:
                            rep["issues"].append({**it, "where": where})
                        png = await pg.screenshot(type="png", clip={"x": 0, "y": 0, "width": tl["width"], "height": tl["height"]})
                        shots[(lang, fmt, s["id"])] = png
                        rep["frames"] += 1
                    e2 = await pg.evaluate("window.__tplErrors || []")
                    for e in sorted(set(e2 + errs)):
                        rep["errors"].append(f"{lang} {fmt}: {e}")
                    if lang == langs[0] and fmt == "9:16" and fams:
                        rep["fonts"] = await pg.evaluate(FONTS_JS, sorted(fams))
                    await pg.close()
            # contrast of every text over footage (the template's b-roll treatment)
            if contrast_frame:
                rep["contrast"] = await contrast_checks(page_for, info, contrast_frame, langs[:1],
                                                        dump=sheet_dir / "contrast" if sheet_dir else None)
            # determinism: same moments, fresh page, different order (incl. transition moments)
            for fmt in FORMATS:
                tl = test_timeline(info, langs[0], fmt)
                times = []
                for s in tl["scenes"][1:]:
                    times += [round(s["start"] - 0.12, 3), round(s["start"] + 0.2, 3)]
                times = times[:: max(1, len(times) // 8)] + [settle_time(tl["scenes"][0])]
                a, _ = await page_for(tl, f"{tid}-det-a")
                b, _ = await page_for(tl, f"{tid}-det-b")
                await b.evaluate("t => window.seek(t)", tl["duration"] * 0.6)
                ga = {}
                for t in times:
                    await a.evaluate("t => window.seek(t)", t)
                    ga[t] = await a.screenshot(type="png")
                for t in reversed(times):
                    await b.evaluate("t => window.seek(t)", t)
                    gb = await b.screenshot(type="png")
                    diff = _diff(ga[t], gb)
                    if diff:
                        rep["determinism"].append(f"{fmt} t={t:.2f}s renders differently on a second pass ({diff}; "
                                                  "something depends on wall-clock time, randomness or seek order)")
                await a.close()
                await b.close()
        finally:
            await browser.close()
            for f in tmp.glob("*.js"):
                f.unlink(missing_ok=True)
            try:
                tmp.rmdir()
            except OSError:
                pass
    if sheet_dir:
        sheet_dir.mkdir(parents=True, exist_ok=True)
        for lang in langs:
            _sheet(shots, lang, sheet_dir / f"{tid}-{lang}.png", tid)
        fd = sheet_dir / "frames"
        fd.mkdir(exist_ok=True)
        for (lang, fmt, sid), png in shots.items():
            (fd / f"{tid}-{lang}-{fmt.replace(':', 'x')}-{sid}.png").write_bytes(png)
    if previews_dir:
        from io import BytesIO
        from PIL import Image
        for fmt in FORMATS:
            sid = "hero"
            im = Image.open(BytesIO(shots[(langs[0], fmt, sid)])).convert("RGB")
            W, H = im.size
            im.thumbnail((480, 480))
            im.save(previews_dir / f"preview-{fmt.replace(':', 'x')}.png", optimize=True)
    return rep


def _diff(a: bytes, b: bytes) -> str | None:
    """None when two frames match. Tiny differences (anti-aliasing of an element Chromium rasterised at a
    different scale after another seek order: <= 0.3 % of the pixels) are not counted."""
    if a == b:
        return None
    from io import BytesIO
    import numpy as np
    from PIL import Image
    x = np.asarray(Image.open(BytesIO(a)).convert("RGB"), dtype=np.int16)
    y = np.asarray(Image.open(BytesIO(b)).convert("RGB"), dtype=np.int16)
    if x.shape != y.shape:
        return "different size"
    d = np.abs(x - y).max(axis=2)
    frac = float((d > 8).mean())
    if frac <= 0.003:
        return None
    return f"{frac * 100:.1f} % of the pixels differ, max {int(d.max())}"


def _sheet(shots: dict, lang: str, out: Path, tid: str) -> None:
    """Contact sheet: one row per format, one column per scene."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    ids = [s["id"] for s in test_scenes(lang)]
    cell = {"9:16": (216, 384), "1:1": (300, 300), "16:9": (480, 270)}
    rows = []
    for fmt in FORMATS:
        w, h = cell[fmt]
        row = Image.new("RGB", (len(ids) * (w + 8) + 8, h + 34), (24, 26, 34))
        d = ImageDraw.Draw(row)
        for i, sid in enumerate(ids):
            png = shots.get((lang, fmt, sid))
            if not png:
                continue
            im = Image.open(BytesIO(png)).convert("RGB").resize((w, h), Image.LANCZOS)
            row.paste(im, (8 + i * (w + 8), 26))
            d.text((8 + i * (w + 8), 6), f"{fmt} {sid}", fill=(200, 205, 220))
        rows.append(row)
    W = max(r.size[0] for r in rows)
    sheet = Image.new("RGB", (W, sum(r.size[1] for r in rows) + 30), (24, 26, 34))
    ImageDraw.Draw(sheet).text((8, 8), f"template {tid} · {lang}", fill=(255, 255, 255))
    y = 30
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.size[1]
    sheet.save(out, optimize=True)


def validate(target: str, args) -> dict:
    p = Path(target)
    if p.is_dir() and (p / "manifest.json").is_file():
        d = p.resolve()
    else:
        d = T.ROOT / target
    rep = {"template": target, "errors": [], "warnings": [], "issues": [], "determinism": [], "fonts": []}
    if not (d / "manifest.json").is_file():
        rep["errors"].append(f"{d}: no manifest.json")
        return rep
    try:
        m = T.read_manifest(d)
    except T.TemplateError as e:
        rep["errors"] += e.errors
        return rep
    errs, warns = T.check_manifest(m, d)
    rep["template"] = m.get("id") or target
    rep["errors"] += errs
    rep["warnings"] += warns
    js = d / (m.get("js") or "template.js")
    if m.get("kind") == "full" and js.is_file():
        rep["errors"] += T.js_syntax(js)
    if errs:
        return rep
    info = T._derive(m, d)
    tinfo = {"id": m["id"], "name": m.get("name"), "kind": m["kind"], "version": info["version_hash"],
             "tokens": m.get("tokens") or {}, "options": T.option_values(m, {})[0],
             "default_transition": m.get("default_transition") or "lift", "base": d.resolve().as_uri() + "/"}
    if info.get("css_file"):
        tinfo["css"] = info["css_file"]
    if info.get("js_file"):
        tinfo["js"] = info["js_file"]
    langs = ["en", "ar"] if args.quick else ["en", "ar", "ru", "uz"]
    frame = None if args.no_contrast else frame_from(Path(args.frame) if args.frame else None, HERE / ".validate-tmp")
    r = asyncio.run(render_checks(tinfo, langs, Path(args.sheet) if args.sheet else None,
                                  d if args.previews else None, rep["template"], frame))
    rep["contrast"] = r["contrast"]
    rep["contrast_failed"] = [c for c in r["contrast"] if not c["ok"]]
    rep["errors"] += r["errors"]
    rep["issues"] = r["issues"]
    rep["determinism"] = r["determinism"]
    rep["fonts"] = r["fonts"]
    rep["frames"] = r["frames"]
    for f in r["fonts"]:
        if not f["ok"]:
            rep["errors"].append(f"font “{f['family']}” did not load (check fonts.files and the file)")
    if args.previews:
        names = [f"preview-{f.replace(':', 'x')}.png" for f in FORMATS]
        if m.get("previews") != names:
            rep["warnings"].append(f"manifest previews should list {names}")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("templates", nargs="*", help="template ids or folders (default: all installed)")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--previews", action="store_true")
    ap.add_argument("--sheet")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--frame", help="contrast check background: an image or a clip (default: the sample frame "
                                    "video/html/broll-placeholder.jpg)")
    ap.add_argument("--no-contrast", action="store_true", help="skip the contrast-over-footage check")
    ap.add_argument("--make-sample", action="store_true", help="(re)write the sample frame and exit")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(errors="backslashreplace")     # emoji / Arabic on a legacy console code page
    except (AttributeError, ValueError):
        pass
    if args.make_sample:
        print(make_sample_frame())
        return 0
    targets = args.templates or [m["id"] for m in T.list_all()] + [d.name for d in T.ROOT.iterdir()
                                                                   if d.is_dir() and d.name.startswith("_") and (d / "manifest.json").is_file()]
    reports = []
    for t in targets:
        tgt = str(T.ROOT / t) if t.startswith("_") else t
        rep = validate(tgt, args)
        reports.append(rep)
        if not args.json:
            n_iss = len(rep["issues"]) + len(rep.get("contrast_failed") or [])
            ok = not rep["errors"] and not n_iss and not rep["determinism"] and (not args.strict or not rep["warnings"])
            print(f"\n== {rep['template']}: {'OK' if ok else 'PROBLEMS'}  ({rep.get('frames', 0)} frames rendered)")
            for e in rep["errors"]:
                print(f"  ERROR    {e}")
            for i in rep["issues"]:
                print(f"  LAYOUT   {i['where']}: {i['what']}")
            for c in rep.get("contrast_failed") or []:
                print(f"  CONTRAST {c['where']}: .{c['cls']} “{c['text']}” {c['ratio']}:1 over footage (needs {c['need']}:1)")
            if rep.get("contrast"):
                worst = min(rep["contrast"], key=lambda c: c["ratio"] / c["need"])
                print(f"  contrast {len(rep['contrast'])} texts over footage, lowest {worst['ratio']}:1 "
                      f"({worst['where']} .{worst['cls']}, needs {worst['need']}:1)")
            for dd in rep["determinism"]:
                print(f"  NONDET   {dd}")
            for w in rep["warnings"]:
                print(f"  warning  {w}")
            for f in rep["fonts"]:
                print(f"  font     {f['family']}: {'loaded' if f['ok'] else 'NOT LOADED'}")
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=1))
    bad = any(r["errors"] or r["issues"] or r.get("contrast_failed") or r["determinism"] or (args.strict and r["warnings"])
              for r in reports)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
