# Video Studio

A local web app for making promo videos about other products. It builds them from screenshots,
screen recordings and an AI-written script (the **brief**). People review and edit the brief in the
browser, preview it live, generate the voice-over with the Ovoz TTS app, and render MP4s in 9:16, 1:1
and 16:9, in several languages from one brief (uz, ru, en, ar), with background music, word-accurate
captions, `.srt`/`.vtt` subtitles and a cover image per video. The look comes from a **template**
(built in: *Aurora*, *Clean*, *Bold*; import your own as a zip — see
[`../templates/TEMPLATES.md`](../templates/TEMPLATES.md)). The editor has undo/redo, a version history, a
follow-up message for the source AI, and an English / Uzbek interface. A live system monitor (header widget +
monitor page) shows GPU, VRAM by owner, CPU and RAM; optional local AI layers (b-roll from three video generators,
image-to-video, a talking-head presenter, AI music) run in warm model workers, and any ComfyUI workflow can be a
video generator through a managed local ComfyUI (HTTP API only). Everything is free/open source: FastAPI,
Playwright Chromium, FFmpeg, faster-whisper, numpy, NVML/psutil and plain HTML/JS.

## Run

```bat
video\studio\start.bat
```
or
```sh
video/.venv/Scripts/python -m uvicorn server:app --app-dir video/studio --host 127.0.0.1 --port 8001
```

Open **http://127.0.0.1:8001/**. Voice-over needs the Ovoz app at `http://127.0.0.1:8000`
(override with the `OVOZ_URL` env var). Without Ovoz you can still edit, preview with estimated
timing, and render a silent video. The UI shows a banner when Ovoz is down.

