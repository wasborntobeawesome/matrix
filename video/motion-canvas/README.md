# Option: Motion Canvas

Renders `../shared/build/timeline.json` + `voiceover.wav` to `../out/motion-canvas.mp4`
(1080x1920, 30 fps, 641 frames, H.264 High yuv420p + AAC 48 kHz, 21.37 s, ~2.4 MB).

## Install / run

```bash
cd video/motion-canvas
npm install            # Motion Canvas 3.17.2, Vite 5.4.11, Playwright 1.65.1 (pinned)
npm run render         # headless render  -> ../out/motion-canvas.mp4
npm run render -- --preview   # also writes mid-scene PNGs to ./preview/
npm run dev            # optional: the normal Motion Canvas editor at http://localhost:9177
```

Playwright reuses the Chromium build (revision 1255) that the Python venv already installed in
`%LOCALAPPDATA%\ms-playwright`. On a fresh machine, run `npx playwright install chromium` once.

## How the headless render works

Motion Canvas has no official CLI renderer (v3). It renders only inside a browser page that the
Vite dev server serves. `render.mjs` handles this without the editor UI:

1. It copies the shared inputs (timeline, voice-over, fonts) into `./assets` (git-ignored).
2. It starts the Vite dev server through Vite's JS API.
3. It opens `render.html` in **headless Chromium** with Playwright. That page (`src/render-entry.ts`)
   imports the project with `?project` and calls Motion Canvas's own **`Renderer.render()`** with the
   official **`@motion-canvas/ffmpeg`** exporter. Each frame goes to the dev server as a PNG over the
   Vite websocket. The server pipes the frames into ffmpeg, which muxes in the voice-over. The UI does the same thing when you press RENDER.
4. It polls `window.__frame` / `window.__result`, closes everything, copies
   `output/motion-canvas.mp4` to `../out/`, and prints the streams.

I don't click any buttons or scrape the DOM, so the run is repeatable and fails loudly with
a non-zero exit when something goes wrong. **Automatability: good, but it isn't built in.** You
need your own ~100-line driver (Vite + Playwright) and a small page. Both use only public exports
(`Renderer`, `RendererResult`, and the `?project` import), though `Renderer` is framed as an
"editor" class. Upgrades could break this, so the versions are pinned.

## Render time (this machine: 16 logical cores, Windows 11)

| step | time |
|---|---|
| Vite start + project load | ~1 s |
| render 641 frames + ffmpeg encode (in parallel) | **51–62 s** (≈ 0.4x real-time) |
| total `npm run render` | **~55 s** |

The bottleneck is the PNG encode plus base64 transfer per frame (canvas → dataURL → websocket →
ffmpeg). It runs single-threaded in one browser tab.

## Design

There is one Motion Canvas scene (`src/scenes/main.tsx`). Every scene, item and caption is
timed from `timeline.json` with an absolute `until(t)` helper, so the cut follows the voice-over
exactly. The total is 641 frames, matching 21.358 s.

- **Background:** dark brand colour with three drifting radial-gradient glows, a faint dot grid and a vignette.
- **Header:** an "Ovoz" wordmark with an animated equaliser logo, top safe edge at y≈280.
- **Scene changes:** each scene enters with fade, rise and pop staggers, then exits with fade, lift and scale during the timeline's trailing pad.
- **hero:** kicker pill, a gradient "Ovoz" headline with a second line, the sub, and an animated waveform.
- **chips:** 4 language chips with UZ/RU/EN/AR badges. They pop in (easeOutBack) at times spread over the spoken line, so the chips land roughly as the voice names each language.
- **stat:** a gradient "0→9" count-up inside a glowing progress ring, plus the headline and sub.
- **list:** 4 emoji rows sliding in, roughly in sync with the voice.
- **cta:** headline, sub, and a pulsing gradient "▶ Ovoz" pill with a ripple ring.
- **Captions:** a rounded pill at y≈1390 that pops on each new chunk and hides in the gaps. A gradient progress bar sits at y≈1508. All content stays between y=220 and y=1540 (Instagram safe zone).

## Text / script support (checked in the extracted frames)

- **Uzbek** `Oʻzbekcha`, `his-tuyg‘u`, `koʻring`: OK, since Inter has U+02BB and U+2018.
- **Cyrillic** `Русский`: OK (Inter).
- **Arabic** `العربية`: correctly shaped and right-to-left. Text is laid out by Chromium's DOM and
  drawn with canvas `fillText`, so browser shaping and bidi apply. Inter falls back to Noto Sans Arabic
  through the `fontFamily` list. I loaded the fonts with the `FontFace` API before the scene builds.
- **Emoji** 😊 😮 😌 🎙️: rendered in colour with the Windows system **Segoe UI Emoji** font. On Linux
  you would need a colour-emoji font installed, such as Noto Color Emoji. The 🎙️ glyph is grey in Segoe's design.

## Licenses

- Motion Canvas (`core`, `2d`, `ui`, `vite-plugin`, `ffmpeg`): **MIT**. Vite: MIT. Playwright: Apache-2.0.
- The `@motion-canvas/ffmpeg` exporter pulls in `@ffmpeg-installer/ffmpeg`. On Windows that is an
  **ffmpeg build from 2018 (N-92722) licensed GPLv3**. That's fine for producing videos (output isn't
  covered by the GPL), but note it if you redistribute the tool. You could swap in `video/bin/ffmpeg.exe`
  by rendering an image sequence and muxing yourself.
- Fonts: Inter and Noto Sans Arabic are OFL.

## Pros

- Free and MIT. The animation code is real TypeScript (generators, signals, tweens), well suited to
  data-driven, precisely timed motion graphics.
- Fine-grained timing control: frame-exact and deterministic, with no dropped frames, because rendering isn't real-time.
- Browser text engine: Arabic shaping and RTL, Cyrillic, and colour emoji all work.
- Built-in flexbox layout (`layout` prop), gradients, shadows and easing library. The output looks polished.
- Comes with a live-preview editor (`npm run dev`) with a timeline, which is great for iterating on design.

## Cons / what was hard

- **No official headless/CLI render.** You must write a driver that combines the Vite dev server,
  headless Chromium and `Renderer`. It works well once written, but it relies on semi-internal APIs.
- The ffmpeg exporter has **no quality settings**: libx264 at defaults (CRF 23, ≈815 kb/s here) and
  AAC at 64 kb/s mono. The gradients still look clean in the checks I ran. For higher quality, use the image-sequence
  exporter plus your own ffmpeg (`-crf 18`).
- The exporter receives the audio path as a URL with its leading `/` stripped, relative to cwd. It
  breaks for files outside the project root, so the voice-over is copied into `./assets`.
- The plugins are CommonJS. With `"type": "module"`, `vite.config.ts` needs `.default` unwrapping.
- Bug I hit and fixed: animation helpers that set an element's "hidden" start state inside a lazy
  generator leave staggered items visible until their turn. Start states must be set eagerly.
- About 295 MB of `node_modules`. Performance is ~0.4x real-time, slower than native renderers.

## Files

- `render.mjs`: one-command headless render (Vite, Playwright, Renderer, ffmpeg exporter).
- `render.html`, `src/render-entry.ts`: the headless render page.
- `src/project.ts`, `src/fonts.ts`, `src/project.meta`: the project, font loading, and render settings (also used by the editor).
- `src/scenes/main.tsx`: all animation and layouts.
- `preview/*.png`: mid-scene frames extracted from the final mp4.
