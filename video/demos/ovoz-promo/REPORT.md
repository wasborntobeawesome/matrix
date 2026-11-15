# Ovoz promo: Video Studio QA report

2026-11-15, 18:29–19:20. Tester: QA agent acting as the "source AI" plus a human editor.
Studio: http://127.0.0.1:8001 (commit `3f7b706`, Video Studio v2 phase 4). Product: Ovoz at http://127.0.0.1:8000.
Project: `ovoz-62e5`. No Studio code was changed.

## Deliverables (this folder)

| Path | What |
|---|---|
| `final/ovoz-{uz,ru,en}-{9x16,16x9}.mp4` (+ `.srt`, `.vtt`, `.png` cover, `.json` metadata) | **Final videos**: Aurora template, music at −14 dB |
| `final-clean-v1/` | First batch from "Make video": Clean template (the template the brief asked for), music at −20 dB |
| `templates/{aurora,bold,clean}-uz-9x16.mp4` | Template comparison: the same project, uz 9:16 |
| `analysis/contact-*.png` | Contact sheet per video (one frame every 1.5 s) |
| `analysis/analysis-*.json` | Duration, loudness, speech segments vs `.srt` cues for each video |
| `analysis/frames-*/` | The extracted frames |
| `brief.json` | The brief I wrote as the source AI (imported as-is) |
| `brief-final-from-studio.json` | The brief after editing in the Studio |
| `brief-assets/` | The files imported with the brief: `web-models.png`, `phone-emotions.png`, `ovoz-type-play.mp4`, `logo.png` (a placeholder mark I made, because Ovoz has no logo) |
| `screenshots/` | All real Ovoz captures: `web1440-*`, `web1280-*` (desktop), `m390-*` (390×844 @3x = 1170×2532), `ovoz-type-play.mp4` (frame-by-frame phone recording, 6.7 s) |
| `ui/` | Studio UI screenshots referenced below |
| `import-response.json` | The API's response to the import |

## Brief decisions (as the source AI)

- **8 scenes, uz main + ru + en, 9:16 + 16:9.**
  - hero hook with an AI b-roll background
  - chips with the 4 languages
  - phone screen recording (type text, tap play)
  - stat "9 voice models"
  - browser screen with focus on the NC licence badges
  - phone screen with focus on Navoiy's 10 emotion chips
  - list "your own voice / consent / stays on your PC"
  - CTA
- **Voices: Ovoz's own models.** uz = `navoiy/madina` with the `xursand` style (the product promotes itself). ru = Edge Svetlana, en = Edge Ava.
  - In the ru `say` I wrote the name as "Овоз" so the Russian voice pronounces it correctly.
- **Music:** `music.generate` (ACE-Step), "uplifting modern tech, marimba plucks, warm pads", 100 bpm.
- **B-roll:** `comfy:wan22-14b-lightx2v` (same Wan 2.2 A14B + LightX2V as `wan22-a14b`, measured about 2× faster in ComfyUI), final quality.
- **Template: I picked `clean`.** My reasoning: Ovoz's UI is light, minimal and teal, so the screenshots would sit naturally in a light template. **This was the wrong call** (see the template verdict below).
- **Facts checked against `README.md` and `/api/models`:**
  - The README lists 10 models, but Sayro is unavailable, so 9 are usable. The "9" stat matches the owner's statement and what's available; please confirm this is the claim you want.
  - Navoiy's 10 emotions, the NC badges and adding voices with consent are all real. Ovoz has no public URL, so the CTA has no `url`.

## What worked well

- **Import.** The multipart API call took 6 s, with 0 warnings and 0 missing translations.
  - The MP4 recording was converted to VP9 WebM and the brief was updated.
  - Cyrillic, Arabic and Uzbek survived intact.
  - My `GPU’ingizda` (U+2019) was shown correctly as `GPUʼingizda` (U+02BC tutuq belgisi). All oʻ/gʻ use U+02BB, on screen and in the `.srt`.
