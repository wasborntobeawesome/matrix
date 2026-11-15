"""“Ask the AI for missing material”: a ready-to-send follow-up message for the source AI.

GET /api/projects/{id}/followup[?lang=uz] -> {message, issues: [{type, scene?, lang?, file?, text}], counts, ok, lang}

It checks the stored brief against the uploaded assets and lists (in English by default — the source AI
usually works in English — or in Uzbek with lang="uz"):
  - files the brief references that were never uploaded, or can't be used (a video as the logo, ...)
  - missing translations per language
  - voice-over lines over the word limit, and empty text the layout needs
  - screenshots in the wrong orientation for their frame, or too small to look sharp
  - suggestions (a screenshot that would help a text-only scene, a logo, a recording, focus areas)
"""
from __future__ import annotations

import brief as B

LANGS = ("en", "uz")
LABELS = {"en": {"uz": "Uzbek", "ru": "Russian", "en": "English", "ar": "Arabic"},
          "uz": {"uz": "oʻzbekcha", "ru": "ruscha", "en": "inglizcha", "ar": "arabcha"}}
FIELD_NAMES = {"en": {"say": "voice-over (say)", "kicker": "kicker", "headline": "headline", "sub": "subline",
                      "callout": "callout", "button": "button", "url": "url", "stat": "stat"},
               "uz": {"say": "ovoz matni (say)", "kicker": "kicker", "headline": "sarlavha (headline)",
                      "sub": "pastki satr (sub)", "callout": "izoh (callout)", "button": "tugma (button)", "url": "url",
                      "stat": "raqam (stat)"}}
# what each layout needs on screen
NEEDS = {"hero": ["headline"], "chips": ["headline", "items"], "stat": ["stat", "headline"], "list": ["headline", "items"],
         "cta": ["headline"], "screen": ["asset"], "screens": ["items"], "presenter": ["headline"]}
MIN_W = {"phone": 720, "browser": 1280, "none": 720}

