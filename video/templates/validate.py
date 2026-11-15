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
  4. Determinism: renders the same moments twice, in a fresh page and in a different order, and compares
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


async def render_checks(info: dict, langs: list[str], sheet_dir: Path | None, previews_dir: Path | None, tid: str) -> dict:
    from playwright.async_api import async_playwright
    rep = {"issues": [], "errors": [], "fonts": [], "determinism": [], "frames": 0}
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
        browser = await p.chromium.launch(args=["--force-color-profile=srgb", "--hide-scrollbars", "--font-render-hinting=none",
                                                "--disable-lcd-text", "--allow-file-access-from-files", "--disable-gpu"])
        tmp = Path(__file__).resolve().parent / ".validate-tmp"
        tmp.mkdir(exist_ok=True)

        async def page_for(tl, name):
            W, H = tl["width"], tl["height"]
            data = tmp / f"{name}.js"
            data.write_text("window.TIMELINE = " + json.dumps(tl, ensure_ascii=False) + ";\nwindow.AUDIO = null;\n", encoding="utf-8")
            pg = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            errs = []
            pg.on("pageerror", lambda e: errs.append(f"page error: {e}"))
            pg.on("console", lambda m: errs.append(f"console: {m.text}") if m.type == "error" else None)
            await pg.goto(RENDERER.as_uri() + "?render&data=" + data.resolve().as_uri())
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
    r = asyncio.run(render_checks(tinfo, langs, Path(args.sheet) if args.sheet else None,
                                  d if args.previews else None, rep["template"]))
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
    args = ap.parse_args()
    targets = args.templates or [m["id"] for m in T.list_all()] + [d.name for d in T.ROOT.iterdir()
                                                                   if d.is_dir() and d.name.startswith("_") and (d / "manifest.json").is_file()]
    reports = []
    for t in targets:
        tgt = str(T.ROOT / t) if t.startswith("_") else t
        rep = validate(tgt, args)
        reports.append(rep)
        if not args.json:
            n_iss = len(rep["issues"])
            ok = not rep["errors"] and not n_iss and not rep["determinism"] and (not args.strict or not rep["warnings"])
            print(f"\n== {rep['template']}: {'OK' if ok else 'PROBLEMS'}  ({rep.get('frames', 0)} frames rendered)")
            for e in rep["errors"]:
                print(f"  ERROR    {e}")
            for i in rep["issues"]:
                print(f"  LAYOUT   {i['where']}: {i['what']}")
            for dd in rep["determinism"]:
                print(f"  NONDET   {dd}")
            for w in rep["warnings"]:
                print(f"  warning  {w}")
            for f in rep["fonts"]:
                print(f"  font     {f['family']}: {'loaded' if f['ok'] else 'NOT LOADED'}")
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=1))
    bad = any(r["errors"] or r["issues"] or r["determinism"] or (args.strict and r["warnings"]) for r in reports)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