- **Editor.**
  - Side-by-side translations.
  - Drag-a-rectangle focus picker, which saved immediately (`ui/12-focus-dragged.png`).
  - Per-scene transition picker with an animated preview.
  - Autosave, plus version history with readable summaries.
  - The scene form pre-fills the b-roll prompt and provider from the brief (`ui/03-scene-background-form.png`).
  - Logo upload and pick took 2 clicks (`ui/07-logo-set.png`).
  - The Uzbek UI is fully translated; at 420 px there is no horizontal scroll (`ui/17-uz-interface.png`, `ui/18-uz-420px.png`).
  - No console errors in any session.
- **Make video, once the voice-over succeeded.**
  - Retry resumes at the failed stage and keeps cached lines (15 left to generate instead of 22).
  - The Ovoz unload took 3 s (2.9 → 0.0 GB).
  - The Studio started ComfyUI by itself, ran the clip, then freed ComfyUI's GPU memory.
  - The monitor page shows VRAM moving between owners clearly (`ui/23-monitor-ai-stage.png`).
- **Caption timing is accurate.**
  - 24/24 lines aligned.
  - Every `.srt` cue overlaps real speech, measured with ffmpeg `silencedetect` on the voice stems.
  - I ran faster-whisper myself, without the script prompt, on the hardest uz line. It placed "Oʻzbek" at 6.32–6.98 s and "rus" at 7.30–7.64 s; the Studio's captions say 5.70–6.98 s and 7.30–7.66 s.
  - Chip pop-ins land on the spoken words.
- **Loudness.** Every MP4 is −14.0 ±0.1 LUFS, −1.5 dBTP, LRA 3.6–3.8. Ducking works: the music is 10.5–13 dB lower under speech than in the 3 s pause.
- **Aurora renders look like a real product ad** (`analysis/contact-ovoz-uz-9x16.png`, `contact-ovoz-uz-16x9.png`).
  - The stat ring and chips with check badges are good.
  - The Arabic chip is fine.
  - Russian Cyrillic is crisp.
  - The tightened focus makes the "NC · notijorat" badges legible even in 9:16.

## Bugs

| # | Severity | Bug | Steps to reproduce | Expected | Actual | Screenshot |
|---|---|---|---|---|---|---|
| 1 | **High** | One failed TTS line aborts the whole voice job and Make video, with no retry and a misleading Uzbek-only error | ru voice `edge/ru-RU-SvetlanaNeural`, `say` = "Говорит на узбекском, русском, английском и арабском." → Generate voice, or Make video | Retry the line 2–3×, finish every other line, then list the failed lines with "edit text / try another voice" | The job stops at the first failure: `Ovoz error 422: Ovoz yaratib bo'lmadi. Matnni tekshiring.` ("check the text"). The remaining ru/en lines are not attempted. Retry fails again within 3 s on the same line. | `ui/09-voice-done.png`, `ui/19-make-voice-failed.png` |
| 2 | Medium | The editor doesn't refresh voice state after a voice job ends in error | Same as #1, from the "Generate voice (3)" button | Scenes whose lines were generated show ✓ / aligned | All scenes stay "estimated", "Voice: 0/8 scenes", although all 8 uz lines were generated. A page reload shows "aligned 8/8". | `ui/09-voice-done.png` vs `ui/10-after-reload.png` |
| 3 | Medium | Make video elapsed time and ETA are wrong | Make video fails, you Retry, then watch the AI stage | Elapsed for this run; ETA covering music + b-roll + render | "Running · 5 min 30 s · ~7 s left". The elapsed time counts from the first failed attempt. The ETA covers only the music job, though the b-roll still took 6 min and the render 4 min. | `ui/22-make-ai-stage.png` |
| 4 | Medium | Clean template with a b-roll background: text over it is unreadable | `template: clean`, hero scene with an AI clip (`dim: 0.5`) | Text legible over the clip, as in Aurora | The clip gets a milky light wash. The teal kicker "BEPUL · 4 TIL" disappears and the grey sub is hard to read. All 6 covers (end of scene 1) carry the problem. | `analysis/contact-uz-9x16-20261115-185707.png` (first 4 frames), `final-clean-v1/*.png` |
| 5 | Low | In 9:16 phone screen scenes the callout covers the bottom of the phone screen | `screen` + `device: phone` + `callout` with no focus, or a focus in the lower half | The callout avoids the focus area, or can be positioned | "Bir bosishda audio" sits over exactly the area where the play button and audio player appear in the recording | `analysis/frames-ovoz-uz-9x16/f009.jpg`–`f012.jpg` |
| 6 | Low | Captions show the pronunciation spelling | ru `say` = "Попробуйте Овоз…" (Cyrillic so the voice says it right) | On-screen captions say "Ovoz" | Captions and `.srt` say "Овоз" while headlines say "Ovoz". There is no caption-text vs spoken-text field. | `final/ovoz-ru-9x16.srt`, `analysis/contact-ovoz-ru-9x16.png` |
| 7 | Low | `.srt` lines over the documented 42 characters; a line starts with a dash | ru `models` cue / uz `emotions` cue | ≤ 42 characters; a dash stays at the end of the previous line | "бесплатные модели прямо на вашем компьютере." = 44 characters. The uz cue 8 second line starts with "— xursand…". | `final/ovoz-ru-9x16.srt` cue 5, `final/ovoz-uz-9x16.srt` cue 8 |
| 8 | Low | History hides template names | Switch template Clean → Aurora → Bold in the picker | "template: clean → aurora" | "edit template" and "template: — → bold" (Aurora is stored as "no template", so it shows "—") | `GET …/history` versions 00008–00010 |
| 9 | Low | Russian line break leaves a one-letter preposition at the end of a line | ru hook headline "Превратите текст в голос", 16:9 | "в" keeps to the next word (non-breaking space after short prepositions) | "Превратите текст в / голос" (headline and cover) | `final/ovoz-ru-16x9.png` |
| 10 | Cosmetic | The project title field is truncated in the header at 1440 px | Open the editor at 1440×900 | Full title or tooltip | "Ovoz — bepul TTS pron" / "Ovoz — bepul T" | `ui/01-editor-open.png`, `ui/29-renders-list.png` |