One-time setup (already done in this repo's venv):
`video/.venv/Scripts/python -m pip install fastapi "uvicorn[standard]" python-multipart faster-whisper`, plus
playwright+Chromium, pillow, numpy, pyyaml (shared with `video/html`). The word-timing model is downloaded
once: `video/.venv/Scripts/python -c "from faster_whisper import download_model; download_model('small')"`
(≈ 480 MB in the Hugging Face cache; without it captions use the old estimate).

Example assets for a fictional app ("Nimbo") are in [`example/`](example/). Click
**Start from example** in the UI, or regenerate the assets with `example/make_example.py`.
[`BRIEF.md`](BRIEF.md) is the hand-off doc for the source AI: format, prompt, API and inbox.

## Workflow

1. The source AI supplies a brief plus assets in one of three ways:
   - **UI**: drop `brief.json` and the files on the projects page (or *New project*, then the Assets tab).
   - **HTTP API**: `POST /api/projects` as multipart (see the curl example in BRIEF.md).
   - **Inbox folder**: `video/inbox/<name>/brief.json` + files are imported automatically within ~3 s
     and moved to `video/inbox/_imported/`. Errors go to `ERROR.txt` in that folder.
2. In the editor: reorder, add, duplicate or delete scenes (drag, or Alt+↑/↓). Edit text, pick
   screenshots, and drag a focus rectangle on the image. Set the languages (toggle UZ/RU/EN/AR, ★ = main),
   the formats (toggle 9:16 / 1:1 / 16:9), voice model/voice per language (only available Ovoz models
   that speak that language), speed, brand colours, logo and captions. The scene form has a tab per
   language plus **Side by side**; scenes with a missing translation show `⚠ RU` in the list and a
   warning in the form. The **Music** tab picks / uploads background music. Every change autosaves and
   updates the live preview, which has its own language and format switch.
3. **Generate voice** synthesises only the missing or changed lines (every language) and measures the
   word timing; ▶ next to a scene plays its clip. **Render MP4 (n)** renders every language × format.
   The finished files appear under Renders with their cover, `.srt` and `.vtt` (play with subtitles /
   download).

## Architecture

```
video/
  html/                 renderer (shared with the original HTML option)
    index.html          stage; ?data=<same-origin data.js url> | ?embed (live preview, driven by postMessage)
    transitions.js      scene transitions (lift, fade, slide, zoom, wipe, blur, cut), shared with the studio UI
    app.js              mount(timeline) -> seek(t) is a pure function of t; templates (tokens -> CSS variables,
                        template.css, template.js layouts/transitions/hooks, run with a frozen clock); layouts hero, chips,
                        stat, list, cta, screen, screens, presenter; phone/browser frames; focus zoom (inside the screen) +
                        callout; recordings (<video>, seek awaits 'seeked'); 9:16, 1:1 (.square) and 16:9 (.land);
                        right-to-left (.rtl, mirrored side-by-side layouts); ?cover = no captions/progress
    render.py           --project DIR [--timeline F] [--cover T --cover-out PNG]: 8 headless Chromium pages ->
                        JPEG -> ffmpeg (H.264 + AAC); waveforms follow `analysis_audio` (the voice) when
                        the muxed `audio` is the music mix
  shared/voicelib.py    Ovoz TTS client, silence trim, joining, caption chunks (estimated or from word
                        times), Uzbek display text (oʻ gʻ ʼ), length estimate
  studio/
    server.py           FastAPI app + static UI + /renderer and /shared/fonts mounts
    brief.py            JSON Schema + lenient normalize/validate; localize(brief, lang); missing_translations
    store.py            projects on disk, safe uploads, VP9 transcode, thumbnails
    build.py            per-scene voice cache, timeline per language + format, voice-over, music mix
    align.py            word timing: faster-whisper (CPU int8) + alignment onto the script, cache, fallback
    music.py            music library (add_track/list/delete), builtin numpy tracks, ducking + loudness mix
    exports.py          .srt / .vtt cues from the aligned captions, cover time
    templates.py        template discovery, manifest checks, zip import, timeline.template for the renderer
    history.py          version history: snapshot per save (coalesced), list, restore, change summaries
    followup.py         "Ask the AI for missing material" message
    jobs.py             background queues: voice (1 worker), render (1 at a time, subprocess)
    inbox.py            shared-folder watcher
    aijobs.py           GPU job queue for the AI layers (b-roll, portrait, presenter, music) — runs them in warm workers
    workers.py          warm AI model workers (one video/ai/worker.py per model family): RAM budget, idle timeout, release
    providers.py        b-roll video generators (wan22-5b, fastwan-5b, wan22-a14b, comfy:<workflow>): licences, presets, estimates
    comfy.py            ComfyUI: managed server (start/stop/adopt, flags, log), HTTP + WebSocket client, /free, cancel
    comfyflows.py       ComfyUI workflow library: API-format parsing, mapping auto-detection, /object_info validation, fill
    sysmon.py           1 Hz system sampler (NVML + psutil + PDH per-process GPU counters), 30-min history, stage markers
    gpu.py              VRAM status for the AI queue (reads the sampler; nvidia-smi + PowerShell fallback)
    library.py          shared b-roll + portrait library (video/library/)
    presenter.py        talking-head planning, segment cache, assembly
    pipeline.py         "Make video": voice -> free GPU (Ovoz /api/unload) -> AI jobs -> render
    static/             UI: index.html, app.js, ai.js (AI page + components), monitor.js (header widget + #/monitor),
                        style.css, strings.js (English UI text), strings-uz.js (Uzbek UI text)
    example/            fictional sample brief + screenshots + recording
  templates/<id>/       manifest.json (+ template.css, template.js, fonts, previews); aurora, clean, bold,
                        _starter (copy me), validate.py, TEMPLATES.md
  projects/<id>/        (gitignored) project.json, history/, assets/, assets.json, thumbs/,
                        build/{voice/<hash>.wav, voiceover[-<lang>].wav, mix-<lang>.wav (+.json),
                        timeline-<lang>-<fmt>.json, data-<lang>-<fmt>.js},
                        renders/<lang>-<fmt>-<timestamp>.mp4 (+ .json metadata, .srt, .vtt, .png cover)
  inbox/                (gitignored) drop folders here
  library/              (gitignored) broll/<id>/, portraits/<id>/, music/<id>/, align/<hash>.json,
                        ai-jobs.json, ai-timings.json
  ai/                   local generative-AI scripts + their own venv (see ai/README.md)
  comfyui/              setup.py (tracked) + the local ComfyUI install, venv, models, logs (gitignored)
  comfy-workflows/<id>/ workflow_api.json + manifest.json + preview.jpg per ComfyUI workflow; README.md (guide)
```

- **Voice cache.** Each line's clip is `build/voice/<sha1(text, model, voice, rate, language, style)>.wav`
  (48 kHz mono, trimmed). Changing one line only re-synthesises that line. Scenes without a clip get
  an estimated length (characters ÷ speaking rate) and silence, so preview and render always line up.
- **Scene length** = speech + pad (0.4 s, last scene 1.0 s), at least `duration`, with per-layout
  minimums (screen 3.2 s). A recording extends its scene to its own length (max 10 s).
- **Recordings** are transcoded on import to VP9 WebM (no audio, 30 fps, long side ≤ 1600, GOP 30)
  because Playwright's Chromium has no H.264 decoder. In render mode `seek(t)` sets `currentTime`,
  waits for `seeked` plus two animation frames, and only then is the screenshot taken.
- **Live preview** is the same renderer page in a *sandboxed* iframe (`/renderer/index.html?embed`,
  `sandbox="allow-scripts"`: an opaque origin, so template code cannot touch the editor or the API). The
  editor posts `{type: "load", tl}` after each autosave and `{type: "seek", t}` on every animation frame,
  driven by the voice-over `<audio>` clock (or a wall clock before voice exists).
- **Renders** run `html/render.py --project <dir> --progress` as a subprocess. Progress lines become
  job progress, a cancel kills the process, and the MP4 is moved into `renders/` only when complete.

## API

All JSON unless noted. Errors are `{"detail": ...}`; invalid briefs return 422 `{"errors": [...]}`.
Interactive docs: http://127.0.0.1:8001/docs

| Method & path | What it does |
|---|---|
| `GET /api/health` | `{studio, ovoz, ovoz_error?}` |
| `GET /api/voices` | proxy of Ovoz `GET /api/models` (503 when Ovoz is down) |
| `GET /api/brief-spec` | brief JSON Schema |
| `GET /api/ai-request` | copy-paste prompt for a chat AI (text/plain, from BRIEF.md) |
| `GET /api/inbox` | inbox path + recent import events |
| `GET /api/projects` | project summaries |
| `POST /api/projects` | create. multipart: `brief` (JSON text or file) + any files; or JSON brief; or `{"blank": true, "product", "language", "languages"?, "format", "formats"?}` → `{id, warnings, ...}` |
| `POST /api/projects/example?format=9:16` | new project from `example/` |
| `GET /api/projects/{id}` | project.json + assets + renders + voiced map + warnings + jobs |
| `PUT /api/projects/{id}` | replace the brief (`{"brief": {...}}` or the brief itself) → normalised brief + warnings |
| `DELETE /api/projects/{id}` | delete project |
| `POST /api/projects/{id}/assets` | multipart upload (images png/jpg/webp ≤ 30 MB; recordings mp4/mov/webm ≤ 300 MB) |
| `DELETE /api/projects/{id}/assets/{name}` | delete asset |
| `GET /api/projects/{id}/files/{name}` · `/thumbs/{name}` | asset file (range requests) · JPEG thumbnail |
| `GET /api/projects/{id}/timeline` | preview timeline (estimated lengths where no voice yet) |
| `GET /api/projects/{id}/voiceover.wav` | current joined voice-over |
| `GET /api/projects/{id}/voice/{scene}.wav` | one scene's clip (404 if not generated) |
| `POST /api/projects/{id}/voice` | job: `{"scene"?: id, "force"?: bool, "lang"?: "ru" \| "langs"?: [...]}` (default every language) + word timing |
| `POST /api/projects/{id}/render` | job: `{"allow_silent"?, "skip_voice"?, "workers"?: 8, "langs"?: [...], "formats"?: [...]}` → one MP4 per language × format; result `{renders: [...], outputs: [...]}`; 409 if one is already queued/running for the project |
| `GET /api/jobs/{job}` · `POST /api/jobs/{job}/cancel` | `{status: queued/running/done/error/cancelled, stage, progress 0..1, message, error, result, position}` |
| `GET /api/projects/{id}/jobs` | recent jobs of a project |
| `GET /api/projects/{id}/renders` | renders, newest first: `{name, language, format, duration, srt, vtt, cover, timing: {aligned, voiced}, music: {lufs, true_peak, …}, …}` |
| `GET …/renders/{name}[?download=1]` · `DELETE …/renders/{name}.mp4` | stream or download a render **or** its `.srt` / `.vtt` / cover `.png` · delete (with its sidecars) |
| `GET /api/projects/{id}/timeline?lang=ru&format=1:1` | preview timeline of one language + format (adds `music` for the preview, `timing`, per-scene `timing: aligned/estimated`) |
| `GET /api/projects/{id}/voiceover.wav?lang=` · `…/voice/{scene}.wav?lang=` | voice-over / one clip of a language (default: main language) |
| `GET /api/library/music` · `GET …/music/{id}/track.{ext}` | music library (with `used_in`) · the audio file |
| `POST /api/library/music` | multipart: audio file (mp3/wav/ogg/m4a ≤ 60 MB) + **`licence`** (required) + `title`, `artist`, `mood`, `bpm`, `project` (use it there) |
| `DELETE /api/library/music/{id}` | delete an uploaded/generated track (projects using it render without music) |
| `GET /api/align/status` | word-timing model, loaded, error |
| `GET /api/templates` · `GET /api/templates/{id}` · `GET /api/templates/schema` | installed templates (built-ins first, broken ones with `error`) · one · manifest schema |
| `POST /api/templates[?replace=1]` · `DELETE /api/templates/{id}` | import a template zip (multipart `file` or raw body; 422 `{errors: [...]}` lists every problem) · delete (not built-ins) |
| `GET /templates/{id}/{file}` | template files for the preview (never cached: hot reload) |
| `GET /api/projects/{id}/timeline?template=<id>` | preview another template without saving it (the picker's live preview) |
| `GET /api/projects/{id}/history` · `GET …/history/{n}` · `POST …/history/{n}/restore` | version list (newest first: `{id, created, updated, kind, summary, scenes}`) · one snapshot with its brief · restore (saved as a new version) |
| `GET /api/projects/{id}/followup[?format=text][&lang=uz]` | ready-to-send message for the source AI (English, or Uzbek with `lang=uz`) + `issues`, `counts`, `ok` |

Render jobs go through one queue, so only one render runs at a time and the others wait. Voice jobs
use their own queue. A render first generates any missing voice lines. If Ovoz fails it errors,
unless `allow_silent` is set: then it renders with silence and the result carries warnings. The UI
offers this when Ovoz is down.

Uploads are checked by extension and by content: Pillow verify for images, ffmpeg probe and
transcode for video. Size limits are enforced while streaming. File names are reduced to
`[A-Za-z0-9._-]`, and asset/render paths are resolved inside the project folder, so `..` gives 404.
The server binds to 127.0.0.1 only and has no authentication. It is meant for local use.

## Languages, formats, music, word timing, exports

### Several languages from one brief

- `languages: ["uz", "ru", "ar"]` (the first is the main language, also `language`). Every text field
  (`say`, `headline`, `sub`, `kicker`, `callout`, `button`, `url`, `stat`, items, screen labels) is a
  plain string (all languages) or a map `{"uz": "...", "ru": "..."}`. Old single-language briefs are
  unchanged. See [BRIEF.md](BRIEF.md#languages-translations-and-formats).
- `voices: {lang: {model, voice, rate?, style?}}`, default a Microsoft (`edge`) voice per language;
  `voice` stays the main language's voice.
- `brief.localize(brief, lang)` makes the single-language brief that voice-over, timeline and renderer
  use. A missing translation falls back to the main language and is reported per scene and language
  (`GET /api/projects/{id}` → `missing`, the editor's ⚠ badges and warning box).
- Voice clips are cached per language (the hash includes the language and voice). The main language
  keeps `build/voiceover.wav` (the AI presenter lip-syncs to it); others use `voiceover-<lang>.wav`.
- **Uzbek on screen:** for `uz`, headline, sub, kicker, items, callout, button, labels and the product
  name get `oʻ gʻ` (U+02BB) and the tutuq belgisi `ʼ` (U+02BC), like the captions: "Nimbo'ni ko'ring" →
  "Nimboʼni koʻring" (a product name or a capitalised name ending in *o* + apostrophe + case suffix is
  the tutuq belgisi, not the letter oʻ). Applied at render/preview time; the stored brief and the
  text sent to the TTS are unchanged. ru/en/ar are untouched.
- **Arabic** (`ar`) renders right to left: `#stage.rtl` with `dir=rtl`, no letter-spacing (it breaks
  Arabic joining), right-aligned lists, the progress bar fills from the right, and side-by-side
  layouts are mirrored (16:9 and 1:1 `screen`: text right, device left; `screens`: first screen on the
  right; 16:9 `presenter`: card left; the 1:1 presenter bubble moves to the bottom-right). 9:16 layouts
  are centred and only flip text direction.

### Formats: 9:16, 1:1, 16:9

- `formats: ["9:16", "1:1", "16:9"]` (default `[format]`). 1:1 = 1080×1080: header y 34–84, content
  y 100–885, caption pill at y≈958, progress at 1036. Every layout has a 1:1 version: hero, chips
  (2 columns), stat, list, cta, `screen` (phones next to the text, browsers / wide shots below it),
  `screens`, `presenter` (card on the right), presenter bubble (bottom-left, devices and text keep
  clear of it), b-roll backgrounds (cover crop).
- A render (`POST …/render`, the *Render MP4 (n)* button, and the "Make video" render stage) makes one
  MP4 per language × format: `renders/<lang>-<9x16|1x1|16x9>-<timestamp>.mp4`, plus `.json`, `.srt`,
  `.vtt` and the cover `.png`. The renders list shows the latest batch and folds older ones away.
- **Caption over the phone:** in 9:16 `screen`/`screens` scenes the device now ends at y 1325 and that
  scene's caption pill moves down to y≈1410 (still above the 1540 px bottom safe zone and the progress
  bar), so it sits below the phone instead of covering its screen. In 16:9 the device box ends at y 900
  (it used to touch the pill at the end of the camera drift); in 1:1 at y 885.
  `preview/v2-phase1-caption-over-phone-before-after-9x16.png`, `…-caption-clearance-before-after-16x9.png`.
- The AI presenter speaks the main language; other languages render without the bubble (the still
  portrait on `presenter` scenes). B-roll is generated for the main format's orientation.

### Background music

- Library: `video/library/music/<id>/track.(mp3|wav|ogg|m4a)` + `meta.json` `{id, title, artist, licence,
  source: upload|generated|builtin, duration, mood?, bpm?, prompt?, created, lufs, ext}`. `music.py` is
  the only module that touches it; `music.add_track(file, meta)` is the entry point for uploads now and an
  AI music generator later (it checks the file with ffmpeg, measures duration and loudness).
- Three builtin tracks are synthesised with numpy on first start (pads, plucks, FM piano, kick/hat;
  seamless loops; "CC0, generated locally"): *Calm pad* 72 bpm 27 s, *Bright pluck* 104 bpm 18 s,
  *Soft pulse* 90 bpm 21 s.
- Editor **Music** tab: list with ▶ preview, pick one per project, delete (uploads), upload (licence
  required), and the mix settings. Project setting:
  `music: {track, volume_db: -20, duck: true, duck_db: -10, fade_in: 1, fade_out: 2.5, start: 0}`.
- **Mix** (render, `build.music_mix` → `music.mix`): voice-over loudness-matched to −20 LUFS, track to
  −12 LUFS (8 LU apart, like raw speech vs a mastered song), then `volume_db`; ducking by `duck_db`
  while the voice speaks, from the voice-over's own level (10 ms RMS gate at peak − 38 dB, 0.15 s
  look-ahead, 0.3 s hold, 80 ms attack / 450 ms release, so short gaps between lines don't pump);
  equal-power loop cross-fade when the track is shorter than the video; fade in/out; then two-pass
  EBU R128 `loudnorm` of the whole mix to **−14 LUFS, −1.5 dBTP** (linear). Cached per voice-over +
  settings (`build/mix-<lang>.wav`). The renderer's waveforms still follow the voice, not the music.
- **Editor preview** plays the track with the voice, gain from the same reference levels and the duck
  envelope sampled every 0.1 s (`timeline.music.duck`), fades included (approximate: HTML audio volume).

### Caption and word timing (faster-whisper)

- After the voice-over (voice job, render job, "Make video" voice stage) each clip is transcribed with
  **faster-whisper `small`, CPU, int8, 8 threads** (`align.py`; the GPU stays free) with word timestamps,
  and the recognised words are mapped onto the KNOWN script words: both sides are normalised (case,
  apostrophes, Arabic diacritics/alef forms, ё, Cyrillic Uzbek → Latin), then a character-level
  `difflib.SequenceMatcher` alignment gives each script word the times of its matched characters;
  unmatched words are interpolated between neighbours by character weight. The script is given to
  Whisper as `initial_prompt`; when fewer than 85 % of the words match, the clip is transcribed again
  without it (Whisper hallucinated "Редактор субтитров…" on one Russian line with the prompt) and the
  better result is kept. Below 50 % the scene falls back to the old estimate.
- Cached per audio hash + script + model in `library/align/<hash>.json`. The live preview queues
  missing alignments in the background and picks them up; renders align synchronously.
- Used for the caption chunks (start/end of their words), the karaoke word highlight (per-word times
  in `captions[].words`) and the chip/list/screens item pop-ins. Scenes show **aligned / estimated** in
  the scene list and the scene form; renders store `timing: {aligned, voiced}`.
- Missing model or `faster-whisper` → estimated timing, as before. `STUDIO_WHISPER_MODEL` /
  `STUDIO_WHISPER_THREADS` / `STUDIO_WHISPER_PROMPT=0` override the defaults.

**Measured** (Ryzen 7 9800X3D; ground truth = Microsoft Edge TTS `WordBoundary` events for 3 lines per
language, 23–36 words each; error = |aligned word start − true start|):

| Model (CPU int8) | Speed (×real time) | uz mean / p90 | ru mean / p90 | en mean / p90 | ar mean / p90 |
|---|---|---|---|---|---|
| base | 0.17 | 57 / 110 ms | 70 / 135 ms | 70 / 115 ms | 61 / 105 ms |
| **small** (default) | **0.20** (≈ 0.75 s per 4 s line, load 0.6 s) | **55 / 130 ms** | **51 / 100 ms** | **51 / 100 ms** | **44 / 85 ms** |
| medium | 0.53 | 126 / 175 ms (+85 ms bias) | 122 / 173 ms | 119 / 200 ms | 139 / 240 ms |
| large-v3-turbo | 0.56 | 59 / 110 ms | 56 / 100 ms | 51 / 100 ms | 41 / 100 ms |
| old estimate (given the exact speech window) | – | 49 / 89 ms | 140 / 404 ms | 101 / 237 ms | 64 / 124 ms |

- `small` is as accurate as `large-v3-turbo` here at 3× the speed; `medium` has a systematic +100 ms
  bias. Median errors are 31–45 ms (about one video frame), mean bias ≤ 13 ms. All 4 languages matched
  100 % of the words with the prompt.
- Honest note: on these evenly paced Microsoft voices the estimate is already good for Uzbek and
  Arabic; alignment mainly fixes lines with pauses and dashes (ru/en). On the local `navoiy` voice all
  3 test lines aligned (100 % / 100 % / 100 % of words).
- Method: the clips were synthesised with the `edge-tts` package (`boundary="WordBoundary"`, the same
  Microsoft voices Ovoz uses) and decoded without trimming, then `align.transcribe` + `align.align_words`
  were compared word by word with the boundary events. The Studio itself never calls Edge directly.

### Exports per render

- `<name>.srt` and `<name>.vtt`: cues from the aligned captions, ≤ 2 lines × 42 characters, ≤ 6 s, split
  at sentence ends, never across scenes or real pauses; Arabic SRT lines are wrapped in RLE/PDF marks.
- `<name>.png` cover: a still without captions and progress bar (`render.py --cover T --cover-out`,
  `index.html?…&cover`). Default: the end of the first scene (everything in, before the exit fade);
  `cover: {scene, t}` in the brief, or *Use this frame as cover* under the preview.
- All three are listed with the render (`GET /api/projects/{id}/renders` → `srt`, `vtt`, `cover`), served
  by `GET …/renders/{name}` (`?download=1`), deleted with the MP4, and the *Play* dialog loads the
  `.vtt` as a subtitle track.

### Verified (2026-11-15, test server on :8011, Playwright + API)

- **3 languages × 3 formats from the UI:** the brief (uz main + ru + ar maps, 9 scenes incl. hero,
  screen phone/recording/browser, screens, stat, list, chips with a b-roll clip, cta) and 5 assets were
  dropped on the projects page; *Calm pad* picked in the Music tab; an AR translation removed (the scene
  showed `⚠ AR: sub`) and restored; *Make video* (draft) clicked. First run with voice: voice stage 63 s
  (27 Edge lines + alignment), render stage 395 s for **9 MP4s**. Final run after the last fixes:
  alignment 28 s (27/27 aligned), render 389 s. Per output: 9:16 48–54 s, 1:1 31–35 s, 16:9 46–48 s for
  39–47 s videos. Durations match the timelines (uz 39.36 → 39.37 s, ru 41.50, ar 46.56 → 46.57 s);
  each has `.srt` (11/15/10 cues), `.vtt` and a cover.
- **Loudness / ducking** (`ffmpeg ebur128` + `astats`, stems written with the final gain): every MP4
  −14.0 LUFS (±0.02), true peak −1.5 dBTP. Voice vs music while speaking: uz 21.5 dB, ru 22.3 dB,
  ar 23.6 dB (music ≈ −38 dBFS RMS under ≈ −16 dBFS voice). In the 2–3 s pause of the `web` scene the
  music comes up by 7.4–8.8 dB (median over the window, incl. the release ramp; the plot shows the full
  10 dB) and is back down before the next word: `preview/v2-phase1-ducking-{uz,ru,ar}.png`.
  Editor preview: music gain −30.5 dB while speaking, −23 dB in that pause.
- **Frames from the MP4s:** `preview/v2-phase1-frames-<lang>-<fmt>.png` (each scene at 80 %),
  `…-uz-apostrophes.png` ("Nimboʼni koʻring"), `…-caption-word-sync-ru.png` (the highlighted word at
  t = 1.30 s is "Nimbo", aligned 1.04–1.48 s), `…-covers.png`, presenter bubble / card layouts with a
  stand-in video `…-presenter-{uz-1x1,ar-1x1,ar-16x9}.png`.
- **UI:** `preview/v2-phase1-ui-*.png` (projects, new editor, music tab, missing translation, side by
  side, AR 1:1 preview playing, Make running/done, renders with covers and subtitle links, AR render
  playing with its `.vtt`). No console errors.
- **Local voice:** a 3-scene Uzbek 1:1 project with Ovoz `navoiy`/`madina` + *Bright pluck*: voice,
  alignment (3/3), mix (−14.0 LUFS) and render in 28 s.
- **Old projects:** 18 stills of 3 old projects rendered from their old `timeline.json` with the new
  renderer: identical except the 9:16 and 16:9 `screen`/`screens` scenes (caption/device change above).
  The old CLI (`render.py` without arguments) stills are pixel-identical. Old briefs normalise with
  `languages: [language]`, `formats: [format]`, `voices: {language: voice}` and no warnings.

## Templates, transitions, history, follow-up, Uzbek UI (v2 phase 2)

### Templates
- `video/templates/<id>/manifest.json` (+ `template.css`, `template.js`, fonts, pictures). **theme** = design
  tokens only (colours, fonts, radii, shadows, caption / progress / logo style, background picture);
  **full** = + CSS and an ES module that can replace any layout (`layouts.<name>.build(scene, ctx)` /
  `seek(el, localT, ctx)`), add transitions and background hooks. Full guide, API, safe zones, Figma
  workflow: [`../templates/TEMPLATES.md`](../templates/TEMPLATES.md).
- Built in: **Aurora** (the original look, the default), **Clean** (light, minimal: app-icon hero, check
  chips, hairline list, *Drift* transition), **Bold** (huge stacked type, stickers, box captions, moving
  bands, *Punch* / *Swipe*). All support every layout, 9:16 / 1:1 / 16:9, Arabic RTL, Cyrillic and Uzbek.
- Brief: `template`, `template_options` (settings the manifest declares), `transition`, `scene.transition`.
  The brand colours keep applying through the tokens (`"primary": "brand.primary"`).
- Editor: **Template** in the settings bar opens a picker docked on the left, so the live preview stays
  visible: thumbnails per format, point at one to see it in the preview, click to use it, its options,
  *Import a template* (zip; every problem is listed), delete imported ones. **↻** re-reads the template
  files (hot reload while you edit a template).
- Determinism: template code runs with a frozen clock, seeded `Math.random` and timers refused; CSS
  animations are paused and seeked; full templates render without compositor hints so a frame never
  depends on the seek order. `video/templates/validate.py` checks manifests, renders every layout × format
  × en/ar/ru/uz, flags overflow / safe-zone / caption collisions / fonts and compares double renders.
- Template code is sandboxed (see *Security* below): render.py serves the page from a virtual origin that
  only exposes the folders the render needs, with no network; the live preview is a sandboxed iframe. A
  template still runs JavaScript inside the render page, so it can draw anything into *its own* video.

### Transitions
`lift` (the original), `fade`, `slide`, `zoom`, `wipe`, `blur`, `cut` (`video/html/transitions.js`) plus
template ones. Project default in the settings bar, per scene in the scene form (“Transition into this
scene”), each with a tiny animated preview (point at it). Pure functions of the cut's progress.

### Undo / redo and version history
- Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y) and ↶ ↷ in the top bar; one step per burst of edits (typing in a field is
  one step). The raw JSON editor keeps the browser's own undo.
- Every save writes a snapshot to `projects/<id>/history/` (an *edit* younger than 45 s is updated in place,
  the last 100 are kept). The **History** tab lists time, kind and a change summary (“template: bold →
  clean · scene “hook”: headline (uz)”) with *Restore*; a restore is saved as a new version and is itself
  undoable (Ctrl+Z or History).

### “Ask the AI for missing material”
Button under the scene list (and `GET /api/projects/{id}/followup`): an English message ready to paste into
the source AI, listing missing or unusable files (a video as logo…), screenshots in the wrong orientation
for their frame or too small, missing translations per language, voice-over lines over 18 words and empty
text a layout needs, and up to five suggestions (a screenshot for a text-only scene, a focus area, a logo, a
screen recording). Copy button in the dialog.

### English / Oʻzbek interface
EN / OʻZ switch in the top bar, remembered per browser (`localStorage`). Every UI string is in
`static/strings.js` (English) and `static/strings-uz.js` (Uzbek Latin with oʻ gʻ ʼ, keys checked to match);
buttons and tabs wrap instead of overflowing. Server messages follow the UI language too: the UI sends
`X-UI-Lang: en|uz` with every API call (or `?ui_lang=uz`), and the server translates brief warnings, upload
errors, error details, history summaries, job / AI-job / "Make video" stage messages, errors and notices
(`i18n.py`: English regex → Uzbek template; anything without a translation stays English). The follow-up
message for the source AI stays **English by default** (AIs work best in it); the dialog has a *Message
language* switch and the API takes `?lang=uz`.

### Verified (2026-11-15, test server on :8011, Playwright + API)

- **Old projects pixel-identical:** 435 stills (15 old timelines: the 9 `nimbo-ade8` language × format
  renders, 4 older projects, the original CLI timeline; each scene at 4 moments incl. the exit transition)
  rendered with the phase-1 renderer and the new one, software raster, one page at a time: 435 / 435
  identical without a template, and identical with `template: aurora` injected (one presenter-video first
  frame differed once and matched on re-render — video decode timing). The old `render.py` CLI still
  renders the Ovoz promo (641 frames, 17 s).
- **Frame grid**, `nimbo-ade8` (uz + ru + ar, 9 scenes = every layout but presenter) × 9:16 / 1:1 / 16:9 ×
  aurora / clean / bold: `preview/v2-phase2-grid-<template>-<lang>.png`. Fixed from these: bold chip labels
  overflowing, stacked headlines measured too small, RTL stat overlapping the header, box captions wrapping
  at half width, bold callouts covering the 1:1 text column, Arabic line height in clean.
- **validate.py** on aurora, clean, bold, _starter and the imported starter: OK (108 frames each, no layout,
  font or determinism problems). It found real issues during development (chip text clipped, a seek-order
  dependent frame caused by `will-change` raster caching → full templates now render without it).
- **Template switching from the UI** on `nimbo-ade8`: aurora → clean → bold → aurora, previews per format
  `preview/v2-phase2-nimbo-<template>-<fmt>.png`; hovering a card previews it live in the editor
  preview. The project was left on aurora, as before.
- **Import:** a broken zip (bad id, empty name, code in a theme, colour "blue", caption style "wavy") was
  rejected with all five errors listed (`v2-phase2-import-errors.png`); `_starter` zipped and imported
  through the UI, applied, rendered (uz 9:16, 39.4 s video in 50.6 s): `v2-phase2-render-starter-frames.png`.
- **Render time per template** (`nimbo` uz, 39.4 s = 1181 frames, 8 workers, incl. encoding):

  | template | 9:16 | 16:9 |
  |---|---|---|
  | aurora | 49.7 s | 44.0 s |
  | clean | 42.1 s | 34.8 s |
  | bold | 33.2 s | 33.4 s |

  Clean and bold are faster: no grid / particles / blurred glass caption to rasterise.
- **Undo / redo:** typing in a headline → Ctrl+Z restored it, Ctrl+Shift+Z redid it, ↶ undid again; a
  template change was undone (Ctrl+Z) and redone (Ctrl+Y). **History:** restoring the oldest version
  reverted the template, Ctrl+Z undid the restore; summaries like “template: bold → clean”
  (`v2-phase2-history*.png`).
- **Follow-up:** a deliberately broken brief (missing `search.png` and `logo.png`, a landscape and a
  400×860 screenshot in phone frames, missing en/uz/ru translations, a 21-word line, empty `say`, one chip)
  produced all 13 issues + 3 suggestions (`v2-phase2-followup-broken.png`); a complete project only gets
  suggestions (`…-followup-ok-project.png`).
- **Uzbek UI:** every key translated (checked by script, no ASCII apostrophes in oʻ/gʻ); projects, editor,
  template picker, scene transitions, history, follow-up, music and AI pages at 1600 px, 1280 px and 420 px
  with an overflow check (no clipped buttons/labels, no horizontal scroll): `v2-phase2-uz-*.png`. The
  choice survives a reload. No console errors.

## System monitor, warm AI models, more video generators, AI music (v2 phase 3)

### Live system monitor
- **Header widget on every page** (`#sysmon`, `static/monitor.js`): GPU %, VRAM used/total, GPU temperature
  and power, CPU %, RAM used/total. Updated every second over Server-Sent Events; amber from 60 % / 75 % /
  70 °C, red from 90 % / 92 % / 82 °C. The tooltip splits VRAM into Ovoz / AI / Studio and shows the RAM of
  the warm models. Click → **#/monitor**.
- **Monitor page:** hand-written canvas charts of the last 5 / 15 / 30 minutes with a synced crosshair +
  tooltip: GPU utilisation; VRAM stacked by owner (Ovoz TTS, AI jobs, Studio render/Chromium, desktop &
  other, total as a line); CPU; RAM stacked (warm AI models, other AI, Ovoz, Studio, other); GPU temperature;
  GPU power with the power-limit line (separate charts — no dual axes). Shaded bands = “Make video” stages
  (voice amber, AI purple, render teal), AI jobs (labelled warm/cold) and renders; they are also listed under
  *Timeline markers*. Tiles with the current values, *Running now*, the GPU process table (PID, owner, VRAM,
  GPU %, RAM) and the warm-model table. Colours were checked with the dataviz palette validator against the
  dark surface (lightness band, chroma, colour-blind separation).
- **Sampler** (`sysmon.py`): one thread, 1 Hz, 30-minute ring buffer. NVML (`nvidia-ml-py`, BSD-3) for GPU
  %, VRAM, temperature, power; `psutil` (BSD-3) for CPU/RAM and the process tree; the Windows counters
  `\GPU Process Memory(*)\Dedicated Usage` and `\GPU Engine(*)\Utilization Percentage` read through PDH with
  ctypes (≈1 ms — the old PowerShell probe took ~2 s). Owners: Ovoz = the process listening on :8000, its
  children and anything running from `OVOZ_DIR`; AI = warm workers and their children; Studio = this server's
  other children (render.py, headless Chromium). `gpu.Monitor` (VRAM gate, unload stage) now reads the sampler.
- **Overhead (measured):** 2.7 ms CPU per 1 s sample in steady state = 0.27 % of one core (0.017 % of the
  16-thread CPU), ~6 ms wall time, max 24 ms; the first samples read every process's command line once.
  The page shows the live figure at the bottom.
- **History survives a restart:** every 15 s the new samples are appended to `library/monitor-history.jsonl`
  (one ~150-byte JSON line per sample) and the markers saved to `library/monitor-markers.json`; once the file
  holds about an hour it is rewritten with the last 30 minutes. On start the last 30 minutes are loaded back
  (a torn last line after a crash is skipped; markers still open are closed at the last saved sample).

### Warm AI model workers (128 GB RAM)
- One `video/ai/worker.py --family <f>` process per model family (`workers.py`): `wan` (Wan2.2-TI2V-5B,
  FastWan, portraits), `a14b-t2v`, `a14b-i2v`, `echomimic`, `acestep`. It keeps the weights in RAM between
  jobs; during a job they stream to the GPU (group offloading from page-locked RAM) and afterwards only the
  CUDA context (~0.4 GB) stays on the GPU. Details and measurements: `../ai/README.md`.
- **Make video:** before the voice-over stage every warm worker releases its GPU memory (the stage detail
  lists the workers and the AI VRAM before/after); the weights stay in RAM, Ovoz gets the GPU. After
  `/api/unload` the AI stage uses the warm workers.
- **Settings** (AI page → *Warm AI models*, `library/ai-settings.json`): keep warm (on), unload after
  **30 min** idle, **RAM budget 80 GB** — before a job, least recently used *other* idle workers are stopped
  until the total working set + what the job still has to load fits; after a job the same check runs again.
  A family that is bigger than the budget on its own exits after each job. **Release RAM** stops all idle
  workers (or one). Busy workers are never stopped by the budget.
- **Cold vs warm (same kind of job twice, Ovoz unloaded):**

  | Job | Cold | Warm | Saved |
  |---|---|---|---|
  | FastWan 5B draft b-roll | 50.5 s (load 24 s) | **20.2 s** | 60 % |
  | Wan2.2 5B draft b-roll | 129 s (load 11 s + text encoder already warm) | 115 s | 11 % |
  | Wan2.2 A14B draft b-roll | 257 s (load 87 s) | **165 s** | 36 % |
  | ACE-Step 30 s of music | 12.6–14 s (load 4.4 s + worker start) | **3.8–6 s** | 60–70 % |
  | EchoMimic presenter | see “Verified” below | | |

### Video generators (providers)
`providers.py` — every provider has an id, name, licence (+ note), modes (t2v / i2v), presets (resolution,
frames, fps, steps), max seconds, VRAM/RAM needs, `estimate` (learned timings, warm or cold) and runs as an
AI-queue job (`generate`).

| id | Model | Licence | Modes | Draft / final | Use |
|---|---|---|---|---|---|
| `fastwan-5b` | FastWan2.2-TI2V-5B (FastVideo, 3-step DMD) | Apache-2.0 | t2v, i2v | 480×832 / 704×1280, 24 fps, 3 steps | **the default** (drafts 20 s warm) |
| `wan22-5b` | Wan2.2-TI2V-5B (unchanged) | Apache-2.0 | t2v, i2v | same sizes, 20 / 30 steps | *legacy* (the original; draft clips came out near-black) |
| `wan22-a14b` | Wan2.2-T2V/I2V-A14B + Wan2.2-Lightning 4-step LoRA | Apache-2.0 | t2v, i2v | 480×832 / 720×1280, 16 fps, 5 s | **quality** |

- Same 3 prompts at draft settings (frames in `preview/v2-phase3-providers-compare.png`, full table and
  judgement in `../ai/README.md`): A14B is clearly best (detail, natural motion), FastWan is good and 6× faster
  than the 5B, the original 5B draft was nearly black on 2 of 3 prompts. So **`fastwan-5b` is the default**
  (a project without `broll_provider`, the b-roll form, "Make video"); `wan22-5b` stays selectable and is
  marked *legacy* in the UI. Old jobs / clips without a provider still count as `wan22-5b`.
- **Animate an image (image-to-video):** the b-roll form (AI page and the scene form) has *Animate this image*:
  a project image or a library portrait; the prompt then describes the motion
  (`preview/v2-phase3-i2v-portrait.png`): Wan2.2-I2V-A14B keeps the face and background (264 s cold); FastWan is
  fast (78 s cold) but the face drifts after a few frames.
- Per scene `background.provider` (+ `background.image`), project default `broll_provider` (editor → AI tab).
  Library ids keep the old hash for `wan22-5b` text-to-video (existing clips stay cached); other providers
  and image-to-video include the provider and the image's hash.
- **Cloud providers (not implemented, nothing is called).** A paid API could be added later as one more
  entry in `PROVIDERS` with `family: None` and a `CloudProvider` class (sketch in `providers.py`, disabled):
  `generate(task) -> mp4` over HTTPS with the API key from an environment variable (never stored in projects),
  `estimate()` from the service, `vram_gb = 0` so the AI queue skips the VRAM gate, and its own terms shown
  next to the local licences. The rest of the studio (library, assignment, Make video) would work unchanged.

### AI music (ACE-Step v1.5, MIT)
- Job kind `music`: prompt, genre, mood, bpm, duration (10–240 s), seed; instrumental only. The track is
  generated 6 s longer than asked, the leading silence and the faded/silent tail are cut, a 1.5 s fade-out is
  added, it is encoded to OGG, checked and added with `music.add_track` as `source: "generated"` with the
  licence text, prompt, seed, model and the check results. Identical requests reuse the track.
- **Checks** (`music.analyze`, nobody can listen here): duration; EBU R128 loudness, true peak and LRA via
  ffmpeg; silent fraction; spectral flatness (white noise 0.56, music ≈ 0.00), spectral centroid, band
  energies and spectral flux (dB change of 24 log bands per 0.5 s: a sine 0.0, noise 2.0, music 4–6, the
  builtin tracks 3–12). Fails on silence, noise-like spectra or a static drone. Tested on a sine and white
  noise (both rejected) and every generated track (passed).
- **Tone shaping** (generated tracks only — ACE-Step output is bass-heavy): when `music.add_track` stores a
  `source: "generated"` track it keeps the untouched take as `original.<ext>` and writes `track.<ext>` through
  `highpass=38 Hz (12 dB/oct)` + `lowshelf 120 Hz −3 dB (Q 0.7)` + make-up gain back to the original's
  integrated loudness (limiter at −1 dBFS). `meta.tone` records the settings and ffmpeg measurements
  (loudness, sub-bass < 60 Hz and bass 60–250 Hz RMS before/after). On the three generated library tracks:
  loudness −9.7 → −9.9, −17.4 → −17.4, −11.6 → −11.6 LUFS; sub-bass RMS −2.9 / −1.9 / −1.0 dB; energy below
  30 Hz −5.8 / −8.4 / −16.8 dB; spectral centroid up 26–112 Hz. Uploads and builtin tracks are not touched.
- UI: **Generate music** on the AI page and in the editor's Music tab (generate now, or *Generate during
  “Make video”*, which stores `music.generate` in the brief).

### "Make video" + AI music + providers
- The AI stage first generates the music when `music.generate` is set and the project has no (existing)
  track (or reuses an identical generated one), then the portrait, the b-roll (each scene with its provider,
  image-to-video when `background.image` is set), then the presenter. Every job runs alone on the GPU (one AI
  queue), and the monitor shows each stage and job as a band.

### API (v2 phase 3)

| Method & path | What it does |
|---|---|
| `GET /api/monitor[?since=&procs=1]` | samples (1 Hz, 30 min), markers, latest sample, process table, warm workers, current pipeline/job, sampler overhead |
| `GET /api/monitor/stream[?procs=1]` | Server-Sent Events, one sample per second |
| `GET /api/ai/providers` | video generators with licences, capabilities, presets, estimates |
| `POST /api/ai/broll` | now also `provider` and `image: {project, asset}` or `{portrait}` |
| `POST /api/ai/music` | `{prompt, genre?, mood?, bpm?, duration?, seed?, title?, project?}` → job (or an instant done job when the same track exists) |
| `GET /api/ai/workers` · `PUT /api/ai/workers/settings` · `POST /api/ai/workers/release-ram` · `POST /api/ai/workers/release-gpu` | warm workers: view, `{warm, idle_minutes, ram_budget_gb}`, stop idle workers (`{family?}`), free their GPU memory |

### Verified (2026-11-15, test server on :8012, Playwright + API)

- **Make video, 3-scene Uzbek project** (`nimbo-a7ea`: hook with `wan22-a14b` b-roll, screen scene with the
  project default `fastwan-5b`, CTA; corner presenter (draft); `music.generate`; local Ovoz voice `navoiy`):

  | Stage | Result |
  |---|---|
  | ① Voice | warm A14B worker released its GPU memory first (weights stayed in RAM, 68 GB); 3 lines in 42 s, 3/3 aligned |
  | ② Free GPU | `unloaded navoiy`, Ovoz 2.9 → 0.0 GB, free 10.6 → 13.5 GB (1.5 s) |
  | ③ AI (644 s) | music 21 s (cold) → A14B b-roll **165 s, warm** (no load) → FastWan b-roll 47 s (cold; the RAM budget stopped the idle A14B worker first) → presenter 405 s (cold: load 79 s, 11.6 s of talking head) |
  | ④ Render | 40.6 s, 11.5 s video, −13.9 LUFS / −1.5 dBTP with the generated music |

  Monitor during the run: `preview/v2-phase3-make-monitor-*.png` (one per stage / job), the whole run
  `v2-phase3-monitor-after-make.png` — VRAM moves from Ovoz (amber, voice stage) to the AI workers (purple),
  the RAM chart shows the 68 GB warm A14B model, then the eviction. Frames: `v2-phase3-make-render-frames.png`
  (A14B light ribbons, FastWan bokeh behind the phone, presenter bubble).
- **Warm presenter:** the CTA line was edited twice and *Make video* run again each time. The presenter job
  (only the CTA chunk) took **186 s cold** (load 67 s, 4.0 s of video) and **74 s warm** (load 0, 3.1 s of
  video); the voice stage released the warm EchoMimic worker's GPU memory (0.7 GB → context only) before
  Ovoz ran. Whole pipeline 259 s → 157 s.
- **Music:** 4 generated tracks (30 s, 30 s, 18.5 s in the pipeline, 33 s): all passed the checks (LUFS
  −9.7 … −17.4, flux 3.8–6.3 dB, flatness 0.00, < 3 % silent after trimming); a white-noise and a sine test
  file were rejected. Spectrograms: `preview/v2-phase3-music-*-spectrum.png` — clear beat and harmonic lines,
  a fade-out at the end. Honest note: the takes are bass/low-mid heavy (< 1 % of the energy above 2 kHz);
  nobody listened to them.
- **Monitor:** `v2-phase3-monitor-running-warm-job.png` (a warm FastWan job: *Running now* chip, warm RAM
  tile 23.9 GB = the chart band = the worker table), `…-hover.png` (crosshair tooltip with the stage list).
  History is saved every 15 s and reloaded after a restart (see *Live system monitor*).
- **UI:** AI page (providers, image-to-video, Generate music, warm models), editor scene background (provider
  pre-selected from the scene), Music tab (*Generate music*, *Generate during “Make video”*), AI tab (project
  default provider) — EN and OʻZ at 1600 / 1280 / 420 px, no console errors, no horizontal overflow
  (`v2-phase3-*`, `v2-phase3-uz-*`). Every new string has an Uzbek translation (key parity checked by script).
- **Old projects:** every existing brief normalises exactly as with the committed code; `nimbo-ade8` (uz
  9:16) rendered again: 39.36 s, PSNR 56.9 dB against its earlier render (encoder noise). The renderer and
  `build.py` are unchanged.

## ComfyUI provider (v2 phase 4)

Any ComfyUI workflow can be a b-roll video generator. The Studio runs a local ComfyUI (or uses one you
already run) and talks to it **only over its HTTP / WebSocket API** — ComfyUI is GPL-3.0 and is never
imported, linked or shipped; it is a separate program in `video/comfyui/`. Full guide (building, exporting,
mapping, custom-node risks, licences, troubleshooting): [`../comfy-workflows/README.md`](../comfy-workflows/README.md).

### Install
`py -3.12 video/comfyui/setup.py` — clones ComfyUI at the pinned tag **v0.45.3** into `video/comfyui/ComfyUI`
(core nodes only, no Manager), makes `video/comfyui/.venv` (Python 3.12, **torch 2.8.0 + CUDA 12.8**, works on
the RTX 5070 Ti / sm_120), installs ComfyUI's requirements (the web UI's template *JSON* only; `--templates full`
adds ~670 MB of template preview media) and downloads the models of both samples into `video/comfyui/models/`
(outside the checkout; `extra_model_paths.yaml` points ComfyUI there). Downloads resume and are checked
against Hugging Face's SHA-256. Re-running it is a no-op (8 s). Only `setup.py`, `extra_model_paths.yaml` and a
README are tracked by git; the install is ignored.

