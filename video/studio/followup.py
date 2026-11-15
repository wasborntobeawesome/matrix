"""“Ask the AI for missing material”: a ready-to-send follow-up message for the source AI.

GET /api/projects/{id}/followup -> {message, issues: [{type, scene?, lang?, file?, text}], counts, ok}

It checks the stored brief against the uploaded assets and lists, in English:
  - files the brief references that were never uploaded, or can't be used (a video as the logo, ...)
  - missing translations per language
  - voice-over lines over the word limit, and empty text the layout needs
  - screenshots in the wrong orientation for their frame, or too small to look sharp
  - suggestions (a screenshot that would help a text-only scene, a logo, a recording, focus areas)
"""
from __future__ import annotations

import brief as B

LABELS = {"uz": "Uzbek", "ru": "Russian", "en": "English", "ar": "Arabic"}
FIELD_NAMES = {"say": "voice-over (say)", "kicker": "kicker", "headline": "headline", "sub": "subline",
               "callout": "callout", "button": "button", "url": "url", "stat": "stat"}
# what each layout needs on screen
NEEDS = {"hero": ["headline"], "chips": ["headline", "items"], "stat": ["stat", "headline"], "list": ["headline", "items"],
         "cta": ["headline"], "screen": ["asset"], "screens": ["items"], "presenter": ["headline"]}
MIN_W = {"phone": 720, "browser": 1280, "none": 720}


def _txt(v, lang: str, main: str) -> str:
    """The text of a field in one language (a plain string is used for every language)."""
    if isinstance(v, dict):
        return str(v.get(lang) or "").strip()
    return str(v or "").strip()


def _words(s: str) -> int:
    return len(s.split())


