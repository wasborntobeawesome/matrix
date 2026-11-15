# Video Studio brief

The **brief** is the script for one promo video, as JSON. The source AI (a coding agent, a chat
assistant, or an AI writing into the shared inbox folder) writes it together with screenshots and
optional screen recordings. People review and edit it in the studio, then render an MP4.

One brief can make the video in **several languages** (uz, ru, en, ar) and **several formats**
(9:16, 1:1, 16:9): a render makes one MP4 per language × format, each with `.srt`/`.vtt` subtitles
and a cover PNG. Old single-language briefs keep working unchanged.

- JSON Schema: `GET http://127.0.0.1:8001/api/brief-spec`
- The prompt below: `GET http://127.0.0.1:8001/api/ai-request` (also the **Copy request for AI** button)
- Full example: [`example/brief.json`](example/brief.json) with assets in [`example/`](example/)

## Ways to hand it over

**1. Chat AI.** Paste the prompt below (fill in the product part). The AI answers with the JSON and
a list of screenshots. Save its JSON as `brief.json`, collect the screenshots under the names it
asked for, and drop everything onto the studio's *New project* drop zone.

**2. Coding agent, HTTP API** (multipart: one `brief` part + any number of files):

```sh
curl -F "brief=@brief.json;type=application/json" \
     -F "files=@home.png" -F "files=@goals.png" -F "files=@insights.png" \
     -F "files=@web-dashboard.png" -F "files=@add-expense.mp4" \
     http://127.0.0.1:8001/api/projects
# -> 201 {"id": "nimbo-3f9a", "warnings": [...], "url": "/#/p/nimbo-3f9a", ...}

# then, optionally, voice + render without the UI:
curl -X POST http://127.0.0.1:8001/api/projects/nimbo-3f9a/render -H "Content-Type: application/json" -d "{}"
curl http://127.0.0.1:8001/api/jobs/<job id>          # poll: status, stage, progress
curl -OJ "http://127.0.0.1:8001/api/projects/nimbo-3f9a/renders/<name>.mp4?download=1"
```

**Brief → finished MP4 in one call ("Make video").** It runs the voice-over, frees the GPU (Ovoz unloads
its models), generates every AI layer the brief asks for (b-roll `prompt`s, the presenter; quality
`final` by default) and renders the video, one step after another:

```sh
curl -X POST http://127.0.0.1:8001/api/projects/nimbo-3f9a/make -H "Content-Type: application/json" -d "{\"quality\": \"final\"}"
# -> 202 {"id": "<pipeline id>", "status": "queued", "stages": {"voice": {...}, "unload": {...}, "ai": {...}, "render": {...}}}
curl http://127.0.0.1:8001/api/projects/nimbo-3f9a/make     # poll: status, current stage, per-stage progress/eta/message
# when status == "done": result.renders lists every file (<lang>-<format>-<timestamp>.mp4), result.render the first
curl -OJ "http://127.0.0.1:8001/api/projects/nimbo-3f9a/renders/<name>.mp4?download=1"
curl -OJ "http://127.0.0.1:8001/api/projects/nimbo-3f9a/renders/<name>.srt?download=1"   # also .vtt and the cover .png
# failed or cancelled?  POST /api/make/<pipeline id>/retry resumes at the stage that failed
```

AI layers are slow (minutes per clip/presenter, see the studio README). Without `background`/`presenter` in
the brief, the AI stage finishes instantly.

JSON-only (no files) also works: `curl -H "Content-Type: application/json" -d @brief.json http://127.0.0.1:8001/api/projects`.

**3. Shared folder inbox.** Create a folder inside `video/inbox/` and put `brief.json` plus the asset
files in it (flat, or assets in an `assets/` subfolder):

```
video/inbox/
  nimbo-launch/            <- any folder name
    brief.json
    home.png
    goals.png
    add-expense.mp4
```

The studio checks every 3 s. When the files have stopped changing it imports the folder as a new
project and moves it to `video/inbox/_imported/<name>-<timestamp>/` (with `IMPORTED.txt` naming the
project). If something is wrong, it writes `ERROR.txt` into the folder; fix the files and it retries.

## Rules the studio enforces