| Downloaded (Comfy-Org/Wan_2.2_ComfyUI_Repackaged, Apache-2.0) | Size |
|---|---|
| `wan2.2_ti2v_5B_fp16` (draft sample) | 10.0 GB |
| `wan2.2_t2v_high_noise_14B_fp8_scaled` + `…low_noise…` (quality sample) | 2 × 14.3 GB |
| `umt5_xxl_fp8_e4m3fn_scaled` (both) | 6.7 GB |
| `wan2.2_vae` / `wan_2.1_vae` | 1.4 / 0.25 GB |
| **total downloaded** | **47.0 GB** (≈ 20 min at 25–30 MB/s, two streams) |
| `wan2.2_t2v_lightx2v_4steps_lora_v1.1_{high,low}_noise` | 2 × 1.2 GB — **hard-linked**, byte-identical (SHA-256) to the LightX2V LoRAs the A14B worker already has |

The diffusers folders in `video/ai/.models` can't be shared otherwise (ComfyUI loads single-file checkpoints).

### Managed server
- AI page → **ComfyUI**: state (running / busy / starting / stopped / error / external), URL, version, queue
  (running + waiting), VRAM it holds, last `/free`, **Start / Stop / Free GPU memory / Open ComfyUI**, settings,
  log tail (`video/comfyui/comfyui.log`). The header has a **ComfyUI** pill (state + queue length).
