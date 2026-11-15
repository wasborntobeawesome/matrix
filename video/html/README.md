# Option: HTML/CSS + Playwright (headless Chromium) + FFmpeg

Renders `video/shared/build/timeline.json` + `voiceover.wav` to `video/out/html.mp4`
(1080x1920, 30 fps, H.264 High yuv420p BT.709, AAC 192k, `+faststart`).

## Run

```sh
# uses the shared venv (playwright + Chromium, numpy) and video/bin/ffmpeg.exe
video/.venv/Scripts/python video/html/render.py                  # full render
video/.venv/Scripts/python video/html/render.py --out x.mp4 --workers 8 --quality 94 --crf 18
video/.venv/Scripts/python video/html/render.py --stills 2.5,7.5  # PNG stills -> video/html/preview/, no video
```

Live preview: run the renderer once (it writes `build/data.js`), then open `video/html/index.html`
in Chrome and click — it plays in real time with the voice-over. (Chrome may block `file://`
audio; scenes still play then.)

No extra installs beyond the shared venv (`pip install playwright numpy` + `playwright install chromium`).

## How it works

| File | Role |
|---|---|
| `index.html`, `style.css` | Stage 1080x1920, fonts via `@font-face` from `video/shared/fonts/` |
| `app.js` | Builds the 5 scene layouts (hero/chips/stat/list/cta), captions, progress bar from the timeline; exposes `window.seek(t)` |
| `render.py` | Audio analysis → `build/data.js`; N parallel Chromium pages; JPEG screenshots; image2pipe → ffmpeg |

- **Deterministic time.** There are no CSS animations/transitions or timers. `seek(t)` sets every
  animated property (opacity, transform, blur, colours, counter value, bar heights) as a pure function
  of `t`, so frames can be rendered out of order by 8 pages at once (page *k* renders frames k, k+8, …);
  the writer re-orders them before piping into ffmpeg.
- **Audio-reactive.** `render.py` computes per-frame RMS + 24 log-spaced FFT bands of the voice-over;
  the hero waveform, the logo bars and the caption equalizer move with the actual voice.
- **Speech-synced items.** Chips and list items pop in when the voice says them (matched against the
  caption words, e.g. "quvonch," → "😊 Quvonch"; unmatched ones like "Русский" follow the previous item).
- **Captions** are karaoke-style: the current word (estimated from character length inside the chunk)
  is accent-coloured, upcoming words dimmed. Pill centred at y≈1345, progress segments at y≈1480,
  brand header at y≈245–310 — everything inside the 220px top / 380px bottom Instagram safe zones.

## Render time (this machine, 16 threads, Windows 11)

641 frames: **19–25 s wall time total** (≈26–34 frames/s incl. audio analysis, Chromium start-up and
x264 `-preset slow`), 8 workers. A single page does ~6–8 fps. Output ≈ 9.6 MB (≈3.6 Mbit/s).
Stills mode: ~2 s for a handful of frames, which made iterating on the design very quick.

## Licenses

| Tool | License | Commercial use |
|---|---|---|
| Playwright (Python) | Apache-2.0 | free |
| Chromium (Playwright build) | BSD-3-Clause + bundled third-party OSS licences | free |
| FFmpeg (gyan.dev "essentials" build, `--enable-gpl --enable-version3`, includes libx264) | GPLv3 binary | free to *use* to make videos; the output video is not covered by the GPL. Redistributing the binary requires GPL compliance. |
| x264 | GPLv2+ | same as above (commercial x264 licence exists if you need to ship it closed) |
| NumPy | BSD-3-Clause | free |
| Inter, Noto Sans Arabic | SIL OFL 1.1 | free (fonts can be embedded/used in videos) |
| Segoe UI Emoji (emoji glyphs, Windows system font) | Microsoft font, part of Windows | rendering it into your own video is generally fine; the font file itself can't be redistributed. For a cross-platform/licence-clean setup, bundle Noto Color Emoji (OFL) or Twemoji (CC-BY 4.0). |
| This code | — (written for this repo) | — |

## Script support