- Images: PNG, JPG, WebP, ≤ 30 MB, ≤ 60 megapixels. Recordings: MP4, MOV, WebM, ≤ 300 MB, first
  90 s used; converted to silent VP9 WebM on import, so `demo.mp4` becomes `demo.webm` and the brief
  is updated automatically.
- File names are sanitised to `A-Z a-z 0-9 . _ -`; use simple names like `home.png`.
- Up to 12 scenes. Unknown fields are kept; small mistakes are fixed with a warning, not rejected.

## Languages, translations and formats

```json
"language": "uz",                          // main language (first of `languages`)
"languages": ["uz", "ru", "ar"],           // optional; default [language]
"format": "9:16",
"formats": ["9:16", "1:1", "16:9"],        // optional; default [format]
"voices": {                                // optional; default: a Microsoft voice per language
  "uz": { "model": "edge", "voice": "uz-UZ-MadinaNeural" },
  "ru": { "model": "edge", "voice": "ru-RU-SvetlanaNeural" },
  "ar": { "model": "edge", "voice": "ar-SA-ZariyahNeural" }
}
```

- **Text fields** (`say`, `kicker`, `headline`, `sub`, `callout`, `button`, `url`, `stat`, chip/list
  `items`, `screens` item `label`) are either a plain string, used for every language, or a map with one
  text per language:

  ```json
  { "id": "hook", "layout": "hero",
    "headline": { "uz": "Nimbo'ni ko'ring", "ru": "Знакомьтесь, Nimbo", "ar": "تعرّف على نيمبو" },
    "say": { "uz": "Nimbo'ni ko'ring: pulingizni xotirjam boshqaradigan yangi ilova.",
             "ru": "Знакомьтесь, Nimbo — приложение, с которым деньги наконец под контролем.",
             "ar": "تعرّف على نيمبو، تطبيق الميزانية الذي يجعل المال أكثر هدوءًا." } }
  ```
  Chips/list items: a list of maps (`[{"uz": "Veb", "ru": "Веб"}, ...]`) or one map of lists
  (`{"uz": ["iPhone", "Veb"], "ru": ["iPhone", "Веб"]}`). Keep the same number of items in every language.
- A translation that is missing falls back to the main language. The studio shows a *missing translation*
  warning per scene and language. In a multi-language brief, give `say` as a map: a plain-string `say`
  would be read out in the main language's words.
- `voice` is the main language's voice (same as `voices[language]`).
- **Formats:** 9:16 = 1080×1920 (Reels, Shorts, TikTok), 1:1 = 1080×1080 (feeds), 16:9 = 1920×1080.
  Every layout adapts; you don't write anything per format.
- **Arabic** is laid out right to left: text, lists and progress run RTL, and side-by-side layouts are
  mirrored (text on the right, device on the left).
- **Uzbek:** write apostrophes however you like (`o'`, `g'`, `'`). On screen the studio shows `oʻ gʻ`
  (U+02BB) and the tutuq belgisi `ʼ` (U+02BC): "Nimbo'ni ko'ring" → "Nimboʼni koʻring". The brief keeps
  what you wrote, and the voice-over reads your text as written.

**Music and cover (optional).** The studio user picks the track from the studio's music library, so a
source AI can only suggest a mood:

```json
"music": { "mood": "calm, warm" },                                    // suggestion; the user picks a track
"music": { "track": "builtin-calm-pad", "volume_db": -20, "duck": true, "duck_db": -10,
           "fade_in": 1, "fade_out": 2.5, "start": 0 },                // as the studio stores it
"cover": { "scene": "hook", "t": 2.0 }                                 // cover PNG moment (default: end of scene 1)
```

## Scene layouts