- Command: `main.py --listen 127.0.0.1 --port 8188 --extra-model-paths-config … --output/input/temp/user-directory
  video/comfyui/… --disable-auto-launch --preview-method none --disable-partner-nodes --reserve-vram 1.0
  --disable-all-custom-nodes`. Output goes to the log file (not a pipe), so ComfyUI survives a studio restart
  and the next studio process **adopts** it. Stop kills its process tree.
- **Flags measured** (Wan 2.2 14B fp8 + LightX2V, 480×832×81): ComfyUI v0.45's default **dynamic VRAM**
  (comfy-aimdo) streams the 14.3 GB experts through 16 GB without trouble: **89 s** cold, peak 11.6 GB
  VRAM, ~32 GB RAM. With `--disable-dynamic-vram` (the old estimate-based loading) the same job loaded the
  high-noise expert *partially* (7.7 GB on the GPU, 5.9 GB offloaded, lowvram patches) and had not finished
  step 1 after 10 minutes → cancelled. `--lowvram` / `--normalvram` do nothing while dynamic VRAM is on, so
  the default is **auto + `--reserve-vram 1`** (headroom for the desktop / Ovoz). VRAM mode, reserve, custom
  nodes and extra flags are settings (`library/comfy-settings.json`).
- **External mode**: give the URL of a ComfyUI you run yourself; the Studio never starts/stops it, the monitor
  attributes the process that listens on that (local) port.