- **Uzbek Latin** (`Oʻzbekcha` U+02BB, `his-tuyg‘u` U+2018, `GPU’da`): Inter has the glyphs; correct.
- **Cyrillic** (`Русский`): Inter covers it; correct.
- **Arabic** (`العربية`): Chromium's HarfBuzz shapes it correctly (joined forms, RTL) with
  Noto Sans Arabic via the font-family fallback chain + `dir="rtl"`; nothing special needed.
- **Emoji** (`😊 😮 😌 🎙️`): rendered in colour via Segoe UI Emoji (system fallback). 🎙️ with VS16 works.
  On Linux you would need a colour emoji font installed (e.g. `fonts-noto-color-emoji`).

Text handling is the biggest strength of this option: it's a browser, so shaping, bidi, fallback,
kerning and wrapping are all "just CSS".

## Pros

- Full CSS/SVG design vocabulary (gradients, gradient text, backdrop blur, shadows, grid layout) —
  easy to make it look like a modern app promo; designers/web devs already know it.
- Perfect multilingual text rendering for free (HarfBuzz, bidi, font fallback, colour emoji).
- Deterministic, frame-accurate, parallelisable; fast (~20 s for 21 s of 1080x1920).
- Live preview in any browser; still-frame mode for quick iteration.
- No build step, no Node project, no proprietary runtime; ~900 lines total (JS+CSS+HTML+Python).

## Cons

- You write your own tiny animation engine (`seek(t)` with easing helpers); no timeline editor.
  GSAP/anime.js could replace it (GSAP is now free incl. commercial, but not OSI open source).
- Chromium headless is software-rendered: heavy `filter: blur()` / `backdrop-filter` on big areas
  slows capture; I avoided large blurs (glows are radial gradients).
- JPEG intermediate (q=94) adds a tiny bit of loss before x264 (PNG is lossless but ~3x slower).
- Chromium download ≈ 150 MB+; depends on system emoji font.
- Frame capture via screenshots, not a true render API — fine at this size, but 4K/60 would be slow.

## What was hard

- Getting transitions between scenes to not overlap visually (exit window [end−0.3 s, end+0.1 s],
  next scene enters at start+0.1 s).
- Syncing chips/list items to the voice with only chunk-level caption timing (heuristic word matching).
- Nothing about fonts/RTL/emoji was hard — that's the point of this option.

## Known limitations

- Word-level karaoke timing is estimated from character counts (same as the shared captions), so the
  highlighted word can be slightly early/late.
- `ffprobe` isn't available; duration was verified with `ffmpeg -i` (21.37 s video vs 21.358 s audio —
  641 frames at 30 fps).

## Studio extensions (2026-11)

The renderer is also the engine of [`video/studio`](../studio/README.md):

- `render.py --project <dir>` renders a studio project (its `build/timeline.json`, assets from
  `assets/`, output `renders/<timestamp>.mp4`). `--progress` prints `@@PROGRESS {json}` lines, and
  `--stills-dir` sets where `--stills` writes. A timeline without `audio` renders with a silent track.
  The no-argument CLI is unchanged.
- `index.html?data=<url>` loads a data.js of the same origin (never another site's script). `?embed`
  loads nothing and waits for a timeline from the parent page: `postMessage({type: "load", tl})`, then
  `{type: "seek", t}` / `{type: "play", on}` (the studio's sandboxed live preview); `window.loadTimeline(tl)`
  still works for same-origin callers. `app.js` can re-mount with a new timeline without reloading.
- render.py opens the page from a virtual origin (`http://render.studio.local`, answered by a Playwright
  route from the renderer, its fonts and the folders the timeline's `file://` URIs name), not from
  `file://`; every other request is aborted and Chromium has a dead proxy and no DNS. Template code can
  therefore neither read other files on the disk nor send anything out.
- New layouts: `screen` (phone / browser / no frame, Ken-Burns scroll, camera zoom into `focus`,
  highlight ring + spotlight, callout pill with arrow, screen recordings via `<video>`) and
  `screens` (2–3 devices side by side or cascaded, labels synced to speech).
- 16:9 (1920x1080) for every layout (`#stage.land` CSS + geometry in JS). Brand colours come from
  the timeline, including the formerly hard-coded glow/gradient colours. An optional logo image is
  supported. Headlines shrink to fit.
