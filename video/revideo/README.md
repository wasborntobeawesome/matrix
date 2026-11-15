# Ovoz promo — Revideo option

Renders `../shared/build/timeline.json` and `../shared/build/voiceover.wav` to
`../out/revideo.mp4` (1080×1920, 30 fps, H.264 yuv420p + AAC, 21.40 s) with
[Revideo](https://github.com/midrender/revideo), the MIT-licensed fork of Motion Canvas
that adds headless rendering.

## Run

```bash
cd video/revideo
npm install          # also downloads Chrome for Testing for Puppeteer (~/.cache/puppeteer)
npm run render       # -> ../out/revideo.mp4   (WORKERS=1 npm run render for a single worker)
```

`npm run render` does three things:
1. `scripts/prepare-assets.mjs` copies the timeline, voice-over and fonts from `../shared`
   into `src/` and `public/`, and builds `src/envelope.json`, a loudness envelope (one value per
   frame) that drives the waveform.
2. `render.mjs` calls `renderVideo()` from `@revideo/renderer`. That starts Vite, opens the project
   in headless Chrome through Puppeteer, and pipes the frames to ffmpeg.
3. It remuxes the video stream (stream copy, no re-encode) with the original WAV, encoded once to
   AAC, using `../bin/ffmpeg.exe`.

`npm run editor` should open the Revideo editor UI for live preview, but I did not test it.

Files: `src/project.tsx` (the whole video in one scene), `render.mjs`, `scripts/prepare-assets.mjs`.
Preview frames from the middle of each scene are in `preview/`.

## Render time (this machine: 16 logical cores, Windows 11)

| workers | wall time for 641 frames |
|---|---|
| 1 | about 65 s (about 10 fps) |
| 3 (the default) | about 27 s (about 24 fps) |

The cold first run takes a few seconds longer because Vite pre-bundles dependencies. The 3-worker
output matches the 1-worker output (SSIM 0.995; the gap is encoder noise only).
Output size is about 4 MB (about 1.4 Mbit/s video).

## What the video does

- Dark brand background with three slowly drifting radial glows, a scrolling grid, floating
  particles and a vignette.
- A brand header (logo whose bars move with the voice), a waveform driven by the **real
  voice-over loudness**, a caption pill (one chunk at a time, pop-in, hidden in speech gaps) and
  a thin progress bar. Everything sits inside the IG safe zone (y 220–1540).
- **hero**: a kicker pill, then "Ovoz" in a gradient with glow, then "yangilandi" and the sub,
  staggered.
- **chips**: dashed placeholder slots appear first. Each language chip pops in when its name is
  spoken; the times come from the caption timings. The Arabic chip uses a mirrored layout.
- **stat**: a ring arc draws while the number counts 0 to 9, and 9 dots pop in around the ring.
  Then the headline and a sub pill.
- **list**: placeholder rows, then each emotion row slides in when it is spoken. Each row has an
  emoji badge and small animated equalizer bars.
- **cta**: the headline and sub, then a gradient "Ovoz" button with a play icon, a pulse, a
  breathing glow and ripple rings.
- Scene exits: fade, lift and scale-down, overlapping the next scene's entrance.

## Script support (Uzbek, Cyrillic, Arabic, emoji)

- **Uzbek** ʻ/‘ (U+02BB, U+2018) and **Cyrillic** "Русский" render correctly in Inter.
- **Arabic** "العربية" renders correctly shaped and right-to-left. It falls back from Inter to
  Noto Sans Arabic through the CSS font stack. One limitation: Motion Canvas/Revideo `Txt` draws
  wrapped text grapheme by grapheme and places each line at the x of its *first logical*
  character, which misplaces RTL lines. Workaround: `textWrap={false}` on RTL text, which draws it
  as one `fillText` run. Mixed or wrapped RTL paragraphs would need more work.
- **Emoji** render in colour through Chrome's Segoe UI Emoji on Windows (😊 😮 😌 🎙️). On Linux
  you would need a colour emoji font installed, for example Noto Color Emoji.
- Fonts are loaded with the `FontFace` API and a top-level `await` in `project.tsx`, so no frame
  is drawn with a fallback font.

## License and maintenance status

- Revideo packages (`@revideo/*` 0.11.0): **MIT**. They are based on Motion Canvas (MIT). Puppeteer
  is Apache-2.0. The bundled `@ffmpeg-installer`/`@ffprobe-installer` binaries are **GPL** ffmpeg
  builds. They are used only as external tools, and the final mux uses `video/bin/ffmpeg.exe`.
  The fonts (Inter, Noto Sans Arabic) are OFL.
- Revideo sends anonymous **telemetry** by default. `render.mjs` sets `DISABLE_TELEMETRY=true`.
- **Maintenance (checked 2026-11-14):**
  - npm `@revideo/core` was first published 2024-03. The 0.10.x releases ran through 0.10.4
    (2025-02-24).
  - Then the project went quiet for about 14 months: the last commit was 2025-05, and an alpha
    appeared in 2025-04.
  - It came back in July 2026: there were dependency updates, docs work and fixes such as
    "decouple editor plugin injection from core so headless renders work", ending in **0.11.0
    (2026-07-10)**.
  - The GitHub repo moved from `redotvideo/revideo` to **`midrender/revideo`**. It is not
    archived, has about 4.2k stars and 63 open issues, and was last pushed 2026-07-15.
  - So it is maintained in bursts by a small company team, with no activity in the last four
    months. It is usable, but you should pin versions.

## Pros

- A real programmatic animation API (Motion Canvas generators: `all`, `waitFor`, tweens,
  signals). Choreography such as "pop this chip when the word is spoken" is short and precise.
  Reactive signals (`() => …`) make background loops trivial.
- Headless `renderVideo()` from Node with **parallel workers**, which gave 27 s for this reel.
- Canvas rendering inside Chrome gives full browser text shaping (Arabic, emoji), gradients,
  shadows and flex layout.
- MIT licence, no paid tier needed for local rendering.
- `<Audio>`/`<Video>` nodes, and an editor UI for live preview.

## Cons and what was hard

- **Windows breakage out of the box:**
  - Revideo hard-codes `--single-process` for Chromium. With Puppeteer 25's Chrome this crashes
    the page ("Navigating frame was detached").
  - Fixed in `render.mjs` with an array whose `includes()` reports that the flag is already
    present. This is hacky, but it does not patch `node_modules`.
- **Vite dependency discovery** reloads the page during the first navigation. Fixed by listing
  the packages in `optimizeDeps.include`.
- **`outDir` quirk:** media for audio extraction is resolved as `<outDir>/../public/…`, so
  `outDir` must be a direct child of the project. The renderer writes to `./output`, and the
  script copies the result to `../out`.
- **Built-in audio mix is not transparent:**
  - The `<Audio>` track came out −3 dB, ~30 ms early, ~33 ms short and upmixed to stereo
    (`amix` plus a 2018 ffmpeg build bundled by `@ffmpeg-installer`).
  - I kept `<Audio>` in the scene, for the editor and for timing, but replace the track with the
    untouched WAV at the end. The result is verified: level ratio 0.9996 and zero lag against the
    source.
- **Reactive text crashes:** `text={() => …}` on a `Txt` throws "The scene is not available in
  the current context" at render time, and that number was blank in the first render. Use a
  `tween()` that calls `.text()` instead.
- **RTL wrapping bug**, described above.
- **No encoder controls:** the ffmpeg exporter uses default libx264 settings (CRF 23, about
  1.4 Mbit/s). It looks clean here, but there is no option for CRF or preset without
  re-encoding.
- **Heavy install:** about 377 MB `node_modules` plus a downloaded Chrome. Deprecation warnings
  appear on install (fluent-ffmpeg, mathjax-full).
- A harmless console warning ("Tried to access an asynchronous property before the node was
  ready") is printed by the `<Audio>` node.