# message texts per language; {placeholders} are filled with str.format
T = {
    "en": {
        "scene": "scene “{sid}”", "logo": "the brand logo",
        "want_logo": "a square logo (PNG, transparent background)",
        "want_phone": "a portrait phone screenshot (e.g. 1170×2532)",
        "want_browser": "a browser screenshot of the page content (e.g. 1440×900)", "want_any": "a screenshot",
        "missing_file": "{name} ({where}) was not received — please send {what}.",
        "logo_video": "{name} is used as the logo but is a video — please send the logo as a PNG.",
        "unreadable": "{name} ({where}) could not be read — please send it again as PNG/JPG or MP4.",
        "recording": "recording", "screenshot": "screenshot",
        "landscape_phone": "{name} ({where}, phone frame) is landscape ({w}×{h}) — please send a portrait phone {kind}, "
                           "or say if it should be shown in a browser frame.",
        "portrait_browser": "{name} ({where}, browser frame) is a portrait {w}×{h} image that looks like a phone screen — "
                            "please send a desktop browser {kind} (e.g. 1440×900).",
        "low_res": "{name} ({where}) is only {w}×{h} — it will look blurry. Please send it {need}.",
        "need_phone": "at least 1080 px wide (native phone resolution, e.g. 1170×2532)", "need_browser": "at least 1440×900",
        "long_rec": "{name} ({where}) is {s:.0f} s long; only the first 10 s are shown — a 3–8 s clip of the one action "
                    "would be better.",
        "translation": "{label} ({lg}) is missing in {n} scene(s) — {lst}.",
        "no_say": "Scene “{sid}” ({lay}) has no voice-over line (say) — please write one spoken sentence{in_lang}.",
        "in_lang": " in {label}",
        "too_long_say": "Scene “{sid}”{lgs}: the voice-over has {n} words — please shorten it to {m} words or fewer "
                        "(about 6 seconds).",
        "few_items": "Scene “{sid}” ({lay}) needs at least {want} items{shots} — it has {n}.",
        "shots": " (screenshots)",
        "no_asset": "Scene “{sid}” is a screen scene without a screenshot — please send one and name it.",
        "empty": "Scene “{sid}” ({lay}) has an empty {field} — please write one ({hint}).",
        "hint_headline": "1–5 words", "hint_stat": "e.g. 3x or 50%", "hint_other": "short",
        "long_headline": "Scene “{sid}”: the headline has {n} words — on-screen text should be 1–5 words.",
        "sug_shot": "A screenshot of {product} showing “{topic}” would help scene “{sid}” (it could become a 'screen' scene "
                    "that zooms into it).",
        "sug_focus": "Scene “{sid}”: give a 'focus' rectangle (fractions 0..1 of {asset}) for the part the line talks "
                     "about, so the camera can zoom into it.",
        "sug_logo": "A square logo.png (transparent background) would be shown in the header and on the button; add it "
                    "as brand.logo.",
        "sug_rec": "A 3–8 s screen recording (MP4) of the main action — e.g. for scene “{sid}” — would make the video "
                   "livelier than still screenshots.",
        "sug_more": "Only {n} scene(s) show the product. 2–4 more real screenshots (one per feature) would make the video "
                    "more convincing.",
        "sug_presenter": "The presenter is enabled without a portrait: add presenter.portrait_prompt describing a "
                         "fictional host.",
        "the_product": "the product", "promo": "{product} promo",
        "hi": "Hi! Thanks for the material for “{title}”.",
        "all_good": "Everything I need is here — the brief, translations and files all check out. No changes needed.",
        "blocking": "Before I can finish the video, a few things are missing or need fixing:",
        "optional": "Everything required is here. A few optional extras would make the video better:",
        "sections": ["Files that are missing", "Files to replace", "Missing translations", "Text to fix",
                     "Suggestions (optional)"],
        "how": "How to reply:",
        "how_files": "- Send the files with exactly the file names above (screenshots as PNG, recordings as MP4).",
        "how_json": "- Give every scene you change as a complete JSON object with the same \"id\", e.g. "
                    "{{\"id\": \"hook\", \"layout\": \"hero\", ...}}",
        "how_maps": ", with every text field as a map {{{maps}}}.",
        "how_len": "- Keep voice-over lines to one natural sentence of at most 18 words; on-screen headlines 1–5 words.",
    },
    "uz": {
        "scene": "“{sid}” sahnasi", "logo": "brend logotipi",
        "want_logo": "kvadrat logotip (PNG, shaffof fon)",
        "want_phone": "telefonning tik skrinshoti (masalan, 1170×2532)",
        "want_browser": "brauzerdagi sahifa skrinshoti (masalan, 1440×900)", "want_any": "skrinshot",
        "missing_file": "{name} ({where}) kelmadi — iltimos, {what} yuboring.",
        "logo_video": "{name} logotip sifatida ishlatilgan, lekin bu video — iltimos, logotipni PNG qilib yuboring.",
        "unreadable": "{name} ({where}) oʻqilmadi — iltimos, PNG/JPG yoki MP4 qilib qayta yuboring.",
        "recording": "ekran yozuvini", "screenshot": "skrinshotini",
        "landscape_phone": "{name} ({where}, telefon ramkasi) yotiq ({w}×{h}) — iltimos, telefonning tik {kind} yuboring "
                           "yoki u brauzer ramkasida koʻrsatilishi kerakligini ayting.",
        "portrait_browser": "{name} ({where}, brauzer ramkasi) tik {w}×{h} rasm, telefon ekraniga oʻxshaydi — iltimos, "
                            "kompyuter brauzerining {kind} yuboring (masalan, 1440×900).",
        "low_res": "{name} ({where}) atigi {w}×{h} — xira chiqadi. Iltimos, {need} qilib yuboring.",
        "need_phone": "kengligi kamida 1080 px (telefonning asl oʻlchami, masalan, 1170×2532)",
        "need_browser": "kamida 1440×900",
        "long_rec": "{name} ({where}) {s:.0f} soniya; faqat birinchi 10 soniyasi koʻrsatiladi — bitta amalning 3–8 soniyalik "
                    "klipi yaxshiroq boʻladi.",
        "translation": "{label} ({lg}) tarjima {n} ta sahnada yoʻq — {lst}.",
        "no_say": "“{sid}” sahnasida ({lay}) ovoz matni (say) yoʻq — iltimos, bitta ogʻzaki gap yozing{in_lang}.",
        "in_lang": " ({label})",
        "too_long_say": "“{sid}” sahnasi{lgs}: ovoz matnida {n} ta soʻz bor — iltimos, {m} ta soʻzgacha qisqartiring "
                        "(taxminan 6 soniya).",
        "few_items": "“{sid}” sahnasiga ({lay}) kamida {want} ta element{shots} kerak — hozir {n} ta.",
        "shots": " (skrinshot)",
        "no_asset": "“{sid}” — skrinshotsiz screen sahnasi. Iltimos, skrinshot yuboring va nomini ayting.",
        "empty": "“{sid}” sahnasida ({lay}) {field} boʻsh — iltimos, yozing ({hint}).",
        "hint_headline": "1–5 soʻz", "hint_stat": "masalan, 3x yoki 50%", "hint_other": "qisqa",
        "long_headline": "“{sid}” sahnasi: sarlavhada {n} ta soʻz bor — ekrandagi matn 1–5 soʻz boʻlishi kerak.",
        "sug_shot": "{product} ilovasining “{topic}”ni koʻrsatuvchi skrinshoti “{sid}” sahnasiga yordam beradi (u unga "
                    "yaqinlashadigan 'screen' sahnasiga aylanishi mumkin).",
        "sug_focus": "“{sid}” sahnasi: matn gapirayotgan qism uchun 'focus' toʻrtburchagini bering ({asset} ning 0..1 "
                     "ulushlarida), shunda kamera unga yaqinlashadi.",
        "sug_logo": "Kvadrat logo.png (shaffof fon) sarlavhada va tugmada koʻrsatiladi; uni brand.logo sifatida qoʻshing.",
        "sug_rec": "Asosiy amalning 3–8 soniyalik ekran yozuvi (MP4) — masalan, “{sid}” sahnasi uchun — videoni "
                   "skrinshotlardan jonliroq qiladi.",
        "sug_more": "Mahsulot faqat {n} ta sahnada koʻrinadi. Yana 2–4 ta haqiqiy skrinshot (har bir imkoniyatga bittadan) "
                    "videoni ishonarliroq qiladi.",
        "sug_presenter": "Boshlovchi yoqilgan, lekin portreti yoʻq: xayoliy boshlovchini tasvirlovchi "
                         "presenter.portrait_prompt qoʻshing.",
        "the_product": "mahsulot", "promo": "{product} promo",
        "hi": "Salom! “{title}” uchun materiallar uchun rahmat.",
        "all_good": "Kerakli hamma narsa bor — brif, tarjimalar va fayllar joyida. Oʻzgartirish shart emas.",
        "blocking": "Videoni tugatishdan oldin baʼzi narsalar yetishmaydi yoki tuzatilishi kerak:",
        "optional": "Kerakli hamma narsa bor. Bir nechta ixtiyoriy qoʻshimchalar videoni yaxshiroq qiladi:",
        "sections": ["Yetishmayotgan fayllar", "Almashtirish kerak boʻlgan fayllar", "Yetishmayotgan tarjimalar",
                     "Tuzatish kerak boʻlgan matn", "Takliflar (ixtiyoriy)"],
        "how": "Qanday javob berish kerak:",
        "how_files": "- Fayllarni aynan yuqoridagi nomlar bilan yuboring (skrinshotlar PNG, yozuvlar MP4).",
        "how_json": "- Oʻzgartirgan har bir sahnani bir xil \"id\" bilan toʻliq JSON obyekt qilib bering, masalan, "
                    "{{\"id\": \"hook\", \"layout\": \"hero\", ...}}",
        "how_maps": ", har bir matn maydoni {{{maps}}} koʻrinishidagi xarita boʻlsin.",
        "how_len": "- Ovoz matni koʻpi bilan 18 soʻzli bitta tabiiy gap boʻlsin; ekrandagi sarlavhalar 1–5 soʻz.",
    },
}