### Workflows = providers
- `video/comfy-workflows/<id>/` = `workflow_api.json` (Export (API) format) + `manifest.json` (name, licence,
  commercial, notes, capabilities t2v / i2v / image / upscale / interpolate, **input mapping** node + input for
  prompt, negative, seed, width, height, length, fps, steps, image; output nodes; draft/final sizes; estimated
  time and VRAM) + `preview.jpg`. Each is the provider **`comfy:<id>`** everywhere a provider can be chosen (AI
  page form, editor scene background, project default, `background.provider` / `broll_provider` in briefs).
- **Upload** (AI page → *ComfyUI workflows* → *Upload a workflow*): the mapping is auto-detected (prompt /
  negative by following the samplers' positive/negative links back to the text encoders, through
  pass-through nodes; seed, steps, sizes, length, fps, input image, outputs; values driven by `Primitive*`
  nodes are mapped to the primitive) and shown before saving, with what could not be mapped; then a small
  **mapping editor** (node + input per value, several targets per value, outputs, capabilities, sizes,
  licence). Every workflow is **validated against the running ComfyUI's `/object_info`**: missing node types
  (custom nodes), missing model files (with the folder they belong in), mapping errors. **Custom nodes are
  never installed**; the UI warns that they are third-party code with full access to the PC.
- Samples (core nodes only, Apache-2.0 models): `wan22-5b-draft` (Wan 2.2 TI2V-5B, 20 steps; text **and** image
  to video — the `LoadImage` is dropped for text-to-video) and `wan22-14b-lightx2v` (Wan 2.2 T2V-A14B fp8 high +
  low noise, LightX2V 4-step LoRA, 2 + 2 steps).

### A ComfyUI job
In the one-at-a-time AI GPU queue like every other AI job: warm workers release their GPU memory → ComfyUI is
auto-started if needed → validation → image upload (`/upload/image`) for image-to-video → mapped inputs filled,
output prefix `studio/<job id>` → `POST /prompt` → progress from the WebSocket (loading → step x/y with ETA →
decoding → encoding; `/history` polling as fallback) → result via `/view` → H.264 + VP9 WebM + thumbnail in the
b-roll library with metadata (workflow id + graph hash, filled inputs, seed, prompt id, models, licence,
timings, peak ComfyUI VRAM). **Cancel** = `POST /queue {"delete"}` + `POST /interrupt {"prompt_id"}` (measured:
stopped 4.5 s after the click, ComfyUI queue empty). Library ids include the graph hash.

### GPU coordination
- Before the **voice stage** of Make video and before **any other AI worker job**, the Studio sends ComfyUI
  `POST /free {"unload_models": true, "free_memory": true}` and waits until the monitor shows its VRAM dropped;
  after a Make-video AI stage that ran ComfyUI jobs it frees it again. A ComfyUI job's VRAM gate counts what
  ComfyUI already holds as available.
