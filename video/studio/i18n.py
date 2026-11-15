"""Server-side messages in the UI language (EN / OʻZ).

The studio's code writes its user-facing messages (brief warnings, upload errors, history summaries, job
stages and errors, "Make video" notices) in English; the API translates them for the person reading them.
The language comes from the client: the `X-UI-Lang` header (the UI's api() helper sends it) or a `ui_lang`
query parameter (EventSource / links); default English.

`tr(text, "uz")` matches the text against PATTERNS (regex -> Uzbek template with the same named groups);
a message that matches nothing is returned unchanged (English), so a missing translation never breaks
anything. Composite texts (history summaries "a · b · c", "; "-joined errors) are translated part by part.
"""
from __future__ import annotations

import re
from functools import lru_cache

LANGS = ("en", "uz")
LANG_NAMES = {"uz": "oʻzbekcha", "ru": "ruscha", "en": "inglizcha", "ar": "arabcha"}


def ui_lang(request) -> str:
    """'uz' or 'en' from the X-UI-Lang header or the ui_lang query parameter."""
    try:
        v = request.headers.get("x-ui-lang") or request.query_params.get("ui_lang") or ""
    except AttributeError:
        v = ""
    v = str(v).strip().lower()[:2]
    return v if v in LANGS else "en"


# (English regex, Uzbek template). Named groups are copied into the template. Order matters: specific first.
_P = [
    # ---------------------------------------------------------------- brief warnings (brief.normalize)
    (r"product name missing — using 'Product'", "mahsulot nomi yoʻq — 'Product' ishlatiladi"),
    (r"languages: '(?P<x>.*)' not supported \(uz\|ru\|en\|ar\) — ignored",
     "languages: '\\g<x>' qoʻllab-quvvatlanmaydi (uz|ru|en|ar) — eʼtiborsiz qoldirildi"),
    (r"language '(?P<x>.*)' not supported \(uz\|ru\|en\|ar\) — using en",
     "'\\g<x>' tili qoʻllab-quvvatlanmaydi (uz|ru|en|ar) — en ishlatiladi"),
    (r"formats: '(?P<x>.*)' unknown \(9:16\|1:1\|16:9\) — ignored",
     "formats: '\\g<x>' nomaʼlum (9:16|1:1|16:9) — eʼtiborsiz qoldirildi"),
    (r"format '(?P<x>.*)' unknown — using 9:16", "'\\g<x>' formati nomaʼlum — 9:16 ishlatiladi"),
    (r"brand\.(?P<k>\w+) '(?P<v>.*)' is not a hex colour — using default",
     "brand.\\g<k> '\\g<v>' hex rang emas — standart rang ishlatiladi"),
    (r"template '(?P<v>.*)' is not a valid id — using (?P<d>[\w-]+)",
     "'\\g<v>' shablon identifikatori notoʻgʻri — \\g<d> ishlatiladi"),
    (r"template '(?P<v>.*)' is not installed — the video uses (?P<d>[\w-]+) until it is imported",
     "'\\g<v>' shabloni oʻrnatilmagan — u import qilinguncha video \\g<d> bilan chiqadi"),
    (r"broll_provider '(?P<v>.*)': this ComfyUI workflow is not installed in the studio \(video/comfy-workflows\) — "
     r"Make video uses (?P<d>[\w:-]+) until it is",
     "broll_provider '\\g<v>': bu ComfyUI workflow studiyada oʻrnatilmagan — u oʻrnatilguncha “Video yaratish” "
     "\\g<d> ishlatadi"),
    (r"broll_provider '(?P<v>.*)' unknown \((?P<l>.*)\) — using (?P<d>[\w:-]+)",
     "broll_provider '\\g<v>' nomaʼlum (\\g<l>) — \\g<d> ishlatiladi"),
    (r"(?P<lg>\w\w): missing translations in (?P<n>\d+) scene\(s\) \((?P<ids>.*)\) — the main language \((?P<m>\w\w)\) "
     r"is used there",
     "\\g<lg>: \\g<n> ta sahnada tarjima yoʻq (\\g<ids>) — u yerda asosiy til (\\g<m>) ishlatiladi"),
    (r"brand\.logo '(?P<v>.*)' is not an uploaded asset", "brand.logo '\\g<v>' yuklangan fayllar orasida yoʻq"),
    (r"(?P<n>\d+) scenes — keeping the first (?P<m>\d+)", "\\g<n> ta sahna — birinchi \\g<m> tasi qoldirildi"),
    (r"scene (?P<i>\d+) is not an object — skipped", "\\g<i>-sahna obyekt emas — oʻtkazib yuborildi"),
    (r"scene '(?P<s>[^']*)': layout '(?P<v>.*)' unknown — using hero",
     "'\\g<s>' sahnasi: '\\g<v>' maketi nomaʼlum — hero ishlatiladi"),
    (r"scene '(?P<s>[^']*)'(?: \((?P<lg>\w\w)\))?: voice-over has (?P<n>\d+) words \(aim for <= (?P<m>\d+)\)",
     "'\\g<s>' sahnasi: ovoz matnida \\g<n> ta soʻz bor (\\g<m> tadan oshmasin)"),
    (r"scene '(?P<s>[^']*)': (?P<l>chips|list) needs 'items'", "'\\g<s>' sahnasi: \\g<l> uchun 'items' kerak"),
    (r"scene '(?P<s>[^']*)': stat layout needs 'stat'", "'\\g<s>' sahnasi: stat maketi uchun 'stat' kerak"),
    (r"scene '(?P<s>[^']*)': device '(?P<v>.*)' unknown — using phone",
     "'\\g<s>' sahnasi: '\\g<v>' qurilmasi nomaʼlum — phone ishlatiladi"),
    (r"scene '(?P<s>[^']*)': screen needs 'asset'", "'\\g<s>' sahnasi: screen uchun 'asset' (skrinshot) kerak"),
    (r"scene '(?P<s>[^']*)': (?:asset|item asset) '(?P<v>.*)' is not an uploaded asset",
     "'\\g<s>' sahnasi: '\\g<v>' fayli yuklanmagan"),
    (r"scene '(?P<s>[^']*)': focus ignored \(w/h must be > 0, fractions 0\.\.1\)",
     "'\\g<s>' sahnasi: focus eʼtiborsiz qoldirildi (w/h > 0 boʻlishi kerak, 0..1 ulushlar)"),
    (r"scene '(?P<s>[^']*)': screens works best with 2-3 items",
     "'\\g<s>' sahnasi: screens 2–3 ta element bilan yaxshi chiqadi"),
    (r"scene '(?P<s>[^']*)': screens shows at most 3 items", "'\\g<s>' sahnasi: screens koʻpi bilan 3 ta elementni koʻrsatadi"),
    (r"music ignored \(must be an object\)", "music eʼtiborsiz qoldirildi (obyekt boʻlishi kerak)"),
    (r"music\.generate ignored \(give a prompt, genre or mood\)",
     "music.generate eʼtiborsiz qoldirildi (prompt, janr yoki kayfiyat bering)"),
    (r"scene '(?P<s>[^']*)': background ignored \(must be an object\)",
     "'\\g<s>' sahnasi: background eʼtiborsiz qoldirildi (obyekt boʻlishi kerak)"),
    (r"scene '(?P<s>[^']*)': background type '(?P<v>.*)' unknown — using the brand gradient",
     "'\\g<s>' sahnasi: '\\g<v>' fon turi nomaʼlum — brend gradiyenti ishlatiladi"),
    (r"scene '(?P<s>[^']*)': background\.provider '(?P<v>.*)': this ComfyUI workflow is not installed in the studio "
     r"\(video/comfy-workflows\) — Make video uses the default until it is",
     "'\\g<s>' sahnasi: '\\g<v>' ComfyUI workflow oʻrnatilmagan — u oʻrnatilguncha standart generator ishlatiladi"),
    (r"scene '(?P<s>[^']*)': background\.provider '(?P<v>.*)' unknown — using the project default",
     "'\\g<s>' sahnasi: '\\g<v>' video generatori nomaʼlum — loyiha standarti ishlatiladi"),
    (r"(?P<w>.*): must be a transition name like 'fade' — ignored",
     "\\g<w>: 'fade' kabi oʻtish nomi boʻlishi kerak — eʼtiborsiz qoldirildi"),
    (r"(?P<w>.*): '(?P<v>.*)' unknown \(built in: (?P<l>.*)\) — ignored",
     "\\g<w>: '\\g<v>' nomaʼlum (tayyorlari: \\g<l>) — eʼtiborsiz qoldirildi"),
    (r"presenter ignored \(must be an object\)", "presenter eʼtiborsiz qoldirildi (obyekt boʻlishi kerak)"),
    (r"presenter\.mode '(?P<v>.*)' unknown — using corner", "presenter.mode '\\g<v>' nomaʼlum — corner ishlatiladi"),
    (r"presenter\.scenes: unknown scene id\(s\) (?P<v>.*) — ignored",
     "presenter.scenes: nomaʼlum sahna(lar) \\g<v> — eʼtiborsiz qoldirildi"),
    (r"template_options\.(?P<k>[\w-]+): not a number — using the default",
     "template_options.\\g<k>: son emas — standart qiymat ishlatiladi"),
    (r"template_options\.(?P<k>[\w-]+): '(?P<v>.*)' is not a hex colour — using the default",
     "template_options.\\g<k>: '\\g<v>' hex rang emas — standart qiymat ishlatiladi"),
    (r"template_options\.(?P<k>[\w-]+): '(?P<v>.*)' is not one of (?P<l>.*) — using the default",
     "template_options.\\g<k>: '\\g<v>' quyidagilardan biri emas: \\g<l> — standart qiymat ishlatiladi"),
    (r"template_options\.(?P<k>[\w-]+): the template has no such option — ignored",
     "template_options.\\g<k>: shablonda bunday sozlama yoʻq — eʼtiborsiz qoldirildi"),
    (r"no brief given — created a blank project", "brif berilmadi — boʻsh loyiha yaratildi"),
    (r"a brief was included but ignored here — use PUT /api/projects/\{id\}",
     "brif ham yuborilgan, lekin bu yerda eʼtiborsiz qoldirildi — PUT /api/projects/{id} dan foydalaning"),
    # ---------------------------------------------------------------- brief errors
    (r"brief is not valid JSON: (?P<e>.*)", "brif yaroqli JSON emas: \\g<e>"),
    (r"brief must be a JSON object", "brif JSON obyekt boʻlishi kerak"),
    (r"brief needs a non-empty 'scenes' array", "brifda boʻsh boʻlmagan 'scenes' roʻyxati boʻlishi kerak"),
    (r"no usable scenes", "yaroqli sahna yoʻq"),
    (r"invalid brief", "brif notoʻgʻri"),
    # ---------------------------------------------------------------- uploads / files (store.py, server.py)
    (r"(?P<f>.+): unsupported file type \(images: png jpg webp; recordings: mp4 mov webm\)",
     "\\g<f>: bu fayl turi qoʻllab-quvvatlanmaydi (rasmlar: png jpg webp; yozuvlar: mp4 mov webm)"),
    (r"(?P<f>.+): empty file", "\\g<f>: fayl boʻsh"),
    (r"(?P<f>.+): too large \((?P<s>\d+) MB; limit (?P<l>\d+) MB\)",
     "\\g<f>: juda katta (\\g<s> MB; chegara \\g<l> MB)"),
    (r"(?P<f>.+): too large \(limit (?P<l>\d+) MB\)", "\\g<f>: juda katta (chegara \\g<l> MB)"),
    (r"(?P<f>.+): not a valid image", "\\g<f>: yaroqli rasm emas"),
    (r"(?P<f>.+): image too large \((?P<s>\d+x\d+)\)", "\\g<f>: rasm juda katta (\\g<s>)"),
    (r"(?P<f>.+): unsupported image format (?P<v>.*)", "\\g<f>: rasm formati qoʻllab-quvvatlanmaydi: \\g<v>"),
    (r"(?P<f>.+): not a readable video", "\\g<f>: videoni oʻqib boʻlmadi"),
    (r"(?P<f>.+): could not convert video \((?P<e>.*)\)", "\\g<f>: videoni oʻgirib boʻlmadi (\\g<e>)"),
    (r"request body too large \(limit (?P<l>\d+) MB\)", "soʻrov juda katta (chegara \\g<l> MB)"),
    (r"request too large", "soʻrov juda katta"),
    (r"project not found", "loyiha topilmadi"),
    (r"bad project id", "loyiha identifikatori notoʻgʻri"),
    (r"asset not found", "fayl topilmadi"),
    (r"bad asset name", "fayl nomi notoʻgʻri"),
    (r"render not found", "render topilmadi"),
    (r"snapshot not found", "versiya topilmadi"),
    (r"job not found", "vazifa topilmadi"),
    (r"no voice-over yet", "hali ovoz yoʻq"),
    (r"send multipart/form-data with one or more files", "bir yoki bir nechta faylni multipart/form-data bilan yuboring"),
    (r"a render is running for this project", "bu loyiha uchun render ketmoqda"),
    (r"an AI job is running for this project", "bu loyiha uchun AI vazifasi ketmoqda"),
    (r"a “Make video” pipeline is running for this project", "bu loyiha uchun “Video yaratish” ketmoqda"),
    (r"delete the \.mp4; its subtitles and cover go with it", ".mp4 ni oʻchiring — subtitrlar va muqova u bilan oʻchadi"),
    (r"cross-origin request refused: changes can only be made from the studio's own page",
     "boshqa saytdan kelgan soʻrov rad etildi: oʻzgarishlar faqat studiyaning oʻz sahifasidan qilinadi"),
    (r"cross-site request refused: changes can only be made from the studio's own page",
     "boshqa saytdan kelgan soʻrov rad etildi: oʻzgarishlar faqat studiyaning oʻz sahifasidan qilinadi"),
    (r"workers must be a number \(1-16\)", "workers son boʻlishi kerak (1–16)"),
    # ---------------------------------------------------------------- history (history.py)
    (r"Version before history was recorded", "Tarix yozilishidan oldingi versiya"),
    (r"Created \((?P<n>\d+) scenes\)", "Yaratildi (\\g<n> ta sahna)"),
    (r"Imported", "Import qilindi"),
    (r"Created", "Yaratildi"),
    (r"Restored the version from (?P<w>.*)", "\\g<w> dagi versiya tiklandi"),
    (r"No changes", "Oʻzgarish yoʻq"),
    (r"(?P<n>\d+) scenes", "\\g<n> ta sahna"),
    (r"title: (?P<a>[^·]*) → (?P<b>[^·]*)", "sarlavha: \\g<a> → \\g<b>"),
    (r"product name: (?P<a>[^·]*) → (?P<b>[^·]*)", "mahsulot nomi: \\g<a> → \\g<b>"),
    (r"template: (?P<a>[^·]*) → (?P<b>[^·]*)", "shablon: \\g<a> → \\g<b>"),
    (r"main language: (?P<a>[^·]*) → (?P<b>[^·]*)", "asosiy til: \\g<a> → \\g<b>"),
    (r"main format: (?P<a>[^·]*) → (?P<b>[^·]*)", "asosiy format: \\g<a> → \\g<b>"),
    (r"transition: (?P<a>[^·]*) → (?P<b>[^·]*)", "oʻtish: \\g<a> → \\g<b>"),
    (r"title", "sarlavha"), (r"product name", "mahsulot nomi"), (r"main language", "asosiy til"),
    (r"languages", "tillar"), (r"main format", "asosiy format"), (r"formats", "formatlar"),
    (r"brand colours / logo", "brend ranglari / logo"), (r"voice", "ovoz"), (r"voices", "ovozlar"),
    (r"captions", "subtitrlar"), (r"music", "musiqa"), (r"cover", "muqova"), (r"presenter", "boshlovchi"),
    (r"template", "shablon"), (r"template options", "shablon sozlamalari"),
    (r"added scene (?P<v>[^·]*)", "sahna qoʻshildi: \\g<v>"),
    (r"deleted scene (?P<v>[^·]*)", "sahna oʻchirildi: \\g<v>"),
    (r"reordered scenes", "sahnalar tartibi oʻzgardi"),
    (r"scene (?P<v>“[^·]*)", "sahna \\g<v>"),
    # ---------------------------------------------------------------- render / voice jobs (jobs.py)
    (r"voice-over", "ovoz"),
    (r"word timing", "soʻz vaqtlari"),
    (r"word timing: done", "soʻz vaqtlari: tayyor"),
    (r"word timing: (?P<v>.*)", "soʻz vaqtlari: \\g<v>"),
    (r"voice: done", "ovoz: tayyor"),
    (r"voice: (?P<v>.*) · retry (?P<a>\d+)/(?P<b>\d+)", r"ovoz: \g<v> · qayta urinish \g<a>/\g<b>"),
    (r"(?P<n>\d+) lines? failed \((?P<v>.*)\)", r"\g<n> ta satr ovozlanmadi (\g<v>)"),
    (r"voice-over: (?P<n>\d+) lines? failed \((?P<v>.*)\) — choose what to do with (?:it|them) below",
     r"ovoz: \g<n> ta satr ovozlanmadi (\g<v>) — quyida nima qilishni tanlang"),
    (r"voice-over: (?P<n>\d+) lines? failed — continuing with (?:it|them) silent",
     r"ovoz: \g<n> ta satr ovozlanmadi — ular ovozsiz davom ettiriladi"),
    (r"voice: (?P<v>.*)", "ovoz: \\g<v>"),
    (r"timeline \+ audio mix: (?P<v>.*)", "vaqt chizigʻi + audio miks: \\g<v>"),
    (r"(?P<t>.*): starting browser", "\\g<t>: brauzer ishga tushmoqda"),
    (r"(?P<t>.*): frame (?P<a>\d+)/(?P<b>\d+) · ~(?P<s>\d+)s left", "\\g<t>: kadr \\g<a>/\\g<b> · ~\\g<s> s qoldi"),
    (r"(?P<t>.*): frame (?P<a>\d+)/(?P<b>\d+)", "\\g<t>: kadr \\g<a>/\\g<b>"),
    (r"(?P<t>.*): finishing MP4", "\\g<t>: MP4 yakunlanmoqda"),
    (r"(?P<t>.*): analysing audio", "\\g<t>: audio tahlil qilinmoqda"),
    (r"render (?P<v>.*) failed: (?P<e>.*)", "render \\g<v> muvaffaqiyatsiz: \\g<e>"),
    (r"(?P<e>.*)\. Fix the voice settings, or render without voice-over\.",
     "\\g<e>. Ovoz sozlamalarini tuzating yoki ovozsiz render qiling."),
    (r"voice-over skipped: (?P<e>.*)", "ovoz oʻtkazib yuborildi: \\g<e>"),
    (r"Ovoz rejected the request: (?P<e>.*)", "Ovoz soʻrovni rad etdi: \\g<e>"),
    (r"(?P<lg>\w\w): (?P<n>\d+) scene\(s\) without voice-over \(silence\)",
     "\\g<lg>: \\g<n> ta sahna ovozsiz (jimlik)"),
    (r"lost", "aloqa uzildi"),
    (r"cancelled", "bekor qilindi"),
    # ---------------------------------------------------------------- "Make video" + AI queue
    (r"waiting: “Make video” is running AI jobs on the GPU — voice-over continues afterwards",
     "kutilmoqda: “Video yaratish” GPU’da AI vazifalarini bajarmoqda — ovoz keyin davom etadi"),
    (r"waiting: “Make video” is generating the voice-over \(Ovoz has the GPU\)",
     "kutilmoqda: “Video yaratish” ovoz yaratmoqda (GPU Ovoz’da)"),
    (r"waiting for GPU memory: needs ~(?P<a>[\d.]+) GB, (?P<b>[\d.]+) GB free \((?P<w>.*)\)",
     "GPU xotirasi kutilmoqda: ~\\g<a> GB kerak, \\g<b> GB boʻsh (\\g<w>)"),
    (r"Ovoz is using (?P<v>[\d.]+) GB", "Ovoz \\g<v> GB ishlatmoqda"),
    (r"other apps are using it", "uni boshqa dasturlar ishlatmoqda"),
    (r"asking ComfyUI to free its GPU memory first…", "avval ComfyUI’dan GPU xotirasini boʻshatish soʻralmoqda…"),
    (r"cancelling…", "bekor qilinmoqda…"),
    (r"starting without the free-memory check…", "boʻsh xotira tekshiruvisiz boshlanmoqda…"),
    (r"reused from the library", "kutubxonadan qayta ishlatildi"),
    (r"The studio server restarted while this job was (?P<w>\w+)\. Click Retry to run it again\.",
     "Bu vazifa bajarilayotganda (\\g<w>) studiya serveri qayta ishga tushdi. Qayta ishga tushirish uchun “Qayta urinish”ni bosing."),
    (r"The studio server restarted during this pipeline\. Retry resumes it\.",
     "Bu jarayon davomida studiya serveri qayta ishga tushdi. “Qayta urinish” uni davom ettiradi."),
    (r"interrupted by a server restart", "server qayta ishga tushgani sababli toʻxtadi"),
    (r"checking the workflow against ComfyUI \(/object_info\)…", "workflow ComfyUI bilan tekshirilmoqda (/object_info)…"),
    (r"uploading the image to ComfyUI…", "rasm ComfyUI’ga yuklanmoqda…"),
    (r"queued in ComfyUI…", "ComfyUI navbatida…"),
    (r"trimming and checking the audio \(loudness, spectrum\)…", "audio qirqilmoqda va tekshirilmoqda (balandlik, spektr)…"),
    (r"voice-over skipped \(silent scenes\): (?P<e>.*)", "ovoz oʻtkazib yuborildi (jim sahnalar): \\g<e>"),
    (r"music: reused the generated track '(?P<v>.*)'", "musiqa: yaratilgan '\\g<v>' treki qayta ishlatildi"),
    (r"scene '(?P<s>[^']*)': clip (?P<c>\S+) is missing and there is no prompt — brand gradient",
     "'\\g<s>' sahnasi: \\g<c> klipi yoʻq va prompt ham yoʻq — brend gradiyenti"),
    (r"scene '(?P<s>[^']*)': video generator '(?P<v>.*)' is not installed — using (?P<d>[\w:-]+)",
     "'\\g<s>' sahnasi: '\\g<v>' video generatori oʻrnatilmagan — \\g<d> ishlatiladi"),
    (r"scene '(?P<s>[^']*)': image '(?P<v>.*)' to animate is missing — text to video instead",
     "'\\g<s>' sahnasi: jonlantiriladigan '\\g<v>' rasmi yoʻq — oʻrniga matndan video"),
    (r"presenter skipped: (?P<e>.*)", "boshlovchi oʻtkazib yuborildi: \\g<e>"),
    (r"(?P<n>.+) failed \(exit (?P<c>-?\d+)\): (?P<e>.*)", "\\g<n> xato bilan tugadi (kod \\g<c>): \\g<e>"),
]