def build(brief: dict, assets: dict) -> dict:
    issues: list[dict] = []
    main = brief.get("language") or "en"
    langs = brief.get("languages") or [main]
    product = brief.get("product") or "the product"
    scenes = brief.get("scenes") or []

    def add(typ, text, **kw):
        issues.append({"type": typ, "text": text, **kw})

    # ---------------------------------------------------------- files
    refs = []                                        # (file, scene id, role, device)
    for s in scenes:
        if s.get("layout") == "screen":
            if s.get("asset"):
                refs.append((s["asset"], s["id"], "screen", s.get("device") or "phone"))
        if s.get("layout") == "screens":
            for it in s.get("items") or []:
                if isinstance(it, dict) and it.get("asset"):
                    refs.append((it["asset"], s["id"], "screens", s.get("device") or "phone"))
    logo = (brief.get("brand") or {}).get("logo")
    if logo:
        refs.append((logo, None, "logo", None))
    pres = brief.get("presenter") if isinstance(brief.get("presenter"), dict) else None
    seen_missing = set()
    for name, sid, role, dev in refs:
        m = assets.get(name)
        where = f"scene “{sid}”" if sid else "the brand logo"
        if not m:
            if name in seen_missing:
                continue
            seen_missing.add(name)
            what = ("a square logo (PNG, transparent background)" if role == "logo"
                    else "a portrait phone screenshot (e.g. 1170×2532)" if dev == "phone"
                    else "a browser screenshot of the page content (e.g. 1440×900)" if dev == "browser"
                    else "a screenshot")
            add("missing_file", f"{name} ({where}) was not received — please send {what}.", file=name, scene=sid)
            continue
        if role == "logo" and m.get("type") != "image":
            add("invalid_file", f"{name} is used as the logo but is a video — please send the logo as a PNG.", file=name)
            continue
        w, h = m.get("w") or 0, m.get("h") or 0
        if not w or not h:
            add("invalid_file", f"{name} ({where}) could not be read — please send it again as PNG/JPG or MP4.", file=name, scene=sid)
            continue
        if role in ("screen", "screens"):
            kind = "recording" if m.get("type") == "video" else "screenshot"
            if dev == "phone" and w > h:
                add("orientation", f"{name} ({where}, phone frame) is landscape ({w}×{h}) — please send a portrait "
                                   f"phone {kind}, or say if it should be shown in a browser frame.", file=name, scene=sid)
            elif dev == "browser" and h > w and w < 1000:
                add("orientation", f"{name} ({where}, browser frame) is a portrait {w}×{h} image that looks like a phone "
                                   f"screen — please send a desktop browser {kind} (e.g. 1440×900).", file=name, scene=sid)
            minw = MIN_W.get(dev or "phone", 720)
            short = min(w, h) if dev != "browser" else w
            if short < minw:
                add("low_resolution", f"{name} ({where}) is only {w}×{h} — it will look blurry. Please send it "
                                      f"{'at least 1080 px wide (native phone resolution, e.g. 1170×2532)' if dev != 'browser' else 'at least 1440×900'}.",
                    file=name, scene=sid)
            if m.get("type") == "video" and (m.get("duration") or 0) > 10.5:
                add("long_recording", f"{name} ({where}) is {m['duration']:.0f} s long; only the first 10 s are shown — "
                                      "a 3–8 s clip of the one action would be better.", file=name, scene=sid)

    # ---------------------------------------------------------- translations
    if len(langs) > 1:
        miss = B.missing_translations(brief)
        for lg in langs:
            per = [(sid, m[lg]) for sid, m in miss.items() if lg in m]
            if per:
                lst = "; ".join(f"“{sid}”: {', '.join(FIELD_NAMES.get(f, f) for f in fs)}" for sid, fs in per)
                add("translation", f"{LABELS.get(lg, lg)} ({lg}) is missing in {len(per)} scene(s) — {lst}.", lang=lg)

    # ---------------------------------------------------------- text: word limit, empty fields
    for s in scenes:
        sid, lay = s.get("id"), s.get("layout")
        for lg in langs:
            say = _txt(s.get("say"), lg, main)
            if not say:
                if lg == main:          # other languages: listed under missing translations
                    add("empty_text", f"Scene “{sid}” ({lay}) has no voice-over line (say) — please write one spoken "
                                      f"sentence{f' in {LABELS.get(lg, lg)}' if len(langs) > 1 else ''}.", scene=sid, lang=lg)
                continue
            n = _words(say)
            if n > B.SAY_WORDS_SOFT:
                add("too_long", f"Scene “{sid}”{f' ({lg})' if len(langs) > 1 else ''}: the voice-over has {n} words — "
                                f"please shorten it to {B.SAY_WORDS_SOFT} words or fewer (about 6 seconds).", scene=sid, lang=lg)
        for f in NEEDS.get(lay, []):
            if f == "items":
                items = s.get("items") or []
                want = 2 if lay in ("chips", "list", "screens") else 1
                if len(items) < want:
                    add("empty_text", f"Scene “{sid}” ({lay}) needs at least {want} items"
                                      f"{' (screenshots)' if lay == 'screens' else ''} — it has {len(items)}.", scene=sid)
            elif f == "asset":
                if not s.get("asset"):
                    add("missing_file", f"Scene “{sid}” is a screen scene without a screenshot — please send one and name it.",
                        scene=sid)
            elif not _txt(s.get(f), main, main):
                add("empty_text", f"Scene “{sid}” ({lay}) has an empty {FIELD_NAMES.get(f, f)} — please write one "
                                  f"({'1–5 words' if f == 'headline' else 'e.g. 3x or 50%' if f == 'stat' else 'short'}).", scene=sid)
            elif f == "headline" and _words(_txt(s.get(f), main, main)) > 7:
                add("too_long", f"Scene “{sid}”: the headline has {_words(_txt(s.get(f), main, main))} words — "
                                "on-screen text should be 1–5 words.", scene=sid)

    # ---------------------------------------------------------- suggestions
    sug = []
    shots = [s for s in scenes if s.get("layout") in ("screen", "screens")]
    textonly = [s for i, s in enumerate(scenes) if s.get("layout") in ("chips", "list") and 0 < i < len(scenes) - 1]
    for s in textonly[:2]:
        # quote the English text when the brief has it (the message is in English)
        topic = (_txt(s.get("headline"), "en", main) if "en" in langs else "") or _txt(s.get("headline"), main, main)             or _txt(s.get("say"), main, main)[:60]
        sug.append({"type": "suggestion", "scene": s.get("id"),
                    "text": f"A screenshot of {product} showing “{topic}” would help scene “{s.get('id')}” "
                            f"(it could become a 'screen' scene that zooms into it)."})
    for s in shots:
        if s.get("layout") == "screen" and not s.get("focus") and assets.get(s.get("asset"), {}).get("type") == "image":
            sug.append({"type": "suggestion", "scene": s.get("id"),
                        "text": f"Scene “{s.get('id')}”: give a 'focus' rectangle (fractions 0..1 of {s.get('asset')}) for the "
                                "part the line talks about, so the camera can zoom into it."})
            break
    if not logo:
        sug.append({"type": "suggestion", "text": "A square logo.png (transparent background) would be shown in the header "
                                                  "and on the button; add it as brand.logo."})
    if shots and not any(m.get("type") == "video" for m in assets.values()):
        target = shots[0]
        sug.append({"type": "suggestion", "scene": target.get("id"),
                    "text": f"A 3–8 s screen recording (MP4) of the main action — e.g. for scene “{target.get('id')}” — "
                            "would make the video livelier than still screenshots."})
    if len(shots) < 2 and len(scenes) >= 4:
        sug.append({"type": "suggestion", "text": f"Only {len(shots)} scene(s) show the product. 2–4 more real screenshots "
                                                  "(one per feature) would make the video more convincing."})
    if pres and pres.get("enabled") and not pres.get("portrait") and not pres.get("portrait_prompt"):
        sug.append({"type": "suggestion", "text": "The presenter is enabled without a portrait: add presenter.portrait_prompt "
                                                  "describing a fictional host."})
    issues += sug[:5]

    counts = {}
    for it in issues:
        counts[it["type"]] = counts.get(it["type"], 0) + 1
    blocking = [i for i in issues if i["type"] != "suggestion"]
    return {"message": message(brief, issues), "issues": issues, "counts": counts, "ok": not blocking}