- **Measured** `/free` (monitor counters, ComfyUI process / whole GPU): manual after jobs **3.6 → 0.36 GB**,
  GPU 5.8 → 2.3 GB in 1.4 s; before a FastWan worker job **5.8 → 0.4 GB** (GPU 8.2 → 2.6 GB, 0.8 s); after the
  Make-video AI stage **6.4 → 0.4 GB** and **5.6 → 0.4 GB** (GPU 8.7 → 2.6 / 8.0 → 2.6 GB, 0.9 s). What stays is
  ComfyUI's CUDA context (~0.37 GB).
- **Monitor**: new owner **ComfyUI** (pink, `#c75bb5`, validated with the dataviz palette script against the
  other owners on the dark surface — all pairs pass) in the VRAM and RAM charts, tiles, header tooltip and the
  process table (`python.exe ComfyUI`, owner COMFYUI). Sample keys `vc` (VRAM MB) and `rc` (RAM GB).

### Timings (RTX 5070 Ti 16 GB, 128 GB RAM, ComfyUI v0.45.3, same prompt, nothing else on the GPU)

| Workflow · preset | Size | Job time | Denoise | Peak VRAM (ComfyUI) | RAM | Compare (diffusers workers, phase 3) |
|---|---|---|---|---|---|---|
| `comfy:wan22-5b-draft` · draft (t2v) | 480×832×81 @24 | **68 s** | 54.7 s (2.7 s/step) | 13.1 GB | 4 GB | wan22-5b draft 115–129 s |
| `comfy:wan22-5b-draft` · draft (**i2v**, portrait) | 480×832×81 | **68 s** | 55.2 s | 12.6 GB | 4 GB | |
| `comfy:wan22-5b-draft` · final | 704×1280×121 @24 | 295 s | 261 s | 12.2 GB | 14 GB | |
| `comfy:wan22-14b-lightx2v` · draft | 480×832×81 @16 | **89 s** | 80.2 s | 11.6 GB | 25 GB | wan22-a14b draft 165 s warm / 257 s cold |
| `comfy:wan22-14b-lightx2v` · final | 720×1280×81 @16 | 298 s | 280 s | 12.4 GB | 32 GB | |
| uploaded template (Wan 2.2 14B T2V, Lightning on) | 640×640×81 | 93 s | 82.7 s | 10.8 GB | 24 GB | |

Frames: `preview/v2-phase4-compare-same-prompt.png` (top 5B draft — dark bokeh; bottom 14B — sharp, detailed
ribbons, though orange instead of teal), `v2-phase4-frames-draft-i2v.png` (the library portrait smiles and
nods; the face drifts a little by the end), `…-draft-wf-final-preset.png`, `…-quality-final-preset.png`,
`…-uploaded-workflow.png`. The 5B at 480×832 stays the "fast draft" (as in phase 3, the 5B is weak at low
resolution); the 14B LightX2V sample is faster in ComfyUI than the diffusers worker.

### API (v2 phase 4)

| Method & path | What it does |
|---|---|
| `GET /api/comfy/status` | state, reachable, busy, queue, version, devices, VRAM held, last /free, settings, log tail |
| `POST /api/comfy/start` · `/stop` · `/free` | start / stop the managed server; `/free` with VRAM before/after |
| `PUT /api/comfy/settings` | `{mode, url, port, autostart, vram_mode, reserve_vram_gb, custom_nodes, extra_args}` |
| `GET /api/comfy/workflows` | library + validation against `/object_info` |
| `GET /api/comfy/workflows/{id}` | manifest, validation, node list, graph, detected mapping |
| `POST /api/comfy/workflows/detect` | dry run of an upload: detected mapping + validation |
| `POST /api/comfy/workflows` | upload (multipart: JSON + name, licence, commercial, notes, preview image) |
| `PUT /api/comfy/workflows/{id}` · `DELETE …` · `POST …/validate` · `GET …/workflow_api.json` · `GET …/preview` | edit mapping/manifest, delete (not the samples), re-check, download, preview |
| `POST /api/ai/broll` | `provider: "comfy:<id>"` (+ `image` for i2v workflows) |

### Verified (2026-11-15, test server on :8013, API + Playwright)
- **Fresh setup** by `setup.py` (code + venv 191 s once the downloads had finished, models 47 GB, LoRAs
  hard-linked), re-run = no-op; ComfyUI started **from the Studio** (ready in 6–14 s), adopted after studio restarts.
- Both samples end to end as Studio jobs with the same prompt, plus image-to-video and both final presets
  (table above), frames inspected. **Cancel** during a running 14B final job: cancelled in 4.5 s, ComfyUI queue
  empty; a job stuck loading (dynamic VRAM off) cancelled in ~10 s.
- **Upload through the UI**: ComfyUI's own template `video_wan2_2_14B_t2v` (a subgraph with primitives,
  switches and a math expression) loaded in ComfyUI's web UI and exported in API format, then uploaded on the
  AI page — prompt, negative, both seeds, width/height and fps (from a `PrimitiveFloat`) auto-detected,
  steps/length reported as not mappable; validated OK; ran as a job (93 s; with the ribbon prompt this graph
  drew ribbons over a face, with "a red vintage sports car … coastal road at sunset" exactly that —
  `v2-phase4-frames-uploaded-workflow-car.png`; the test upload was deleted again afterwards — same models as the quality sample)
  (`v2-phase4-upload-detected.png`, `…-mapping-editor.png`, `…-workflows.png`). A graph with `VHS_VideoCombine`
  and an unknown LoRA reports *Missing nodes: VHS_VideoCombine* and *Missing model files:
  some_style_lora.safetensors → models/loras* (`…-upload-missing-nodes.png`).
- **Make video** on `nimbo-c2de` (2 scenes, uz, local Ovoz voice `navoiy`, hook background
  `comfy:wan22-5b-draft`): ① voice (ComfyUI `/free` first) 17 s → ② Ovoz unload 2.9 → 0.0 GB → ③ ComfyUI clip
  66 s, then `/free` 5.6 → 0.4 GB → ④ render 14 s; 105 s total (`v2-phase4-monitor-make-comfy.png`,
  `…-monitor-vram-comfy-ai-stage.png`: ComfyUI 11.8 GB during ③, Ovoz 0; `…-make-render-frames.png`).
- **Coordination**: a FastWan job after a ComfyUI job → `/free` 5.8 → 0.4 GB first; a ComfyUI job after a warm
  FastWan worker → `released_workers: ["wan"]`.
- **Old features**: FastWan provider job (60 s), every existing brief normalises exactly as with the committed
  code, the old providers keep their library ids. UI EN + OʻZ (key parity checked), 1600 px and 420 px, no
  console errors, no horizontal overflow (`v2-phase4-ai-*`, `v2-phase4-editor-scene.png`).