Root cause of #1, for the record: Microsoft's Edge service itself returns `NoAudioReceived` for some Russian sentences. I reproduced it with `edge_tts` directly, bypassing both Ovoz and the Studio.
- **Consistent failures:**
  - "Говорит на узбекском, русском, английском и арабском." failed 4/4.
  - "Лицензия каждой модели всегда на виду." failed 3/4.
  - "Девять моделей: голоса Microsoft и бесплатные локальные модели." failed 3/4.
- **Workarounds that worked:**
  - Small rephrasings worked 4/4: "Говорит на четырёх языках: …", "У каждой модели видна лицензия.", "…прямо на вашем компьютере.".
  - The Dmitry voice read the failing sentence 3/3.

So the trigger is external, but the Studio's handling is what turns it into a dead end. Ovoz has the same messaging problem: its 422 says "check the text" for what is really a service hiccup.

## UX problems and friction for a non-technical user

1. **Voice failures are a dead end** (#1). I had to bisect sentences against Edge from a terminal to learn which ones Microsoft rejects. A non-technical owner would see "Matnni tekshiring" in an English UI and retry forever.
2. **No length guard.** The imported brief estimated 50.2 s, over the prompt's own 15–45 s and my 25–40 s target. Neither the import warnings nor the follow-up mentions it. I trimmed 6 lines by hand. The Uzbek cut is still 41.8 s versus 35.4 s for English: Navoiy speaks about 15 % slower than Edge. A per-language "fit to N seconds" (rate) hint would help.
3. **Browser screenshots in 9:16 are tiny.** The brief's focus (w 0.46) gave almost no zoom, and the page was unreadable in the preview. I had to drag a much smaller focus (w 0.21) to make the badges legible. The follow-up could warn about "landscape screenshot in a browser frame in a vertical video" and suggest a phone capture or a tighter focus.
4. **Recording scenes add dead air.** The 6.7 s recording forces the scene to 7.2 s, leaving 2.9–4.1 s with no voice (en is worst). Nothing tells the editor this.
5. **The template can't be judged before the expensive step.** Until "Make video" generates the b-roll, the preview shows the hook on the plain brand gradient. So the Clean-plus-b-roll contrast problem (#4) only appeared after a 6 min AI stage and a 4 min render.
6. **The follow-up's suggestions are generic.** It asks to turn the deliberate chips and list scenes into screen scenes, and repeats this after editing. It never mentions length, dead air or tiny browser shots, which were the real problems here. It did correctly ask for a logo.
7. **Unclear voice button.** After the uz lines existed, the voice button read "Regenerate all (3)" (`ui/19-make-voice-failed.png`). It's unclear whether this regenerates every line or only missing ones, and that "(3)" means languages.
8. **Music.**
   - **Level:** the default −20 dB puts the music 20–23 dB under the voice. That's barely audible on a phone; I raised it to −14 dB, which gives 16–17 dB.
   - **Generated track:** it passed the Studio's checks, but 73 % of its energy is below 250 Hz and 0.5 % is above 2 kHz. The prompt asked for marimba plucks and claps. On phone speakers, which cut below about 200 Hz, it will be close to inaudible. Nobody listened to it; please do.
9. **Seed mismatch.** The scene form shows a random seed (52109), but Make video generated the clip with seed 42. The form's seed seems to apply only to "Queue on GPU".

## Quality issues in the videos

- **Timing:**
  - Captions are accurate (see above).
  - The uz hook→chips line: Navoiy makes a short sound at 5.76 s, then pauses 0.6 s inside the first word "Oʻzbek". This is a possible prosody glitch of the `xursand` style; it needs a human listen.
  - There are 3–4 s voice gaps in `type-and-play` in every language.
- **B-roll** (ComfyUI Wan 2.2 14B, final, 720×1280, seed 42):
  - It's sharp, but off-prompt: "sound-wave ribbons on a dark green background" came out as a teal DNA-like double helix with an oscilloscope line over busy orange bokeh.
  - There is very little motion over 5 s.
  - In 16:9 the portrait clip is cropped hard: the helix fills the frame and runs behind the headline.
  - It looks acceptable in Aurora (darkened) and poor in Clean (washed out).
- **Layout:**
  - In 16:9, phone screen scenes leave about 60 % of the frame empty (a small phone on the right, a short headline on the left).
  - In Aurora 9:16, the list item "Yozuvlar kompyuteringizda" wraps to 2 lines.
  - The callout covers the player (#5).
  - During some cross-fades the outgoing and incoming headlines overlap: "Oʻz hissiyot", from "Oʻnta hissiyot" + "Oʻz ovozingiz".
- **Typography:** in the hook the Aurora sub is thin grey text over bokeh and only just readable. Also #6, #7 and #9.
- **Audio:**
  - Final mix: −14.0 LUFS, −1.5 dBTP.
  - Voice ≈ −15 dBFS RMS; music −31 dBFS under speech and −19.6 dBFS in the long pause.
  - The music is bass-heavy (see UX 8).
- **Covers:**
  - Default = end of the hook.
  - Aurora: fine (`final/*.png`).
  - Clean: weak (#4).
- **Not used:** Arabic was not one of the video languages. In the Arabic chip, RTL text renders correctly.

## Template verdict

| Template | Render (uz 9:16, 41.8 s) | Verdict |
|---|---|---|
| **Aurora** | 57 s | **Most professional for Ovoz.** The darkened b-roll keeps the hook readable, and light UI screenshots stand out on the dark gradient. The stat ring, chips and list look polished, and the brand teal + amber read as one system. |
| Bold | 38 s | Strong and readable, good for TikTok/Reels: sticker kicker, punchy uppercase, word-box captions. But the diagonal bands and repeated "OVOZ" watermark are loud for a tool aimed at creators and businesses, and uppercase Uzbek ("OʻZ OVOZINGIZ") gets heavy. A good second variant for paid social. |
| Clean | 35–41 s | Elegant on plain scenes, but it fails with a b-roll (#4). The light Ovoz screenshots also blend into the light background, with little separation. |

Final deliverables use Aurora. Bold is worth keeping as an A/B variant for Reels.

## Time per step (wall clock)

| Step | Time |
|---|---|
| Read docs (BRIEF, README, TEMPLATES, comfy README, Ovoz README) | ~3 min |
| Ovoz screenshots: 3 viewports × 7 states, Playwright | 22 s capture per 2 viewports; ~6 min including review and redoing the recording (Playwright's recorder writes CSS-pixel frames into a corner, so I rebuilt the recording from @3x screenshots) |
| Write the brief | ~3 min |
| Import (multipart API) | 6 s |
| Editor review, follow-up, logo | ~3 min |
| First "Generate voice" from the UI | 55 s, then failed (#1) |
| Diagnose the Edge failures, trim lines, fix focus, change a transition | ~7 min |
| **Make video (final)**, attempts 1 and 2 | 20 s, then failed; Retry 3 s, then failed |
| Attempt 3, after rephrasing 3 ru lines in the UI | **11 min 10 s**, broken down below |
| ① Voice | 37 s: 15 lines in 21 s + alignment ~15 s, 24/24 aligned |
| ② Free GPU | 3 s (Ovoz 2.9 → 0.0 GB) |
| ③ AI | 6 min 38 s: music 34 s (ACE-Step); b-roll ~6 min = workers release 24 s + ComfyUI start 21 s + model load 78 s + 4 steps × ~72 s + VAE 15 s + ComfyUI /free 3 s |
| ④ Render (Clean, 6 MP4s) | 3 min 52 s (35–41 s per output) |
| Make video, wall clock from first click | 15 min 35 s |
| Template comparison renders (uz 9:16) | Aurora 57 s, Bold 38 s |
| Final Aurora render, 6 MP4s, from the UI | 5 min 8 s (42–57 s per output; Aurora is about 35 % slower than Clean) |
| Frame extraction, contact sheets, loudness and SRT analysis | ~1 min of compute per batch |
| Total session | ~51 min |

## Prioritized improvement suggestions

1. **Make the voice-over resilient (fixes #1, #2).**
   - Retry `NoAudioReceived`/422 two or three times with jitter.
   - Keep generating the other lines.
   - Finish with "2 lines failed" plus per-line actions: edit the text, try another voice (offer Dmitry/Andrew), or render that line silent.
   - Translate the error into the UI language, and say that it is Microsoft's service, not the text.
   - Refresh the scene list on every job end, including errors.
2. **Pipeline ETA and elapsed time.** Sum the estimates of the remaining stages (the AI jobs have estimates; render ≈ 1.2 × video length per output). Reset elapsed per attempt (#3).
3. **Contrast safety over b-roll in light templates.**
   - In Clean, darken rather than lighten the clip, or add a scrim behind the text block.
   - Make `validate.py` measure text contrast over a sample clip.
   - Show a placeholder clip in the preview before generation, so the template picker reveals the problem (#4, UX 5).
4. **Make the follow-up check what actually hurt the video:**
   - total length versus the target
   - lines that make a language > 10 % longer than the others
   - recordings that create dead air
   - landscape screenshots in a browser frame in a 9:16-main project, with a "focus wider than 0.3" hint
   - Stop suggesting that deliberate text layouts become screen scenes on every call.
5. **Music defaults.**
   - Volume −14 to −16 dB.
   - Add a "too dark" check on generated music (for example < 2 % energy above 2 kHz → regenerate with a new seed, or apply a gentle high-shelf).
   - Add a quick "phone speaker" preview (high-pass at 200 Hz).
6. **Callout placement** that avoids the focus rectangle, plus an up/down option (#5).
7. **A spoken-text override** (`say_tts` or a pronunciation dictionary per language) so captions keep the brand spelling (#6).
8. **Typography polish.**
   - Non-breaking space after one-letter Russian prepositions and conjunctions (в, к, с, и, а, о, у) (#9).
   - Enforce the 42-character SRT limit and keep dashes at line ends (#7).
   - Wider title field (#10).
9. **B-roll for 16:9.** When a project has both orientations, offer to generate a landscape variant (an extra job, about 5 min) instead of a hard crop. Show the seed actually used.
10. **History labels:** show "aurora" instead of "—" and name both templates (#8).