SECTIONS = [
    ("Files that are missing", ("missing_file",)),
    ("Files to replace", ("invalid_file", "orientation", "low_resolution", "long_recording")),
    ("Missing translations", ("translation",)),
    ("Text to fix", ("too_long", "empty_text")),
    ("Suggestions (optional)", ("suggestion",)),
]


def message(brief: dict, issues: list[dict]) -> str:
    product = brief.get("product") or "the product"
    title = brief.get("title") or f"{product} promo"
    blocking = [i for i in issues if i["type"] != "suggestion"]
    lines = [f"Hi! Thanks for the material for “{title}”."]
    if not issues:
        lines += ["", "Everything I need is here — the brief, translations and files all check out. No changes needed."]
        return "\n".join(lines) + "\n"
    if blocking:
        lines.append("Before I can finish the video, a few things are missing or need fixing:")
    else:
        lines.append("Everything required is here. A few optional extras would make the video better:")
    n = 0
    for head, types in SECTIONS:
        items = [i for i in issues if i["type"] in types]
        if not items:
            continue
        n += 1
        lines += ["", f"{n}. {head}"]
        lines += [f"   - {i['text']}" for i in items]
    langs = brief.get("languages") or [brief.get("language")]
    files = any(i["type"] in ("missing_file", "invalid_file", "orientation", "low_resolution", "long_recording") for i in issues)
    lines += ["", "How to reply:"]
    if files:
        lines.append("- Send the files with exactly the file names above (screenshots as PNG, recordings as MP4).")
    lines += [
              "- Give every scene you change as a complete JSON object with the same \"id\", e.g. "
              "{\"id\": \"hook\", \"layout\": \"hero\", ...}" + (", with every text field as a map "
                                                                 f"{{{', '.join(f'\"{lg}\": \"...\"' for lg in langs)}}}."
                                                                 if len(langs) > 1 else "."),
              "- Keep voice-over lines to one natural sentence of at most 18 words; on-screen headlines 1–5 words."]
    return "\n".join(lines) + "\n"
