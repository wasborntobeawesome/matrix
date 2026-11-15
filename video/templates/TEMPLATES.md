# Video Studio templates

A **template** decides how a video looks: colours, type, cards, captions, the progress bar, where the
logo sits, how scenes enter and how one scene hands over to the next. The brief decides *what* is said
and shown; the template decides *how*. One brief renders in any template, in every format (9:16, 1:1,
16:9) and language (uz, ru, en, ar).

Templates live in `video/templates/<id>/`. Built in:

| id | kind | look |
|---|---|---|
| `aurora` | theme | The original look (default): dark, glowing brand gradients, soft grid and particles, glassy caption pill. Projects without a template use it, pixel-identical to before. |
| `clean` | full | Light and minimal (Apple-keynote style): app-icon hero, white cards, check-mark chips, a hairline list, thin progress bar, gentle *Drift* transition. |
| `bold` | full | High contrast, huge stacked uppercase type, sticker chips, word-box captions, moving colour bands; *Punch* and *Swipe* transitions. |
| `_starter/` | full | Not installed (folders starting with `_` are skipped). A small, commented template to copy. |

Contents: [Theme or full?](#theme-or-full) · [manifest.json](#manifestjson-reference) ·
[template.css](#templatecss) · [template.js API](#templatejs-api) · [Transitions](#transitions) ·
[Safe zones](#safe-zones-per-format) · [Captions and progress](#captions-and-progress) ·
[Right to left](#right-to-left-arabic-cyrillic-uzbek) · [Fonts and licences](#fonts-and-licences) ·
[Determinism](#determinism-the-one-hard-rule) · [Testing](#testing-with-validatepy) ·
[Packaging](#packaging-and-importing) · [From a Figma design](#from-a-figma-design) ·
[Quick start](#quick-start-from-_starter)

---

## Theme or full?

| | theme | full |
|---|---|---|
| Files | `manifest.json` (+ background pictures, fonts) | + `template.css`, optional `template.js` (ES module) |
| Can change | colours, fonts, radii, shadows, caption / progress / logo style, background picture, which background layers show | anything: CSS for every element, new HTML + animation for any layout, new transitions, background decorations |
| Code | none | JavaScript, run by the renderer in a sandbox (see *Packaging and importing*) |

Start with a **theme** if you only need brand colours and a font. Move to **full** when the design needs
different layouts.

## manifest.json reference

```json
{
  "id": "acme",                         // = folder name; 2-40 chars: a-z 0-9 -
  "name": "Acme",                       // shown in the template picker
  "description": "Our brand look",      // one or two sentences
  "author": "Acme design team",
  "version": "1.0.0",
  "licence": "Proprietary — Acme Inc.",  // of the template itself; fonts list their own
  "kind": "full",                       // "theme" | "full"
  "formats": ["9:16", "1:1", "16:9"],   // supported (validated) formats
  "layouts": ["hero", "chips", "stat", "list", "cta", "screen", "screens", "presenter"],
  "css": "template.css",                // full only (default name)
  "js": "template.js",                  // full only, optional
  "default_transition": "fade",         // built-in id or one listed in "transitions"
  "transitions": [{ "id": "flip", "name": "Flip" }],   // added by template.js (full only)
  "previews": ["preview-9x16.png", "preview-1x1.png", "preview-16x9.png"],
  "options": [ ... ],                   // per-project settings, see below
  "tokens": { ... }                     // design tokens, see below
}
```

`validate.py --previews` renders the preview images for you (the hero scene of the test video).

### tokens

Everything is optional; anything left out keeps the built-in (aurora) value.

```json
"tokens": {
  "tone": "dark",                       // "dark" | "light": light keeps the background light and flips
                                        // the built-in lines, particles and caption pill to dark-on-light
  "colors": {
    "background": "brand.background",   // hex, or a brief colour: brand.primary / brand.accent / brand.background
    "surface": "auto",                  // cards, chips, list rows ("auto" = derived from the background)
    "text": "#F5F7FF", "muted": "#9AA3C0",
    "primary": "brand.primary", "accent": "brand.accent",
    "primary_light": "auto",            // the light end of the headline gradient
    "on_accent": "#07101A",             // text on accent surfaces (callouts)
    "line": "rgba(255,255,255,.09)",    // card / chip borders (any CSS colour)
    "line_mix": "#2A3150",              // hex the active highlight blends from
    "track": "rgba(255,255,255,.08)"    // unlit stat ring segments
  },
  "fonts": {
    "display": "Inter",                 // headlines, numbers, buttons, brand name
    "body": "Inter",                    // everything else
    "display_weight": 800, "body_weight": 500,
    "files": [                          // bundled fonts (loaded and awaited before the first frame)
      { "family": "Acme Sans", "src": "fonts/AcmeSans.woff2", "weight": "100 900", "style": "normal",
        "licence": "SIL OFL 1.1" }
    ]
  },
  "radii":   { "card": 36, "chip": 40, "button": 999, "pill": 999 },     // px (pill = kicker, callout, labels, captions)
  "shadows": { "card": "0 18px 44px rgba(0,0,0,.3)", "device": "0 50px 120px rgba(0,0,0,.55)" },
  "caption": { "style": "pill",         // "pill" | "plain" | "bar" | "box"
               "background": "rgba(13,18,36,.74)", "border": "rgba(255,255,255,.1)",
               "text": "#F5F7FF", "highlight": "brand.accent",   // the word being spoken
               "highlight_text": "#0B0B0F",                      // "box" style: text on the highlight box
               "uppercase": false },
  "progress": { "style": "segments" },  // "segments" | "bar" | "dots" | "none"
  "logo": { "placement": "auto" },      // "auto" | "top-center" | "top-left" | "top-right" | "none"
  "background": { "image": "bg.jpg",    // theme picture under the glow (cover); b-roll clips still go on top
                  "fit": "cover", "position": "center", "opacity": 1,
                  "blobs": true, "grid": true, "particles": true, "vignette": true }   // false hides a built-in layer
}
```

The renderer turns tokens into CSS variables on the page — `--bg --surface --text --muted --primary
--primary-l --accent --font --font-display --w-display --w-body --r-card --r-chip --r-button --r-pill
--shadow-card --shadow-device --cap-bg --cap-border --cap-text --cap-hi --cap-hi-text --on-accent --line`
— and into classes on `#stage`: `tpl-<id>`, `tpl-full`, `tone-light`, `cap-<style>`, `cap-upper`,
`prog-<style>`, `logo-<placement>`, `bgx-no-<layer>`, `opt-<key>` / `opt-<key>-<value>`.

**Brand colours always apply**: a token like `"primary": "brand.primary"` follows the brief, so one
template serves every product. Hard-code a colour only where the design must not follow the brand
(e.g. the light background of `clean`).

### options (per-project settings)

```json
"options": [
  { "key": "glow", "type": "boolean", "default": true, "label": { "en": "Soft colour glow", "uz": "Yumshoq rang nuri" } },
  { "key": "button", "type": "select", "default": "brand", "label": "Button colour",
    "choices": [ { "value": "brand", "label": "Brand colour" }, { "value": "dark", "label": "Graphite" } ] },
  { "key": "tint", "type": "color", "default": "#FFB547", "label": "Tint" },
  { "key": "speed", "type": "number", "default": 1, "min": 0.5, "max": 2, "step": 0.1, "label": "Motion speed" },
  { "key": "tagline", "type": "text", "default": "", "label": "Tagline" }
]
```

The studio shows them under the template picker and stores them in the brief as
`"template_options": {"glow": false}`. `label` / `help` may be a string or `{"en": ..., "uz": ...}`.
Use them in CSS (`#stage.tpl-acme.opt-glow …`, `#stage.tpl-acme.opt-button-dark …`, `var(--opt-tint)`,
`var(--opt-speed)`) or in JS (`ctx.options.speed`).

## template.css

- Scope **every** rule to `#stage.tpl-<id>` — the studio preview switches templates without reloading
  the page, so unscoped rules would leak into other templates.
- Restyle any built-in element; the class names are stable: `#brand .logo .brand-name`, `.scene.<layout>`,
  `.headline .word .grad .kicker .sub`, `.chip .tick`, `.item .emo .label .idx`, `.stat-wrap .stat-num`,
  `.pill .cta-url`, `.scr-text .device .dev-phone .dev-browser .bbar .dscreen .focus-ring .callout .dev-label`,
  `.pv-card .pv-text`, `#captions .cap .eq .txt .w` (`.w.on` = the word being spoken, `.w.said` = already said),
  `#progress .seg .fill`, background `#bg #blobA #blobB #blobC #grid #particles #vignette`.
- `#stage.land` = 16:9, `#stage.square` = 1:1, neither = 9:16; `#stage.rtl` = Arabic.
- Layouts that template.js replaces get the class `tpl-layout` and the whole stage as their box.
- CSS `@keyframes` animations are allowed: the renderer pauses them and sets their time from the video
  clock (scene-local time inside a scene). CSS transitions are switched off.

## template.js API

An ES module with any of these exports:

```js
export const layouts = {
  hero: {
    build(scene, ctx) { ... return element; },   // once per scene, when the video is loaded
    seek(element, localT, ctx) { ... },          // every frame: draw the moment localT s after the scene start
  },
  // chips, stat, list, cta, screen, screens, presenter — any you don't export use the built-in layout
};
export const transitions = { flip: { name: "Flip", lead: 0.3, tail: 0.3, out(p, c) {...}, in(p, c) {...} } };
export function setup(ctx) { }      // once per video: add background decorations to ctx.layers.bg / .fg
export function frame(t, ctx) { }   // every frame, t = video time in seconds: move those decorations
```

**`scene`** is the localized brief scene (`kicker`, `headline`, `sub`, `items`, `stat`, `button`, `url`,
`asset`, `device`, `focus`, `callout`, … — plain strings in the video's language, Uzbek apostrophes already
fixed) plus `start`, `end`, `speech_end` (seconds, video time) and `layout`.

`build` may return `null` after filling `ctx.el` itself (e.g. `ctx.base("screen", scene, ctx.el)` to reuse
the built-in layout and add something on top). Elements with class `headline` are auto-fitted to the box
width and 2-3 lines (set `data-lines="2"`, or `data-fit="off"` to size them yourself). To measure
text in `build`, append your root to `ctx.el` first (it is in the page then).

**`ctx`** (per scene: `ctx.scene`, `ctx.el`, `ctx.index`, `ctx.last`):

| | |
|---|---|
| `W, H, format` | stage size, `"9:16"` / `"1:1"` / `"16:9"`; `portrait`, `square`, `land` booleans |
| `rtl, lang` | right-to-left (Arabic) and the language code |
| `safe, area` | title-safe zone and the scene content area `{x0, y0, x1, y1}` (stage px) — see below |
| `brand` | resolved colours `{name, primary, accent, background, surface, text, muted, primaryLight, light, logo}` |
| `tokens, options, template` | the manifest tokens, this project's option values, the template info |
| `scenes, duration, fps` | the whole timeline (e.g. for background motion that follows the scenes) |
| `layers.bg, layers.fg` | your layers: behind the scenes (above the brand glow) / above the scenes (below captions) |
| `h(tag, cls, text)` | create an element |
| `words(text, cls)` | headline split into `<span class="word">` (brand name + numbers get `.grad`) → `{box, list}` |
| `animWords(list, t, t0, gap)` | the built-in word-by-word rise |
| `rise(t, t0, opts)` → state, `style(el, state)` | `{x, y, s, r, o, blur}` → transform/opacity/filter |
| `prog(t, t0, d, ease)`, `ease.*`, `clamp`, `lerp` | timing helpers; `ease`: linear, outCubic, outQuint, outExpo, inCubic, inOutCubic, inOutSine, outBack, outBackBig |
| `mix(a, b, p)`, `mixHex`, `rgba(hex, a)` | colour helpers |
| `itemTimes(scene, items)` | when the voice says each item (aligned word timing) → video times |
| `audioAt(t)` | `{rms, bands[24]}` of the voice at t (0..1) — for waveforms |
| `media(name, scene)` | screenshot `<img>` / recording `<video>` (seeked for you) → `{el, aspect}` |
| `device(kind, aspect, box)` | phone / browser / none frame → `{g, dev, screen}` |
| `logo(box, nbars)` | brand logo image, or animated bars (returned for you to animate) |
| `base(layout, scene, el)` | build the built-in layout into `el` → its `update(t)` (video time) |
| `fit(box, lines)` | shrink a headline to fit |
| `noise(a, b)` | deterministic pseudo-random 0..1 (use instead of `Math.random`) |
| `fileUrl(name)` | URL of a file in your template folder |

## Transitions

A transition plays at the cut between two scenes, in the window `[cut − lead, cut + tail]`. `out(p, c)` /
`in(p, c)` get `p` 0 → 1 over that window and return a state for the outgoing / incoming scene:
`{x, y, s, r, o, blur, clip: [top, right, bottom, left] (fractions, CSS inset), flash: 0..1}`.
`c = {W, H, rtl, format, brand}`. The outgoing scene is drawn until `cut + tail`, the incoming one from
`cut − lead`. `flash` paints a full-frame flash in `--tx-flash` (default white).

Built in (`video/html/transitions.js`): `lift` (the original: the old scene lifts away and blurs), `fade`,
`slide` (follows the text direction), `zoom`, `wipe`, `blur`, `cut`. The project picks one in the
settings bar (`"transition"` in the brief); each scene can override it (`scene.transition`, the transition
*into* that scene); otherwise the template's `default_transition`. A duration override:
`{"type": "fade", "duration": 0.8}`. A transition that the current template doesn't have falls back to the
template default.

## Safe zones per format

Stage pixels. Platforms put their own buttons and text over the edges, so keep text inside the safe zone;
`validate.py` flags text outside it. The content area is where scene content goes: clear of the brand
header above and the captions below.

| format | stage | safe zone (`ctx.safe`) | content area (`ctx.area`) | brand header | caption pill centre | progress |
|---|---|---|---|---|---|---|
| 9:16 | 1080×1920 | x 50–1030, y 220–1560 | x 50–1030, y 318–1270 | y 244–308 | y 1345 (1410 in screen/screens scenes) | y 1478 |
| 1:1 | 1080×1080 | x 30–1050, y 20–1060 | x 40–1040, y 96–900 | y 30–82 | y 958 | y 1036 |
| 16:9 | 1920×1080 | x 60–1860, y 20–1060 | x 110–1810, y 118–915 | y 44–100 | y 966 | y 1040 |

In 9:16 the bottom ~360 px and the top ~220 px are covered by TikTok / Reels / Shorts UI; the right edge
has the like/share rail, so don't put small text in the right 100 px of the content area either.

**Screen-scene callout** (`.callout`, built-in `screen` layout): the renderer positions it, a template only
styles it. Without a focus it gets its own strip under the device (the device box is shortened by the callout's
height + 26 px), or above the device with `callout_position: "above"`. It never sits on the screen. With a
focus, `auto` puts it below the focus area if it fits, else above, else on the side where it covers less of
it; `above` / `below` force a side. A template that restyles `.callout` should keep its height reasonable,
because a taller callout makes the device smaller.

## Captions and progress

The captions (`#captions .cap`) and progress (`#progress`) are drawn by the renderer for every template,
above the scenes and your `fg` layer. Templates style them (tokens + CSS) but don't move them far: the
caption sits between the content area and the progress bar, so a layout that stays inside `ctx.area`
never collides with it. In 9:16 `screen`/`screens` scenes the caption moves down to y 1410, below the phone.

- Caption text comes in chunks of ~3 words timed to the voice; the word being spoken gets
  `caption.highlight` (and the `.w.on` class), words already said `.w.said`.
- Styles: `pill` (glass pill with a small equaliser), `plain` (text with a shadow), `bar` (rounded box that
  may wrap), `box` (big words, the spoken one on a highlight box — TikTok style). Non-pill captions shrink a
  long line up to ~30 % before they wrap.
- When the project turns captions off, `#captions` is hidden; design layouts so they still look balanced.
- Progress: `segments` (one per scene), `bar` (one continuous bar), `dots`, `none`. RTL fills from the right.

## Text over footage (AI b-roll backgrounds)

A scene can have an AI video clip behind it (`background: {type: "broll", dim, blur}`). Your text must stay
readable over it — the Ovoz QA run found the original Clean look washing the clip out with its light page
colour, so the teal kicker and the grey sub disappeared (QA #4). The rules:

- The renderer adds **`.on-media`** to such a scene element (`#scenes > .scene.on-media`) and, while footage
  covers most of the frame, **`media-bg`** to `#stage` (for the header / progress bar outside the scenes).
- It dims the clip per scene with `dim` (default 0.55), darker at the top and bottom. Dark templates dim towards
  their background colour. **Light templates (`tone: "light"`) dim towards a near-black tint of the brand colour
  with a floor of 0.62** and fade their pastel glow away — never a light wash.
- Built-in rules (`html/style.css`), for every template: a soft dark radial scrim behind the scene content, a dark
  halo (`text-shadow`) on `.headline`, `.sub`, `.kicker`, `.cta-url`, a near-white `--muted`, and a dark pill
  behind the base `.kicker` in dark templates. Light templates also get `--text: #fff`, dark translucent
  `--surface` cards and a white header / progress bar.
- In your template.css, give coloured text and your own classes the same care under `.scene.on-media`: lighter
  tints of `--primary` / `--accent` (e.g. `color-mix(in srgb, var(--primary) 40%, #fff)`) and a halo. Remove
  shadows from gradient-clipped text (`background-clip: text`), or they paint over the fill. See the
  `.on-media` blocks in `clean/template.css`, `bold/template.css` and `_starter/template.css`.
- Targets (WCAG AA): **4.5:1** for body text, **3:1** for large text (>= 66 stage px, or >= 50 px semi-bold),
  measured by `validate.py` (below) against a busy, bright sample frame.
- The studio's live preview shows a **placeholder still** (`html/broll-placeholder.jpg`, tagged "AI clip
  placeholder") for a scene that asks for an AI clip that is not generated yet, so the template picker shows
  the contrast before the slow AI stage. Renders never use it (a missing clip renders the brand gradient).

## Right to left (Arabic), Cyrillic, Uzbek

- Arabic: `#stage.rtl`, `dir="rtl"`. Flexbox / grid rows flip automatically, so build layouts with flex/grid
  and logical properties (`text-align: start`, `margin-inline-start`) instead of absolute left/right.
  Mirror anything you position by hand (`ctx.rtl`), and slide motion (`x * (ctx.rtl ? -1 : 1)`).
  `logo.placement` left/right are mirrored automatically. Letter-spacing is forced to 0 (it breaks the
  joining of Arabic letters) and Arabic needs taller lines: add `#stage.rtl.tpl-<id> .headline { line-height: 1.25 }`.
  `text-transform: uppercase` does nothing to Arabic — fine.
- Fonts: Inter covers Latin, Cyrillic and Uzbek (oʻ gʻ ʼ); Arabic falls back to the bundled Noto Sans
  Arabic. If you bundle a display font without Cyrillic or Arabic, those languages fall back to Inter /
  Noto — check them with `validate.py` (it renders en, ar, ru and uz).
- Russian and Uzbek words are long ("Синхронизация", "jamgʻarmangiz"): size headlines from measurements,
  not character counts, and allow two lines.
- Russian text arrives with a no-break space (U+00A0) after the one-letter prepositions / conjunctions
  в к с и а о у я ("текст в голос"), so they never end a line. `ctx.words()` keeps such a pair in one
  `.nbw` nowrap group (each word still animates on its own); if you split text yourself, split on ordinary
  spaces (`/[ \t\r\n]+/`), not `/\s+/` — in JavaScript `\s` also matches U+00A0.

## Fonts and licences

- Bundle font files in the template folder and list them in `tokens.fonts.files` (woff2 preferred; ttf/otf
  work). They are loaded and awaited before the first frame, so measurements and renders are exact.
- Only bundle fonts whose licence allows embedding in videos and redistribution with the template
  (SIL Open Font License fonts from Google Fonts are a safe default). Put the licence text next to the font
  and its name in `"licence"`.
- A font named in `display`/`body` that is neither bundled nor Inter is only used if it is installed on the
  PC — `validate.py` warns and reports fonts that did not load.

## Determinism: the one hard rule

Every frame must be a pure function of time. The renderer captures frames out of order in 8 parallel
pages, and the live preview seeks back and forth — anything that depends on wall-clock time, the order of
frames or randomness flickers or differs between renders.

- Compute everything in `seek(el, localT)` / `frame(t)` from the time you are given. Keep state only for
  things that don't change (elements, measurements made in `build`).
- No `setTimeout`, `setInterval`, `requestAnimationFrame`, `Date.now()`, `performance.now()`,
  `Math.random()` for motion. The renderer enforces this while your code runs: timers are refused (and
  reported), the clock is frozen at the frame time and `Math.random` is seeded per scene and frame — use
  `ctx.noise(i, k)` for stable pseudo-randomness.
- CSS `@keyframes` are paused and driven by the clock; CSS `transition`s are disabled on the stage.
- Full templates render without compositor-layer hints (`will-change`), so a frame never depends on which
  frames were drawn before it.

## Testing with validate.py

```sh
video/.venv/Scripts/python video/templates/validate.py acme                 # an installed template
video/.venv/Scripts/python video/templates/validate.py path/to/acme-folder  # before installing
video/.venv/Scripts/python video/templates/validate.py --previews acme      # + write preview-*.png
video/.venv/Scripts/python video/templates/validate.py --sheet out/ acme    # + contact sheets of every frame
video/.venv/Scripts/python video/templates/validate.py                      # all installed + _starter
```

It checks the manifest (schema, ids, colours, files, fonts, options, transitions), the JS syntax (when
Node is installed), then renders a test video — every layout, realistic but long-ish copy — in 9:16, 1:1
and 16:9 and in English, Arabic, Russian and Uzbek (`--quick`: en + ar), and on a settled frame of every
scene reports:

- load errors (template.css / template.js / fonts) and exceptions in your code,
- text outside the frame or the safe zone, text clipped by an `overflow: hidden` box, scene text that
  overlaps the caption or the brand header,
- declared fonts that did not load,
- non-determinism: the same moments rendered twice (fresh page, different seek order) must match.
- contrast over footage: every layout and format again over a b-roll background (dim 0.5) — by default the
  sample frame `video/html/broll-placeholder.jpg` (busy, bright bokeh: a worst case), or your own
  `--frame some-frame.png` / `--frame clip.mp4` (a frame at 1.5 s). For each text element it compares the text
  colour with the pixels behind it (a second screenshot with the text hidden; your halos and scrims count) and
  takes the 10th percentile, so a bright spot behind a few letters fails too. Below 4.5:1 (3:1 for large text)
  it prints `CONTRAST … 2.1:1 over footage (needs 4.5:1)`. Text on its own opaque or gradient box (a button) is
  skipped. `--no-contrast` skips the check; with `--sheet DIR` the frames go to `DIR/contrast/`.
  `--make-sample` rewrites the sample frame (deterministic numpy, no AI model).

Exit code 0 = clean. The studio's import runs the manifest + syntax checks; run `validate.py` for the
render checks. While you work, keep the project open in the studio: **↻** in the preview bar (or *Reload*
in the template picker) re-reads your files, so edits to template.css / template.js show without a restart.

## Packaging and importing

1. Zip the folder: `manifest.json` at the top of the zip, or inside one folder (`acme/manifest.json`).
   Allowed files: json, css, js/mjs, png, jpg, webp, svg, gif, woff/woff2/ttf/otf, txt, md; ≤ 300 files,
   ≤ 40 MB unpacked.
2. Studio → open a project → **Template** (top bar) → *Import a template* → choose the zip → *Import*
   (tick *Replace if installed* to update one), or:
   `curl -F "file=@acme.zip" "http://127.0.0.1:8001/api/templates?replace=1"`.
3. Problems are listed all at once (e.g. *tokens.colors.primary: 'blue' — use a hex colour*); nothing is
   installed until the zip is valid. It lands in `video/templates/acme/`.

Rules the import enforces (a zip that breaks one is refused): file names are plain relative paths (no `..`,
no absolute / drive / UNC paths, no backslashes, no Windows device names like `con.css`, no trailing dot or
space, at most 6 folders deep, no two names that differ only in case), no symbolic links, each file ≤ 15 MB,
manifest texts have length limits (name 80, description 600 characters), ≤ 40 options, ≤ 20 transitions,
tokens ≤ 32 KB.

**Where template code runs.** `template.js` / `template.css` run inside the renderer page, never in the
studio itself: the live preview is a sandboxed iframe (an opaque origin with no access to the editor, its
storage or the studio API), and a render serves the page from a private origin that exposes only the renderer,
its fonts, your template folder and the project's media — no other files and no network (`fetch` to anything
else fails). Bundle every font and picture in the template folder; remote URLs do not load in a render.

API: `GET /api/templates` · `GET /api/templates/{id}` · `GET /api/templates/schema` ·
`POST /api/templates` (zip) · `DELETE /api/templates/{id}` (not built-ins; projects using it fall back to
aurora) · files at `/templates/{id}/…` · preview another template without saving:
`GET /api/projects/{id}/timeline?template=<id>`.

## From a Figma design

1. **Frames.** Design three frames per scene type at the stage sizes: 1080×1920, 1080×1080, 1920×1080.
   Add the safe zone and content area from the table above as a locked layer so the design respects them.
2. **Export assets.** Background pictures and decorative shapes: *Export → PNG 1x* (or SVG for flat
   shapes) into the template folder. Icons you place in CSS: SVG. Don't export text — text is live HTML.
3. **Map styles to tokens.**
   - Color styles → `tokens.colors`. Your "brand" swatches should become `brand.primary` / `brand.accent`
     so the template works for every product; neutrals (background, surface, text, muted, lines) stay hex.
   - Text styles → `tokens.fonts` (family + weights) and CSS font sizes per format. Figma line height %
     → CSS `line-height` (e.g. 104 % → 1.04), letter spacing % → `em` (−3 % → −0.03em).
   - Effect styles (drop shadows) → `tokens.shadows` (Figma x, y, blur, spread, colour = CSS box-shadow).
   - Corner radii → `tokens.radii`.
4. **Build layouts with CSS.** Start with CSS only, restyling the built-in layouts (`.chip`, `.item`,
   `.pill`, …). Right-click the studio's live preview → *Inspect* (browser DevTools) to find class names
   and try values live. Use flexbox/grid with Figma's auto-layout values (gap, padding, alignment) so
   Arabic flips.
5. **Only then add template.js** for layouts whose structure differs (e.g. a stacked headline): build the
   DOM in `build`, animate in `seek` with `ctx.prog` and the easings (Figma *Smart animate* "ease out" ≈
   `outCubic`, "gentle" ≈ `inOutSine`, "bouncy" ≈ `outBack`).
6. Run `validate.py --previews --sheet out/`, compare the contact sheets with the Figma frames, iterate.

## Quick start from _starter

```sh
cp -r video/templates/_starter video/templates/acme       # (or copy the folder in Explorer)
# edit manifest.json: "id": "acme", "name": "Acme", colours, fonts
# in template.css / template.js: rename tpl-starter -> tpl-acme
video/.venv/Scripts/python video/templates/validate.py acme --previews
```

The studio lists `acme` immediately (refresh the template picker). `_starter` shows the whole API in about
60 lines: tokens, an option, a CSS-only restyle of built-in layouts, one replaced layout (hero) and one
transition (rise).
