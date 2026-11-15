# Option: pure Python — MoviePy 2 + Pillow

Renders `video/shared/build/timeline.json` + `voiceover.wav` to `video/out/moviepy.mp4`
(1080×1920, 30 fps, H.264 High / yuv420p, CRF 18, AAC 48 kHz).

## Install / run

Everything except two small optional extras is already in the shared venv
(`moviepy>=2`, `pillow`, `numpy`, `imageio-ffmpeg`).

```sh
# one-time: fetch the extras into video/moviepy/_dll and _vendor (git-ignored)
video/.venv/Scripts/python video/moviepy/setup_deps.py

# render
video/.venv/Scripts/python video/moviepy/render.py [--out video/out/moviepy.mp4] [--workers N]

# preview single frames as PNG (no video)
video/.venv/Scripts/python video/moviepy/render.py --frames 72,213,352,498,606
```

`setup_deps.py` downloads:
- `_dll/fribidi-0.dll` (conda-forge build, LGPL-2.1). Pillow's Windows wheels already
  contain libraqm + HarfBuzz but need FriBiDi at runtime; with it on `PATH`,
  `features.check('raqm')` becomes `True` and Arabic gets real HarfBuzz shaping + RTL.
- `_vendor/` arabic-reshaper (MIT) + python-bidi (LGPL-3.0), a fallback used only when raqm
  is unavailable.

ffmpeg: uses `video/bin/ffmpeg.exe` (via `FFMPEG_BINARY`), falls back to imageio-ffmpeg's.

## Render time (this machine: Ryzen 7 9800X3D, 16 threads)

| mode | wall time for 641 frames (21.4 s) |
|---|---|
| default (`--workers 14` process pool → MoviePy → ffmpeg) | **~13 s** (≈ 0.6× real time) |
| `--workers 1` (plain MoviePy, single process) | ~27 s |

Output size ≈ 10 MB (CRF 18). Frame previews: ~0.1 s for 5 frames after a 0.5 s build.

## How it works

- One custom `VideoClip(frame_function)`; MoviePy handles the frame loop, the ffmpeg pipe,
  audio (re-encoded to AAC) and muxing.
- Static layers (headlines, chips, cards, pills, glows, logo) are rendered **once** with
  Pillow as RGBA sprites. Per frame only transforms are applied (translate / scale /
  opacity / Gaussian blur during zoom-in & exit) and `alpha_composite`d.
- Background: three brand-coloured glow blobs computed in numpy at 1/6 resolution, drifting
  and re-arranging per scene, bicubic upscale, vignette, a faint dot grid and ±3 dithering
  noise (avoids gradient banding after H.264).
- Live layers: audio-reactive mirrored waveform (FFT of the actual voice-over, 16 log bands)
  in the hero, a mini level meter inside the caption pill, a 0→9 count-up with a gradient
  progress ring, a pulsing CTA pill with ripples, a stories-style segmented progress bar
  (one segment per scene), and a caption pill that morphs its width between chunks.
- Speed: the frame function is pure Python/Pillow, so frames are rendered by a `spawn`
  process pool (`imap`, in order) and handed to MoviePy, which stays the encoder/muxer.

## Script / language support

| | result |
|---|---|
| Uzbek Latin (`Oʻzbekcha`, `his-tuyg‘u`, `koʻring`) | correct — Inter has ʻ (U+02BB) and ‘ |
| Cyrillic (`Русский`) | correct (Inter) |
| Arabic (`العربية`) | correct joined shaping, RTL — via raqm (after `setup_deps.py`); fallback arabic-reshaper + python-bidi also tested OK for this word |
| Emoji (😊 😮 😌 🎙️) | rendered in colour via Segoe UI Emoji (`embedded_color=True`), Windows-only font |

Font fallback is manual: text is split into runs (Inter / Noto Sans Arabic / Segoe UI Emoji)
per character and drawn on a shared baseline. Mixed-direction *lines* (Arabic + Latin in the
same line) are not bidi-reordered across runs — fine for this script, a limitation in general.

## Licenses

- MoviePy — MIT. Pillow — MIT-CMU (HPND). numpy — BSD-3. imageio-ffmpeg — BSD-2.
- ffmpeg binary — GPL (gyan.dev build); output video carries no license obligations.
- FriBiDi DLL — LGPL-2.1 (dynamically loaded, not committed). arabic-reshaper — MIT;
  python-bidi — LGPL-3.0 (fallback only).
- Fonts: Inter, Noto Sans Arabic — OFL. Segoe UI Emoji — Microsoft, ships with Windows
  (used locally to rasterise; check terms before redistributing the font itself).

## Pros

- 100% Python, no Node/Chromium, tiny dependency set; deterministic, fast (13 s for 21 s).
- Full pixel control; easy to feed real data (audio FFT drives the waveform).
- Permissive licensing (MIT/BSD) for the toolchain itself.

## Cons

- No layout engine, no CSS: every card, wrap, gradient text, shadow, anti-aliased rounded
  rectangle (4× supersampling) and easing is hand-written (~950 lines). Iterating on design
  is slower than with HTML/React-based tools; there is no live preview/timeline UI.
- Text shaping depends on Pillow being built with raqm; on Windows that needs an extra DLL.
- No GPU; per-frame Pillow work is CPU bound (solved with a process pool).
- MoviePy 2.1.2 quirks: it always appends `-pix_fmt yuva420p` for libx264 (ffmpeg then
  auto-falls back to yuv420p — verified in the output), ignores the `pixel_format`
  argument for the output, and upmixes the mono voice-over to stereo AAC.

## What was hard

- Arabic: `features.check('raqm')` was `False` out of the box → found that the wheel only
  lacks `fribidi-0.dll`; a 100 KB conda-forge DLL fixes it.
- Mixing three fonts on one baseline (emoji / Arabic / Inter) and gradient-filled text with
  colour emoji on top.
- Smooth caption pill that resizes between chunks without text spilling outside (clipped
  text layer + 2-half pill reuse).