- Known: the header pill is hidden below 600 px; a workflow whose length comes from a math node keeps its own
  length (the Studio's frame count is a label only); ComfyUI's dynamic VRAM decides how much it keeps after a
  job (3–6 GB) until the next `/free`.

## "Make video" (one button, voice first, then video)

**Make video** in the editor, or `POST /api/projects/{id}/make` for a coding agent, runs four stages in
order. Each stage gets the whole GPU, and only one pipeline runs at a time (others queue).

| Stage | What happens |
|---|---|
| ① Voice-over | Generates every missing or changed line (local Ovoz models may take the whole GPU). No AI GPU job may start; if one is running, the stage first waits for it to finish. |
| ② Free GPU | Calls Ovoz `POST /api/unload`, then confirms with the per-process GPU counters that Ovoz's VRAM dropped (before/after numbers are shown). An old Ovoz without the endpoint (404) gets the notice *"Ovoz is outdated — update it to free GPU memory automatically"*. The AI queue's normal VRAM wait / *Start anyway* then applies. |
| ③ AI clips & presenter | Runs every AI job the project still needs: a presenter portrait from `portrait_prompt` (when no portrait is set), b-roll for scenes whose `background` has a `prompt` but no clip, and the presenter video when it is missing or stale. Uses the pipeline's quality (default **final**; `presenter.quality` is set to match). The studio's own voice requests wait during this stage. If Ovoz loads models again meanwhile (someone using the Ovoz page), the stepper shows a notice. |
| ④ Render | The normal render queue. |

- The UI shows a stepper with each stage's state, progress, elapsed time and ETA, any notices, and the AI
  job rows (with *Start anyway* when a job waits for memory).
- **Cancel** works at any stage. It stops the voice loop, kills the AI subprocess tree, or cancels the
  render.
- **Retry** resumes at the stage that failed, without redoing finished stages.
- Pipelines are kept in `library/pipelines.json`. After a server restart, a running pipeline shows as
  *interrupted*, and Retry resumes it.

**Full-GPU mode.** The runners choose it automatically (`auto`) and fall back to offloading.
- **Wan2.2** `full`: the UMT5 encoder encodes the prompt and goes back to the CPU, the cache is emptied,
  and the whole transformer stays resident for every step; then the VAE decodes.
- **EchoMimic** `full`: transformer, VAE and CLIP stay resident; T5 stays on the CPU.
- `auto` uses full mode only when its measured need fits in free VRAM. A run that spills into shared memory
  is *slower* than offloading.

**Measured on the 16 GB card, with Ovoz unloaded (14.7 GB free):**

| Job | Full GPU | Offload (fallback) | Before this change |
|---|---|---|---|
| B-roll final, 704×1280×121, 30 steps | needs 17 GB, spills 2.2 GB: 32 s/step | group: **23 s/step**, 8.7 / 11 GB → **823 s per clip end to end** | ~23 min (43 s/step), shared GPU |
| B-roll draft, 480×832×81 | 3.89 s/step (11.7 GB) | 3.99 s/step | 4.2–4.5 s/step next to Ovoz |
| Presenter final, 576×1024 | needs 15.8 GB, spills 1.4 GB: 60–66 GPU-s per video second (206 s for a 3.4 s chunk) | model offload: **55 GPU-s per video second** (188 s for the same chunk), 9.7 GB | ~66 GPU-s per video second |
| Presenter draft, 448×800 | 24 GPU-s per video second (12.0 GB) | ≈ 23 GPU-s per video second | |

- **Where the speed-up comes from.** Freeing the GPU (stage ②) is the real speed-up: final b-roll is
  1.7× faster and the final presenter 1.2× faster than on the shared GPU.
- **Full residency itself gains ≤ 3 %.** Group offload with CUDA streams is already close to full speed,
  and at final resolution full residency doesn't fit on 16 GB.
- **Which mode `auto` picks:**
  - Final b-roll: group offload.
  - Final presenter: model offload.
  - Draft b-roll and draft presenter: full mode (it fits).
- The in-pipeline run selected full mode for the final presenter before this threshold was measured,
  which is why its 10.2 s took 679 s.

| Method & path | What it does |
|---|---|
| `POST /api/projects/{id}/make` | `{quality?: "final"\|"draft", allow_silent?: false}` → 202 pipeline (409 + the running one if the project already has one) |
| `GET /api/projects/{id}/make` | latest pipeline of the project: `{status: queued/running/done/error/cancelled/interrupted, current, eta, stages: {voice, unload, ai, render: {state, progress, message, eta, elapsed, detail}}, notices, result: {render}}` |
| `GET /api/make` · `GET /api/make/{pid}` · `POST /api/make/{pid}/cancel` · `POST /api/make/{pid}/retry` | all pipelines · one · cancel · resume from the failed stage |

**Verified (2026-11-15).**
- **Full run from the UI** (Final quality): a 3-scene Uzbek project (9.9 s) using Ovoz's local `navoiy` model.

  | Stage | Result |
  |---|---|
  | ① Voice | 26 s |
  | ② Unload | 6.5 s: `unloaded navoiy`, Ovoz 2.9 → 0.0 GB, free 10.4 → 13.3 GB |
  | ③ AI | final b-roll 823 s (group offload) + final presenter 759 s (3 chunks) |
  | ④ Render | 22.5 s |

  Screens: `preview/make-stepper-*.png`. Frames: `preview/make-render-9x16-*.png`.
- **Cancel and Retry:** the AI stage was cancelled mid-job (the subprocess tree was killed). Retry resumed
  at stage ③, without redoing voice or unload.
- **Voice gate:** a Studio voice request made during stage ③ waited with *"“Make video” is running AI jobs
  on the GPU"*.
- **API path** (`POST /api/projects` → `POST …/make {"quality":"draft"}` → poll): MP4 in 176 s with the
  local `qwen3uz` voice and a draft b-roll. A TTS call made directly to Ovoz during stage ③ produced the
  *"Ovoz loaded models again…"* notice.

## AI layers (optional, local, slow)

Two optional generative layers run on this PC's GPU. Nothing leaves the machine, and everything is free/open
source. They are off by default, and briefs without them render exactly as before (verified pixel-identical).

- **B-roll backgrounds** (Wan2.2-TI2V-5B text-to-video). Any scene can use `background: {type: "broll",
  clip, dim, blur}`. The clip fills the frame (cover) and gets a gentle Ken Burns zoom. It is darkened with
  the brand background colour (a darker band at the top and bottom keeps the header and captions readable),
  can be blurred, and the brand glow stays on top. To cover the scene, the clip is slowed down (≥ 0.6×).
  If that is not enough, it ping-pongs with eased turnarounds, so there is never a jump. Consecutive
  scenes that use the same clip share one continuous video. Clips cross-fade at cuts. Seeking is
  deterministic (`currentTime` + `seeked` + 2 frames), like recordings.
- **AI presenter** (EchoMimicV3-Flash, audio-driven talking head). It uses the project's `presenter`
  setting and the `presenter` layout:
  - `corner` puts a round bubble with a rotating brand ring on the chosen scenes. In 9:16 it sits on the
    left, above the caption pill, away from the right-hand button rail. In 16:9 it sits bottom-right.
    Scenes keep their content clear of it: centred layouts are moved/scaled, `screen` moves the zoom
    target so the focus never sits under the bubble (9:16) or narrows the device box (16:9), and `screens`
    shifts/scales the device group.
  - `presenter` scenes show the talking head full-frame in 9:16, and as a tall card next to the text in 16:9.
  - The video is aligned to the global timeline (video time = t).
  - While the presenter video is missing or stale, renders simply leave the bubble out. Presenter scenes
    then show the still portrait, so the video can be rendered while the presenter is being generated.

### Requirements

- NVIDIA GPU with ≥ 8 GB free VRAM for the draft presets, plus `video/ai/.venv` (torch 2.8 cu128,
  diffusers 0.43, …) and the weights (Wan2.2-TI2V-5B 34 GB, EchoMimicV3-Flash 24 GB). Setup is in
  [`../ai/README.md`](../ai/README.md). The studio itself runs in `video/.venv`. It calls
  `video/ai/.venv/Scripts/python` with `broll.py --out-file …`, `portrait.py --out …` and
  `avatar.py --job job.json` as **subprocesses**, which print `@@PROGRESS {json}` per diffusion step and
  `@@RESULT {json}` at the end.
- On first start the 3 existing clips (`ai/clips/{waves,phone,mic}.mp4`) and the generated presenter
  portrait (`ai/build/presenter.png`) are imported into `video/library/`.

### GPU job queue

- One GPU job runs at a time, in a queue separate from renders. Each job shows its status, stage (loading model,
  diffusion step k/n, VAE decode, …), progress, elapsed time, an ETA (from the measured step time) and the last
  40 log lines. **Cancel** kills the whole subprocess tree (`taskkill /T`).
- **VRAM gate:** before a job starts, `nvidia-smi` free memory is compared with what the job needs (measured peak
  + 0.5 GB CUDA context). When there is not enough, the job stays queued with e.g. *"waiting for GPU memory:
  needs ~8.2 GB, 8.0 GB free (Ovoz is using 5.6 GB)"*.
  - It starts by itself when memory frees up.
  - **Start anyway** skips the check. The job may then spill into shared memory and run much slower.
  - Ovoz's share comes from the Windows "GPU Process Memory" counters (the process on the Ovoz port, its
    children, and anything running from `OVOZ_DIR`), because nvidia-smi has no per-process numbers on WDDM.
  - A presenter job whose chunks are all cached only assembles (CPU), so it skips the check.
- Jobs are stored in `library/ai-jobs.json`. After a server restart, queued/running jobs show as
  **interrupted** (with Retry), and a leftover AI subprocess is killed.
- Measured timings update `library/ai-timings.json`, and the UI's estimates use them.

### Caching

- **B-roll** id = hash(prompt, seed, steps, W×H×frames). Repeating a request reuses the library clip
  instantly. Library entries hold `clip.mp4`, `clip.webm` (VP9, GOP 6 for cheap backward seeks),
  `thumb.jpg` and `meta.json` (prompt, negative, seed, steps, resolution, seconds, model, licence, timings,
  peak VRAM).
- **Presenter:** each scene that shows the presenter is cut into ≤ 5 s chunks (the EchoMimic sequence
  limit on 16 GB). Every chunk is cached by hash(scene voice clip, offset, length, portrait, settings),
  so editing one line only regenerates that scene. The assembled `presenter/<key>.webm` is aligned to
  the timeline frame-exactly. Scenes without the presenter are filled with the still portrait and are
  never visible. When the voice-over or settings change, the UI marks the presenter **stale**.

### Presets and measured timings (RTX 5070 Ti 16 GB, Ovoz loaded with ~5.6 GB)

| Job | Settings | Time | VRAM (torch peak alloc / reserved) |
|---|---|---|---|
| B-roll **draft** | 480×832 or 832×480, 81 frames (3.4 s @ 24 fps), 20 steps | **137 s** end to end: model load 12–24 s, 4.2–4.5 s/step (≈ 86 s), VAE decode ≈ 32 s, library/VP9 ≈ 3 s | 5.0 / 7.2 GB (process ≈ 7.6 GB) |
| B-roll **final** | 704×1280 or 1280×704, 121 frames (5 s), 30 steps | **823 s** via "Make video" with Ovoz unloaded: load 12 s, 23.3 s/step, decode ≈ 100 s. It took ≈ 23 min (43 s/step) earlier on the shared GPU | 8.7 / 11.0 GB, so it **waits** while Ovoz is loaded (≈ 8 GB free) |
| Presenter **draft** | 448×800, 8 steps, TeaCache 0.1, 25 fps | **318 s for 9.3 s** of talking head: pipeline load ≈ 100 s (UMT5 prompt encoding on CPU), then ≈ 23 s of GPU per second of video (3.2 s chunk 79 s, 2.3 s chunk 46 s); re-assembly from cache 2 s | ≈ 6.1–6.3 / 7.6–7.9 GB (ai/README.md). Started with "start anyway" at 8.0 GB free next to Ovoz, with no OOM |
| Presenter **final** | 576×1024, 8 steps | **55 GPU-s per video second** + 66–70 s load (model offload, free GPU). The pipeline run with spilling full mode took 679 s for 10.2 s | 6.7 / 9.7 GB, so it waits while Ovoz is loaded |
| Portrait | Wan2.2 text-to-image, 704×1280, 40 steps | ≈ 22 s + load (ai/README.md) | ≈ 7 GB |

**VRAM with Ovoz.** Ovoz's TTS workers held ~5.6 GB (navoiy 3.0 + qwen 2.5 + server 0.2), and the desktop
held ~2 GB, leaving ≈ 8 GB free.
- The draft b-roll fits: the GPU reached 15.5/16 GB, with no slowdown compared with an earlier test run.
- The draft presenter needs ~0.2 GB more than was free. It waited until "start anyway", then ran normally.
- The final presets need 11 GB, so they wait until Ovoz unloads its models, or they spill (expect several
  times slower).

### Licences (all allow commercial use)

| Component | Licence | Note |
|---|---|---|
| Wan2.2-TI2V-5B weights + code (b-roll, generated portraits) | Apache-2.0 | You are responsible for generated content (model card) |
| UMT5-XXL text encoder | Apache-2.0 | |
| EchoMimicV3 code + Flash weights | Apache-2.0 | used unmodified |
| Wan2.1-Fun-V1.1-1.3B-InP (EchoMimic base) | Apache-2.0 | includes an open-clip ViT-H/14 image encoder; the original OpenCLIP was trained on LAION (dataset-provenance caveat) |
| chinese-wav2vec2-base | MIT | trained on Chinese speech; lip shapes for uz/ru/en/ar are plausible, not dubbing-grade |
| PyTorch / diffusers / transformers / accelerate | BSD-3 / Apache-2.0 | |
| ffmpeg (gyan.dev, `--enable-gpl`) | GPL-3 | fine as a tool; don't ship it inside a closed product |
| FastWan2.2-TI2V-5B-FullAttn (FastVideo) | Apache-2.0 | fine-tune of Wan2.2-TI2V-5B; same UMT5 + VAE |
| Wan2.2-T2V-A14B / I2V-A14B (Wan-AI) + Wan2.1 VAE | Apache-2.0 | |
| Wan2.2-Lightning LoRAs (lightx2v) | Apache-2.0 | fused at load time |
| ACE-Step v1.5 XL turbo + its VAE | MIT | card: licensed / royalty-free / synthetic training data, generated music usable commercially |
| Qwen3-Embedding-0.6B (ACE-Step text encoder) | Apache-2.0 | |
| nvidia-ml-py, psutil, peft | BSD-3 / BSD-3 / Apache-2.0 | |

There are no InsightFace, Basel Face Model or other non-commercial components. Uploaded portraits require
ticking *"This is a fictional person or I have the person's permission"* (stored in the portrait's
`meta.json`). Generated presenters are fictional. Most platforms require an "AI-generated" label on synthetic
presenters. The UI shows the licences and this disclosure on the AI page and in the editor's AI tab.

### AI API