| layout | fields | what you see |
|---|---|---|
| `hero` | `kicker`, `headline`, `sub` | big title with an audio waveform |
| `screen` | `device` (`phone`/`browser`/`none`), `asset`, `focus` {x,y,w,h}, `callout`, `headline`, `kicker`, `sub`, `url` (browser bar), `zoom`, `trim_start` | screenshot or recording in a device frame; the camera zooms into `focus` and a callout points at it |
| `screens` | `device`, `items`: 2–3 × {`asset`, `label`}, `headline`, `sub` | 2–3 screens side by side (phones) or cascaded (browser windows), e.g. before/after or a feature tour |
| `chips` | `headline`, `items` (2–6 short strings) | headline + chips that pop in as the voice names them |
| `stat` | `stat` (e.g. `"3x"`, `"50%"`), `headline`, `sub` | animated counter ring |
| `list` | `headline`, `items` (2–5, may start with an emoji) | list rows that slide in as they are spoken |
| `cta` | `headline`, `sub`, `button`, `url` | call to action with a pulsing button |
| `presenter` | `kicker`, `headline`, `sub` | full-frame AI talking head (9:16) or a tall presenter card next to the text (16:9). Needs the project-level `presenter` (portrait) — see below |

Every scene also has `id` (lowercase, dashes), `say` (the voice-over line, a string or a translation map), optional `pad` (silence
after the line, default 0.4 s; 1.0 s for the last scene) and `duration` (minimum scene length).
Scene length = voice-over length + pad. `focus` is in fractions of the image: `x`,`y` = top-left
corner, `w`,`h` = size, all 0..1 (e.g. the top third: `{"x":0,"y":0,"w":1,"h":0.33}`).

## Optional AI layers (local, slow)

Both are optional; briefs without them render exactly as before. Everything is generated on the
studio PC's GPU (see the AI section of README.md), so the source AI only *suggests* — the studio user
generates and approves.

**Scene background** — any scene may have `background`:

```json
"background": { "type": "brand" }                                   // default (the brand gradient); same as leaving it out
"background": { "type": "broll", "prompt": "slow drifting violet light ribbons on dark navy, soft bokeh", "dim": 0.55, "blur": 0 }
"background": { "type": "broll", "clip": "b-a328a9b1a9", "dim": 0.55, "blur": 4 }   // a clip id from the studio library
```

- `prompt` (from the source AI) is a suggestion: the scene shows the brand gradient until someone clicks
  *Generate new clip* (pre-filled with the prompt) or picks a library clip. The clip is then stored as `clip`.
- Good prompts describe calm, abstract or atmospheric footage: light, textures, slow camera moves,
  objects. No text, logos, UI or recognisable people — the model can't draw readable text, and all
  product information comes from the overlays anyway.
- `dim` 0–0.9 (default 0.55) darkens the clip; `blur` 0–24 px. The clip is cropped to fill the frame,
  slowed down or ping-ponged to cover the scene, and the brand glow stays on top.

**Presenter** — project level:

```json
"presenter": { "enabled": true, "mode": "corner", "scenes": "all", "quality": "draft",
               "portrait": "p-3fd7042cac", "portrait_prompt": "friendly fictional presenter in her 30s, navy blazer" }
```

- `mode`: `corner` = a round bubble (9:16: above the caption pill on the left; 16:9: bottom-right) on the
  scenes in `scenes` (`"all"` or a list of ids). `full` = no bubble; only scenes with layout `presenter`
  show it. `presenter` scenes are full-frame in both modes.
- `portrait`: a studio library portrait id or an uploaded image asset. A source AI can't supply one —
  leave it out and give `portrait_prompt` (a *fictional* person) instead; the studio user generates or
  uploads a portrait (uploads require confirming it is fictional or used with permission).
- `quality`: `draft` (448×800) or `final` (576×1024). The talking head is lip-synced to the voice-over
  and has to be regenerated when the voice-over changes (the UI shows it as stale; the video renders
  without the presenter until it is regenerated).

## The AI request (copy-paste prompt)

