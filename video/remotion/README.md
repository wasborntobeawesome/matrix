# Option: Remotion (React → MP4)

The Ovoz promo reel (1080×1920, 30 fps, 21.36 s) is written as a React component and rendered
with [Remotion](https://www.remotion.dev) 4.0.543. Everything is driven by
`../shared/build/timeline.json`; the audio is `../shared/build/voiceover.wav`.

Output: `../out/remotion.mp4` (H.264 High, yuv420p BT.709, CRF 18 + AAC 48 kHz, about 9.9 MB).

## Install and run

```bash
cd video/remotion
npm install          # pinned: remotion / @remotion/cli / @remotion/media-utils 4.0.543, React 19.1
npm run render       # → ../out/remotion.mp4
npm run studio       # Remotion Studio: live preview + scrubbing in the browser
```

You can also start the preview directly with `npx remotion studio` after `npm run prepare-assets`.
It hot-reloads as you edit `src/*.tsx`, so it's the best way to tweak timing and layout.

- `scripts/prepare.mjs` (run automatically by every npm script) copies `timeline.json`,
  `voiceover.wav` and the two fonts into `public/`. The timeline is then **imported at bundle
  time** (`src/timeline.ts`), so composition width, height, fps and duration (`frames` = 641) all
  come from it. If the timeline changes, re-run `npm run render`.
- The first render downloads Chrome Headless Shell (about 1 minute, once). Remotion bundles its own
  ffmpeg, so `video/bin/ffmpeg.exe` is only needed for extracting preview frames.
- `render` uses `--concurrency=8` (this machine has 16 logical cores), JPEG frames at quality 95
  and `--crf=18`.

## Render time (this machine, Windows 11, 16 threads)

| Step | Time |
|---|---|
| `npm install` | ~30 s (813 MB node_modules, including Chrome Headless Shell) |
| First-ever render (includes Chrome download) | ~1.5 min |
| `npm run render` (bundle + 641 frames + encode) | **24 s** wall clock (measured twice: 24 s, 24 s) |

Output duration is 641 frames = 21.367 s. The voice-over is 21.358 s, so they match to within one
frame. The container reports 21.38 s because of AAC priming.

## Files

- `src/timeline.ts`: typed import of the timeline, plus a `sec()` helper that converts seconds to frames.
- `src/Promo.tsx`: the composition: animated background (scene-travelling glows, a drifting dot
  grid and a vignette), a story-style segmented progress bar with the brand mark and scene counter, the
  caption pill, and `<Audio>`.
- `src/scenes.tsx`: the five layouts (`hero`, `chips`, `stat`, `list`, `cta`), with spring
  entrances and a blur/scale/fade exit that overlaps the next scene.
- `src/audio.tsx`: decodes the voice-over once (`@remotion/media-utils`). The hero waveform, the
  logo bars and the glow intensity react to the **real** voice amplitude and spectrum.
- `src/fonts.ts`: loads `Inter.ttf` and `NotoSansArabic.ttf` (both variable fonts, weights 100–900)
  through the `FontFace` API inside `delayRender()`, so no frame renders before the fonts are ready.
- `preview/`: frames taken from the final MP4 at `t2.0 … t20.5` seconds (one or more per scene,
  plus one transition frame).

## Licenses

**Remotion is not OSI open source.** It uses the "Remotion License", which is source-available with
two tiers. Quoted from `node_modules/remotion/LICENSE.md` (v4.0.543):

> Individuals and small companies are allowed to use Remotion to create videos for free (even
> commercial), while a company license is required for for-profit organizations of a certain size.

> ### Eligibility
> You are eligible to use Remotion for free if you are:
> - an individual
> - a for-profit organization with up to 3 employees
> - a non-profit or not-for-profit organization
> - evaluating whether Remotion is a good fit, and are not yet using it in a commercial way

> ### Allowed use cases
> Permission is hereby granted, free of charge, to any person eligible for the "Free License", to
> use the software non-commercially or commercially for the purpose of creating videos and images …

> ### Disallowed use cases
> It is not allowed to copy or modify Remotion code for the purpose of selling, renting, licensing,
> relicensing, or sublicensing your own derivate of Remotion.

> ## Company License
> You are required to obtain a Company License to use Remotion if you are not within the group of
> entities eligible for a Free License. … Visit remotion.dev for pricing and to buy a license.

The file also says: *"In Remotion 5.0, the license will slightly change"* (remotion-dev/remotion PR #3750).

**Summary:** free for an individual, a company with **≤ 3 employees**, or a non-profit, including
commercial videos. A for-profit with 4 or more employees needs a paid Company License. Check this
again before upgrading to Remotion 5.

Other components: React (MIT), Chrome Headless Shell (BSD-style and other licenses), the bundled
ffmpeg (LGPL/GPL build shipped by Remotion), Inter (SIL OFL 1.1, see `../shared/fonts/OFL-Inter.txt`),
Noto Sans Arabic (SIL OFL 1.1). The emoji come from the OS font (Segoe UI Emoji on Windows),
and the shared emoji glyphs have their own terms.

## Script support

| Text | Result |
|---|---|
| Uzbek Latin `Oʻzbekcha`, `his-tuyg‘u`, `ko‘ring`, `GPU’da` | Correct. Inter has U+02BB and U+2018/2019, so there is no tofu |
| Cyrillic `Русский` | Correct (Inter Cyrillic) |
| Arabic `العربية` | Correct. Chromium/HarfBuzz shapes the letters (joined forms), runs it right-to-left (`dir="rtl"`) and uses Noto Sans Arabic |
| Emoji `😊 😮 😌 🎙️` | Rendered in **colour** through Chrome's system emoji font (Segoe UI Emoji). On Linux/CI you'd need `fonts-noto-color-emoji` installed, or the emoji will be missing or monochrome |

This is Remotion's biggest strength: it renders with a real browser engine, so complex-script
shaping, bidi, font fallback and colour emoji work exactly as they do on a web page.

## Pros and cons

**Pros**
- Full HTML/CSS/SVG/React. Gradients, `background-clip:text`, blur, shadows, masks and flex/grid
  layout are all available, which gives the most "app-promo" look for the least effort.
- `spring()` and `interpolate()` give good motion out of the box. Rendering is deterministic and
  frame-based.
- Remotion Studio gives live preview and a timeline scrubber with hot reload.
- Audio-reactive visuals are easy with `@remotion/media-utils`.
- Fast enough: 24 s for a 21 s 1080×1920 video on this machine.
- Data-driven: the composition is generated from `timeline.json`, so it scales to templated videos.

**Cons**
- **License**: free only for individuals, companies with ≤ 3 employees and non-profits. Larger
  companies must pay.
- Heavy install (about 800 MB including Chrome Headless Shell). Needs Node and a Chromium download
  on the first render.
- You need to know React/TypeScript. Designers who don't code can't use it alone.
- Emoji depend on the system's emoji font (good on Windows/macOS, needs a package on Linux).

## What was hard / gotchas

- Fonts must be loaded with `delayRender`/`continueRender` (or `@remotion/fonts`). Otherwise the
  first frames in each worker tab can render in a fallback font.
- With the default JPEG frames, ffmpeg tags the output as full-range `yuvj420p`.
  `Config.setColorSpace('bt709')` in `remotion.config.ts` fixes this, and the output is now
  standard `yuv420p` BT.709.
- Scene crossfades: I render each scene a few frames past its end with an exit animation, and
  start the next scene's entrance animations 4 frames late, so the two scenes don't visually
  collide. `@remotion/transitions` would also work, but its `TransitionSeries` overlaps scenes
  and shortens the total duration, which conflicts with a fixed timeline.