# history summaries are " · "-joined parts; error lists are "; "-joined
_SEPARATORS = (" · ", "; ")


@lru_cache(maxsize=1)
def _compiled():
    return [(re.compile(p + r"\Z", re.S), t) for p, t in _P]


def _one(text: str) -> str | None:
    for rx, tpl in _compiled():
        m = rx.match(text)
        if m:
            return m.expand(tpl)
    return None


def tr(text, lang: str = "en"):
    """`text` in `lang` (only 'uz' is translated; anything unknown stays English)."""
    if lang != "uz" or not isinstance(text, str) or not text:
        return text
    hit = _one(text)
    if hit is not None:
        return hit
    for sep in _SEPARATORS:
        if sep in text:
            parts = text.split(sep)
            out = [tr(p, lang) for p in parts]
            if out != parts:
                return sep.join(out)
    # "<label> — <changes>" (history: "Imported — 3 scenes", "Restored … — title: a → b")
    if " — " in text:
        a, _, b = text.partition(" — ")
        ta, tb = tr(a, lang), tr(b, lang)
        if (ta, tb) != (a, b):
            return f"{ta} — {tb}"
    return text


def tr_list(items, lang: str = "en"):
    if lang != "uz" or not isinstance(items, list):
        return items
    return [tr(x, lang) for x in items]


def tr_job(j, lang: str = "en"):
    """A job / pipeline view with its user-facing fields translated (a copy)."""
    if lang != "uz" or not isinstance(j, dict):
        return j
    j = dict(j)
    for k in ("message", "error"):
        if isinstance(j.get(k), str):
            j[k] = tr(j[k], lang)
    if isinstance(j.get("notices"), list):
        j["notices"] = [tr(x, lang) if isinstance(x, str) else
                        ({**x, "text": tr(x.get("text"), lang)} if isinstance(x, dict) else x) for x in j["notices"]]
    if isinstance(j.get("stages"), dict):
        j["stages"] = {k: tr_job(v, lang) for k, v in j["stages"].items()}
    res = j.get("result")
    if isinstance(res, dict) and isinstance(res.get("warnings"), list):
        j["result"] = {**res, "warnings": tr_list(res["warnings"], lang)}
    return j


def tr_history(items, lang: str = "en"):
    if lang != "uz":
        return items
    return [{**h, **{k: tr(h[k], lang) for k in ("summary", "label") if isinstance(h.get(k), str)}} for h in items]