def _txt(v, lang: str, main: str) -> str:
    """The text of a field in one language (a plain string is used for every language)."""
    if isinstance(v, dict):
        return str(v.get(lang) or "").strip()
    return str(v or "").strip()


def _words(s: str) -> int:
    return len(s.split())


def build(brief: dict, assets: dict, lang: str = "en") -> dict:
    lang = lang if lang in LANGS else "en"
    L = T[lang]
    labels, fields = LABELS[lang], FIELD_NAMES[lang]
    issues: list[dict] = []
    main = brief.get("language") or "en"
    langs = brief.get("languages") or [main]
    product = brief.get("product") or L["the_product"]
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
        where = L["scene"].format(sid=sid) if sid else L["logo"]
        if not m:
            if name in seen_missing:
                continue
            seen_missing.add(name)
            what = (L["want_logo"] if role == "logo" else L["want_phone"] if dev == "phone"
                    else L["want_browser"] if dev == "browser" else L["want_any"])
            add("missing_file", L["missing_file"].format(name=name, where=where, what=what), file=name, scene=sid)
            continue
        if role == "logo" and m.get("type") != "image":
            add("invalid_file", L["logo_video"].format(name=name), file=name)
            continue
        w, h = m.get("w") or 0, m.get("h") or 0
        if not w or not h:
            add("invalid_file", L["unreadable"].format(name=name, where=where), file=name, scene=sid)
            continue
        if role in ("screen", "screens"):
            kind = L["recording"] if m.get("type") == "video" else L["screenshot"]
            if dev == "phone" and w > h:
                add("orientation", L["landscape_phone"].format(name=name, where=where, w=w, h=h, kind=kind),
                    file=name, scene=sid)
            elif dev == "browser" and h > w and w < 1000:
                add("orientation", L["portrait_browser"].format(name=name, where=where, w=w, h=h, kind=kind),
                    file=name, scene=sid)
            minw = MIN_W.get(dev or "phone", 720)
            short = min(w, h) if dev != "browser" else w
            if short < minw:
                add("low_resolution", L["low_res"].format(name=name, where=where, w=w, h=h,
                                                          need=L["need_phone"] if dev != "browser" else L["need_browser"]),
                    file=name, scene=sid)
            if m.get("type") == "video" and (m.get("duration") or 0) > 10.5:
                add("long_recording", L["long_rec"].format(name=name, where=where, s=m["duration"]), file=name, scene=sid)

    # ---------------------------------------------------------- translations
    if len(langs) > 1:
        miss = B.missing_translations(brief)
        for lg in langs:
            per = [(sid, m[lg]) for sid, m in miss.items() if lg in m]
            if per:
                lst = "; ".join(f"“{sid}”: {', '.join(fields.get(f, f) for f in fs)}" for sid, fs in per)
                add("translation", L["translation"].format(label=labels.get(lg, lg), lg=lg, n=len(per), lst=lst), lang=lg)

    # ---------------------------------------------------------- text: word limit, empty fields
    for s in scenes:
        sid, lay = s.get("id"), s.get("layout")
        for lg in langs:
            say = _txt(s.get("say"), lg, main)
            if not say:
                if lg == main:          # other languages: listed under missing translations
                    in_lang = L["in_lang"].format(label=labels.get(lg, lg)) if len(langs) > 1 else ""
                    add("empty_text", L["no_say"].format(sid=sid, lay=lay, in_lang=in_lang), scene=sid, lang=lg)
                continue
            n = _words(say)
            if n > B.SAY_WORDS_SOFT:
                add("too_long", L["too_long_say"].format(sid=sid, lgs=f" ({lg})" if len(langs) > 1 else "", n=n,
                                                         m=B.SAY_WORDS_SOFT), scene=sid, lang=lg)
        for f in NEEDS.get(lay, []):
            if f == "items":
                items = s.get("items") or []
                want = 2 if lay in ("chips", "list", "screens") else 1
                if len(items) < want:
                    add("empty_text", L["few_items"].format(sid=sid, lay=lay, want=want, n=len(items),
                                                            shots=L["shots"] if lay == "screens" else ""), scene=sid)
            elif f == "asset":
                if not s.get("asset"):
                    add("missing_file", L["no_asset"].format(sid=sid), scene=sid)
            elif not _txt(s.get(f), main, main):
                hint = L["hint_headline"] if f == "headline" else L["hint_stat"] if f == "stat" else L["hint_other"]
                add("empty_text", L["empty"].format(sid=sid, lay=lay, field=fields.get(f, f), hint=hint), scene=sid)
            elif f == "headline" and _words(_txt(s.get(f), main, main)) > 7:
                add("too_long", L["long_headline"].format(sid=sid, n=_words(_txt(s.get(f), main, main))), scene=sid)

    # ---------------------------------------------------------- suggestions
    sug = []
    shots = [s for s in scenes if s.get("layout") in ("screen", "screens")]
    textonly = [s for i, s in enumerate(scenes) if s.get("layout") in ("chips", "list") and 0 < i < len(scenes) - 1]
    for s in textonly[:2]:
        # quote the text in the message's language when the brief has it
        topic = (_txt(s.get("headline"), lang, main) if lang in langs else "") or _txt(s.get("headline"), main, main) \
            or _txt(s.get("say"), main, main)[:60]
        sug.append({"type": "suggestion", "scene": s.get("id"),
                    "text": L["sug_shot"].format(product=product, topic=topic, sid=s.get("id"))})
    for s in shots:
        if s.get("layout") == "screen" and not s.get("focus") and assets.get(s.get("asset"), {}).get("type") == "image":
            sug.append({"type": "suggestion", "scene": s.get("id"),
                        "text": L["sug_focus"].format(sid=s.get("id"), asset=s.get("asset"))})
            break
    if not logo:
        sug.append({"type": "suggestion", "text": L["sug_logo"]})
    if shots and not any(m.get("type") == "video" for m in assets.values()):
        target = shots[0]
        sug.append({"type": "suggestion", "scene": target.get("id"), "text": L["sug_rec"].format(sid=target.get("id"))})
    if len(shots) < 2 and len(scenes) >= 4:
        sug.append({"type": "suggestion", "text": L["sug_more"].format(n=len(shots))})
    if pres and pres.get("enabled") and not pres.get("portrait") and not pres.get("portrait_prompt"):
        sug.append({"type": "suggestion", "text": L["sug_presenter"]})
    issues += sug[:5]

    counts = {}
    for it in issues:
        counts[it["type"]] = counts.get(it["type"], 0) + 1
    blocking = [i for i in issues if i["type"] != "suggestion"]
    return {"message": message(brief, issues, lang), "issues": issues, "counts": counts, "ok": not blocking, "lang": lang}