<!-- AI-REQUEST:START -->
```text
I need material for a short promo video (15–45 seconds) about a product. Please prepare:

PRODUCT
- Name: <product name>
- What it is / who it is for: <one or two sentences>
- Key features to show: <3–5 features>
- Languages of the video: <e.g. uz, ru, ar — any of uz | ru | en | ar; the first one is the main language>
- Formats: <any of 9:16 vertical (default), 1:1 square, 16:9 horizontal>

1) SCREENSHOTS (and optional screen recordings)
- 3–6 screenshots of the real product. Mobile: portrait, native resolution (e.g. 1170x2532 or
  1080x2340), no device frame, status bar visible is fine. Web: 1440x900 (or 2880x1800), browser
  content only, no browser window chrome. Use ONE theme for all (light or dark), consistent.
- Optional: 1–2 screen recordings, 3–8 seconds each, MP4/MOV, no audio needed, showing one action
  (e.g. adding an item). Record at the same size as the screenshots.
- NO personal data: use demo accounts, fake names, fake emails, fake balances. No real customer data,
  no notifications from real people, no API keys.
- File names: lowercase, dashes, describe the screen: home.png, add-expense.mp4, web-dashboard.png.
  Optional logo: logo.png (square, transparent background).
- Screenshots are shared by every language (one set is enough).

2) THE SCRIPT as a JSON "brief", exactly in this format:
- 5–8 scenes, total 15–45 seconds. First scene = hook, last scene = call to action.
- "say" = the voice-over line for the scene: one sentence, at most ~18 words, natural spoken
  language. On-screen text is short: headline 1–5 words, callout 2–5 words.
- TRANSLATIONS: when there is more than one language, write every text field ("say", "headline",
  "sub", "kicker", "callout", "button", list/chip items, screen labels) as a map with one natural,
  idiomatic text per language: {"uz": "...", "ru": "...", "ar": "..."}. Translate the meaning, not
  word by word; keep each language's line about as long as the others. Product names and numbers may
  stay as they are. Give every item list the same number of items in every language.
- Uzbek: Latin script; typing o' g' and ' is fine (the studio shows oʻ gʻ ʼ).
- Use the "screen" layout for most scenes (one screenshot or recording each) and point "focus" at
  the part of the image that the line talks about (fractions 0..1 of the image: x, y = top-left
  corner, w, h = size). Use "screens" to show 2–3 screens at once. Use hero/chips/stat/list/cta
  for the hook, lists of features, a number, and the ending.
- "asset" values must be exactly the file names you give me.
- Brand colours as hex; "background" must be dark (the video style is dark).
- OPTIONAL: "music": {"mood": "<2-3 words, e.g. calm, warm>"} — the studio user picks a track.
- OPTIONAL (generated locally by the studio, slow): for 1–3 scenes that are not screenshots
  (hook, chips, stat, CTA), you may suggest a moving background footage prompt:
  "background": {"type": "broll", "prompt": "<calm abstract footage, no text/logos/people>"}.
  If a friendly on-screen host would help this product (explainers, onboarding, services), add
  "presenter": {"enabled": true, "mode": "corner", "scenes": "all", "portrait_prompt": "<a fictional
  presenter>"} and optionally one scene with layout "presenter". Leave both out when unsure.

Fields:
  title, product, language (main: uz|ru|en|ar), languages ([main, ...others]),
  format ("9:16"|"1:1"|"16:9"), formats ([...]),
  brand {primary, accent, background, logo?}, voices {lang: {model?, voice?}} (optional; leave out if unsure),
  scenes[]: each {id, layout, say, ...layout fields}   (text fields: "text" or {"uz": "...", "ru": "..."})
  layouts:
    hero    {kicker?, headline, sub?}
    screen  {device: "phone"|"browser"|"none", asset, focus?: {x,y,w,h}, callout?, headline?, kicker?, sub?, url?}
    screens {device, items: [{asset, label?}] (2–3), headline?, sub?}
    chips   {headline, items: ["short", "strings"] (2–6)}
    stat    {stat: "3x", headline, sub?}
    list    {headline, items: ["😊 Item one", ...] (2–5)}
    cta     {headline, sub?, button?, url?}
    presenter {kicker?, headline, sub?}            (AI talking head; needs top-level "presenter")
  optional per scene: pad (seconds of silence after the line, default 0.4), duration (minimum seconds),
    background {type: "broll", prompt, dim?, blur?}   (AI b-roll suggestion; default = brand gradient)
  optional top level: music {mood}, presenter {enabled, mode: "corner"|"full", scenes: "all"|[ids], portrait_prompt}

Example (a fictional app, two languages, two formats):
{
  "title": "Nimbo launch promo",
  "product": "Nimbo",
  "language": "en",
  "languages": ["en", "ru"],
  "format": "9:16",
  "formats": ["9:16", "1:1"],
  "brand": { "primary": "#3D7BFF", "accent": "#34D399", "background": "#0A1024" },
  "music": { "mood": "calm, warm" },
  "scenes": [
    { "id": "hook", "layout": "hero",
      "kicker": { "en": "NEW APP", "ru": "НОВОЕ ПРИЛОЖЕНИЕ" },
      "headline": { "en": "Meet Nimbo", "ru": "Знакомьтесь, Nimbo" },
      "sub": { "en": "Budgeting that feels calm", "ru": "Бюджет без стресса" },
      "say": { "en": "Meet Nimbo, the budgeting app that finally makes money feel calm.",
               "ru": "Знакомьтесь, Nimbo — приложение, с которым деньги наконец под контролем." } },
    { "id": "dashboard", "layout": "screen", "device": "phone", "asset": "home.png",
      "focus": { "x": 0.05, "y": 0.135, "w": 0.9, "h": 0.215 },
      "callout": { "en": "All accounts in one place", "ru": "Все счета в одном месте" },
      "headline": { "en": "Everything at a glance", "ru": "Всё под рукой" },
      "say": { "en": "See every account and card in one simple dashboard.",
               "ru": "Все счета и карты — на одном простом экране." } },
    { "id": "add", "layout": "screen", "device": "phone", "asset": "add-expense.mp4",
      "headline": { "en": "Add expenses in seconds", "ru": "Расходы за секунды" },
      "say": { "en": "Log a purchase in two taps, and Nimbo sorts it for you.",
               "ru": "Запишите покупку в два касания, а Nimbo сам её рассортирует." } },
    { "id": "goals", "layout": "screens", "device": "phone",
      "headline": { "en": "Save with a plan", "ru": "Копите по плану" },
      "items": [ { "asset": "goals.png", "label": { "en": "Goals", "ru": "Цели" } },
                 { "asset": "insights.png", "label": { "en": "Insights", "ru": "Аналитика" } } ],
      "say": { "en": "Set goals, check your insights, and watch your savings grow.",
               "ru": "Ставьте цели, смотрите аналитику и следите, как растут сбережения." } },
    { "id": "everywhere", "layout": "chips",
      "headline": { "en": "Everywhere", "ru": "Везде" },
      "items": { "en": ["iPhone", "Android", "Web", "Shared budgets"], "ru": ["iPhone", "Android", "Веб", "Общий бюджет"] },
      "say": { "en": "It syncs between iPhone, Android and the web, with shared budgets for two.",
               "ru": "Работает на iPhone, Android и в вебе, с общим бюджетом на двоих." },
      "background": { "type": "broll", "prompt": "Slow drifting ribbons of violet and teal light over a dark navy background, soft bokeh, cinematic" } },
    { "id": "cta", "layout": "cta", "url": "nimbo.example", "pad": 1.0,
      "headline": { "en": "Try Nimbo free", "ru": "Попробуйте Nimbo" },
      "sub": { "en": "No card needed", "ru": "Без карты" },
      "button": { "en": "Get Nimbo", "ru": "Скачать Nimbo" },
      "say": { "en": "Try Nimbo free today.", "ru": "Попробуйте Nimbo бесплатно уже сегодня." } }
  ]
}

For a single language, plain strings are fine: "headline": "Meet Nimbo".

Reply with: (a) the brief.json in one code block, (b) a list of every file it references with a
one-line description of what each screenshot/recording must show.
```
<!-- AI-REQUEST:END -->

## Voice

`voice.model` / `voice.voice` (main language) and `voices.<lang>.model` / `.voice` (every language)
are ids from the Ovoz TTS app (`GET http://localhost:8000/api/models`, also proxied at `/api/voices`).
If left out, the studio picks `edge` (Microsoft online voices) with a default voice per language
(uz: `uz-UZ-MadinaNeural`, ru: `ru-RU-SvetlanaNeural`, en: `en-US-AvaNeural`, ar: `ar-SA-ZariyahNeural`).
The user can change them in the editor (the voice fields follow the language tab); only models whose
`languages` include that language are offered.