| Method & path | What it does |
|---|---|
| `GET /api/ai/status` | GPU (total/used/free, Ovoz/AI/other MB, processes), queue, presets with estimates + VRAM needs, licences |
| `GET /api/ai/jobs[?project=]` · `GET /api/ai/jobs/{id}` | AI jobs: `{kind, status: queued/running/done/error/cancelled/interrupted, stage, progress, message, eta, elapsed, estimate, need_gb, waiting, log, result}` |
| `POST /api/ai/jobs/{id}/cancel` · `/start-anyway` · `/retry` · `DELETE /api/ai/jobs/{id}` | cancel (kills the tree), skip the VRAM check, re-queue, remove from the list |
| `POST /api/ai/broll` | `{prompt, negative?, seed?, preset: draft/final, orientation: portrait/landscape, assign?: {project, scene, dim?, blur?}}` → job (an instant `done` job when the clip is cached; 409 + the job when the same request is already queued) |
| `GET /api/library/broll` · `GET …/{id}/clip.webm` (or `clip.mp4`, `thumb.jpg`) · `DELETE …/{id}` | library (with `used_in`) |
| `GET/POST /api/library/portraits` · `GET …/{id}/portrait.png` (or `thumb.jpg`) · `DELETE …/{id}` | portraits. Upload: multipart image + `consent=true` |
| `POST /api/ai/portrait` | `{prompt, seed?, project?}` → job (generates a fictional portrait; with `project` it becomes that project's presenter) |
| `GET /api/projects/{id}/presenter` | presenter state: ready / stale / error, seconds still to generate, estimates per quality, active job |
| `POST /api/projects/{id}/presenter` | `{quality?: draft/final}` → job (generates missing voice lines first, then only the missing chunks) |
| `GET /api/projects/{id}/presenter/{key}.webm` | the assembled talking head |

### Verified (2026-11-15, driven with Playwright + API)

- **Old projects:**
  - 18 stills of two existing Nimbo projects and the old `render.py` CLI timeline are pixel-identical
    before and after the change.
  - Old briefs normalise and build byte-identical timelines.
  - The old CLI still renders the Ovoz promo (641 frames, 16.6 s).
- **B-roll backgrounds:**
  - Through the scene form, the `waves` clip was assigned to scene 1 and `mic` (blur 4, darken 60 %) to
    scene 6 of the example project.
  - It was rendered from the UI in 9:16 and 16:9 (≈ 50 s wall time each, vs ~30 s without video
    backgrounds).
  - Frames: `preview/render-ai-broll-*.png`.
  - Ping-pong check: on a 14 s scene, the frame-to-frame difference across the turnaround stays at its
    normal level, with no jump.
- **Draft b-roll job, end to end from the scene form:**
  - Queued → loading → 20 steps with a live ETA (98 s left at step 7) → decode → library → done in 137 s
    (the estimate was 146 s).
  - The clip was auto-assigned to the scene.
  - A second job was cancelled while loading; its process tree was gone afterwards.
  - Screens: `preview/ai-queue-running.png`, `ai-tab-after.png`, `ai-page.png`.
- **Presenter, "AI demo" project** (3 scenes, 9.3 s):
  - The job waited for GPU memory (`preview/ai-presenter-waiting-gpu.png`), started with *Start anyway*,
    and generated 3 chunks.
  - The first run failed at assembly (an ffmpeg option was missing in this build). After the fix,
    **Retry** reused the cached chunks and assembled in 2 s.
  - Rendered from the UI in 9:16 and 16:9: `preview/render-ai-presenter-*.png` (full-frame scene,
    bubble over a zoomed screen scene, bubble on the CTA with b-roll).
  - Editing a line turned the badge **stale** (only that scene's 3.2 s would be regenerated). Reverting
    the edit made it ready again.

### Known limitations (AI)

- Draft b-roll is low-detail (dark, simple abstract motion). It works as a dimmed background, not as hero
  footage. Prompt adherence is hit-or-miss, and the clips carry no product information.
- Clips are 704 or 480 px wide and are upscaled to 1080/1920. A clip whose orientation doesn't match the video is
  cropped hard. The UI warns about this, and generating from a 16:9 project makes landscape clips.
- The presenter restarts from the same portrait at every chunk (≤ 5 s), which gives a slight pose reset
  at scene cuts and inside scenes longer than 5 s.
- Lip sync uses a Chinese-trained audio encoder. It is plausible for en/ru/uz/ar but was only checked from frames.
- The pipeline load takes about 100 s per presenter job, because the 5.7 B UMT5 encoder runs on the CPU.
- 448×800 is soft when shown full-frame at 1080×1920. Use `final` for full-frame scenes. The tight
  head-and-shoulders portrait fills the frame, so the brand header sits over the forehead.
- Final presets don't fit next to a loaded Ovoz. They wait for memory, or you choose *start anyway*
  (spills, slow).
- Per-process VRAM attribution needs Windows performance counters. On other OSes only the totals are shown.
- The portrait generator was not re-run in this session. Its figure comes from the earlier experiment
  (ai/README.md).
- `screen` scenes zoom into `focus` *inside* the device screen, so the frame never grows over the
  headline, captions or progress bar. This was the 9:16 overlap bug. A focus that already spans almost the
  full screenshot width gets little extra magnification; the ring and the dimming carry it.

## Security

A local tool, but a web page: anything open in the same browser can send requests to `127.0.0.1`, and
templates / workflows / briefs come from other people or AIs. The v2 hardening review is in
[`SECURITY-REVIEW.md`](SECURITY-REVIEW.md) (finding → fix → test). In short:

- **Requests** (`security.py`, ASGI middleware): the Host must be a loopback name (DNS rebinding) unless
  `STUDIO_ALLOWED_HOSTS` lists it; POST/PUT/PATCH/DELETE with an `Origin` from another site, `Origin: null` or
  `Sec-Fetch-Site: cross-site` get 403 (tools without Origin, like curl, still work — the same rule as Ovoz);
  per-route body limits (uploads 1.5 GB, music 64 MB, templates 52 MB, workflows 16 MB, everything else 2 MB),
  enforced while streaming; `nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy`.
- **Templates:** the zip import refuses path traversal, absolute / drive / UNC paths, symlinks, Windows device
  names, case-duplicates, other file types, bombs (declared and real size, entry count before parsing) and
  installs atomically. Template files are served with `Content-Security-Policy: sandbox`. Template code runs
  in a sandboxed preview iframe and, when rendering, on a virtual origin that exposes only the needed folders
  (no `--allow-file-access-from-files`, no network). `templates/validate.py` uses the same sandbox.
- **ComfyUI:** an external ComfyUI must be on loopback unless `STUDIO_COMFY_HOSTS=host1,host2` (or `*`)
  allows it; `extra_args` cannot change `--listen`, ports, folders or CORS; uploaded workflows are size- and
  shape-checked (≤ 1500 nodes, node ids, class names) and previews must be real images.
- **Warm workers** accept commands only after a secret handshake (`STUDIO_WORKER_TOKEN`, set by the studio).
- **Processes:** cancelling a render kills render.py with its Chromium and ffmpeg (process tree); the server
  does the same on shutdown; failed renders and AI jobs leave no temp files.

Verified (2026-11-15, test instance on :8014 with its own data folder, Playwright): every page clicked
through with no console errors (`preview/v2-harden-*.png`); from inside the sandboxed preview `parent.document`
and `localStorage` throw and API calls are blocked while the Bold template (template.js + fonts) renders; an
imported template whose module tries `window.top` / a POST ran only in the preview frame and changed nothing;
`/renderer/index.html?data=https://…` loads no foreign script; a render cancelled at frame 0 left none of its 12
processes; a full 30.8 s render took 30.1 s; the monitor showed the previous 8 minutes after a hard kill and
restart; `render.py` with no arguments still renders the Ovoz promo (641 frames, 21.37 s).

## Tests

```sh
video/.venv/Scripts/python -m pip install pytest          # once
video/.venv/Scripts/python -m pytest video/studio/tests   # 148 tests, ~8–10 s, offline: no GPU, no Ovoz, no network
video/.venv/Scripts/python -m pytest video/studio/tests -m slow   # + the real renderer: a 2 s video and the sandbox probe
```

The suite runs in-process (FastAPI `TestClient`) against a throw-away data folder: `tests/conftest.py`
sets `STUDIO_DATA_DIR`, `STUDIO_TEMPLATES_DIR` and `STUDIO_WORKFLOWS_DIR` (see `paths.py`) and
`STUDIO_KEEP_LEFTOVERS=1` before importing the studio, so it never touches real projects, job state or the
warm workers of a running studio; Ovoz points at a closed port and faster-whisper is mocked. Covered: brief
normalisation (old briefs, languages, formats, injected internal keys), Uzbek apostrophes, caption chunks and
the alignment fallback, follow-up messages (EN/UZ), history snapshots / coalescing / restore, template
manifests and zip import with malicious zips, ComfyUI mapping detection on both sample workflows and upload
validation, music mix (loudness, peak, ducking) and tone shaping, monitor history persistence, i18n, and API
smoke tests (example project, update, history, same-origin / Host rejection, upload and body limits, ids in
URLs, security headers, render-process cleanup).

A second studio for manual testing next to the real one (own data, never touches its jobs or workers):

```sh
set STUDIO_DATA_DIR=C:\temp\studio-test & set STUDIO_KEEP_LEFTOVERS=1
video/.venv/Scripts/python -m uvicorn server:app --app-dir video/studio --port 8014
```

## Verified (2026-11-15)

- `studio/preview/ui-*.png`: projects page, AI-request dialog, editor (scene form, focus picker,
  live preview playing, render progress, renders list, assets tab, JSON tab, 420 px mobile layout).
- `studio/preview/render-9x16-*.png`, `render-16x9-*.png`: frames from MP4s rendered from the UI.
  The project was created by drop-zone upload, scene 2's headline was edited, voice came from
  Ovoz `edge`/`en-US-AvaNeural` (7/7 scenes), output 27.20 s for 27.18 s of timeline.
- API path: curl multipart create → `POST /render` (generates voice) → poll → `curl -OJ` download
  (16:9, 27.20 s). Inbox: a good folder was imported and moved to `_imported/`; a broken
  `brief.json` produced `ERROR.txt`. Silent fallback: a project with an unavailable model errors
  without `allow_silent` and renders a silent 30.8 s video with it.
- The old `video/html/render.py` CLI (no args → shared timeline) still renders the Ovoz promo
  (641 frames, 21.37 s, ~17 s wall time).

## Known limitations

- The renderer captures screenshots: ~30–38 fps with 8 workers (1:1 is faster), so ~30–50 s per output
  for a 40 s video; 3 languages × 3 formats take 6–7 minutes. Recordings slow capture down a little,
  since each frame waits for a seek.
- Aurora and Bold are dark (a light `background` colour is darkened); Clean is the light template.
- The AI presenter speaks the main language only (its lip-sync is generated from that voice-over);
  renders in other languages leave the bubble out and show the still portrait on `presenter` scenes.
  B-roll is generated for the main format's orientation.
- Word timing for Uzbek is about as good as the old estimate on the uniform Microsoft voices (see the
  table); it helps more on local voices with uneven pacing and on lines with pauses.
- Music levels in the editor preview are approximate (HTML audio volume, the duck envelope sampled at
  10 Hz); the render's mix is the reference.
- No authentication: keep it on 127.0.0.1. Reads (GET) are not origin-checked: other sites cannot read the
  answers (no CORS), but a page could still trigger a cheap GET such as a timeline preview.