SECTIONS = [
    ("missing_file",),
    ("invalid_file", "orientation", "low_resolution", "long_recording"),
    ("translation",),
    ("too_long", "empty_text"),
    ("suggestion",),
]


def message(brief: dict, issues: list[dict], lang: str = "en") -> str:
    L = T[lang if lang in LANGS else "en"]
    product = brief.get("product") or L["the_product"]
    title = brief.get("title") or L["promo"].format(product=product)
    blocking = [i for i in issues if i["type"] != "suggestion"]
    lines = [L["hi"].format(title=title)]
    if not issues:
        lines += ["", L["all_good"]]
        return "\n".join(lines) + "\n"
    lines.append(L["blocking"] if blocking else L["optional"])
    n = 0
    for head, types in zip(L["sections"], SECTIONS):
        items = [i for i in issues if i["type"] in types]
        if not items:
            continue
        n += 1
        lines += ["", f"{n}. {head}"]
        lines += [f"   - {i['text']}" for i in items]
    langs = brief.get("languages") or [brief.get("language")]
    files = any(i["type"] in ("missing_file", "invalid_file", "orientation", "low_resolution", "long_recording") for i in issues)
    lines += ["", L["how"]]
    if files:
        lines.append(L["how_files"])
    maps = ", ".join(f'"{lg}": "..."' for lg in langs)
    lines += [L["how_json"].format() + (L["how_maps"].format(maps=maps) if len(langs) > 1 else "."), L["how_len"]]
    return "\n".join(lines) + "\n"
