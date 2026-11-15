# Option: local generative-AI video (`video/ai/`)

Two experiments, everything local and free, on an RTX 5070 Ti 16 GB (Blackwell, sm_120).
About 6–10 GB of that VRAM was already taken by the user's TTS server and other agents, so
everything here runs with CPU offload.

| Output | What it is |
|---|---|
| `video/out/ai-broll.mp4` | 21.4 s, 1080x1920, 30 fps. Three Wan2.2 b-roll clips with ffmpeg drawtext scene text and `voiceover.wav` |
| `video/out/ai-avatar.mp4` | Talking-head presenter (fictional, generated) lip-synced to the voice-over with EchoMimicV3-Flash |
| `video/ai/clips/` | Raw clips: `waves.mp4`, `phone.mp4`, `mic.mp4` (704x1280, 121 frames @ 24 fps), `avatar-<scene>.mp4`, plus `stats.json` and `avatar_stats.json` with timing and VRAM |
| `video/ai/preview/` | Extracted frames that were checked by eye |

## Setup

```bash
cd video/ai
py -3.12 -m venv .venv
.venv/Scripts/python -m pip install torch==2.8.0 torchvision torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
.venv/Scripts/python -m pip install diffusers transformers accelerate ftfy imageio imageio-ffmpeg sentencepiece protobuf "huggingface_hub[hf_xet]" fonttools
# talking head only:
.venv/Scripts/python -m pip install omegaconf librosa "moviepy==2.2.1" pyloudnorm einops opencv-python-headless scipy timm
```

Versions that were tested: torch 2.8.0+cu128, diffusers 0.43.0, transformers 5.23.0, accelerate 1.15.0.
The `.venv` is about 8 GB. Pin `torchaudio==2.8.0`, because the cu128 index otherwise installs 2.11, which doesn't match torch 2.8.

Weights go in `video/ai/.models/` (gitignored):

```python
from huggingface_hub import snapshot_download as sd
sd("Wan-AI/Wan2.2-TI2V-5B-Diffusers", local_dir=".models/Wan2.2-TI2V-5B-Diffusers")              # 34.2 GB
# talking head (EchoMimicV3-Flash):
b = ".models/echomimic_v3/flash/"
sd("TencentGameMate/chinese-wav2vec2-base", local_dir=b+"chinese-wav2vec2-base", ignore_patterns=["*fairseq*"])  # 0.38 GB
sd("BadToBest/EchoMimicV3", local_dir=b+"_em3", allow_patterns=["echomimicv3-flash-pro/*"])                    # 3.7 GB
sd("alibaba-pai/Wan2.1-Fun-V1.1-1.3B-InP", local_dir=b+"Wan2.1-Fun-V1.1-1.3B-InP")                            # 19.8 GB
```
EchoMimicV3 code: `git clone https://github.com/antgroup/echomimic_v3 .models/echomimic_v3/repo`. It is used
unmodified; `avatar.py` imports its `src/` package. Its `infer_flash.py` is not used: it needs `decord`,
which has no Py3.12 Windows wheel, and it puts the whole pipeline on the GPU.

## Running

```bash
.venv/Scripts/python broll.py                         # 3 clips, defaults: 704x1280, 121 frames, 40 steps, seed 42
.venv/Scripts/python broll.py --steps 30 --only mic   # (the outputs here used --steps 30)
.venv/Scripts/python broll.py --prompt "..." --name foo --seed 7 --width 704 --height 1280 --frames 81
.venv/Scripts/python assemble.py                      # -> ../out/ai-broll.mp4  (--map hook=waves,cta=mic,...)
.venv/Scripts/python portrait.py --seeds 1 2 3        # presenter candidates -> build/presenter-<seed>.png
cp build/presenter-1.png build/presenter.png
.venv/Scripts/python avatar.py                                  # all scenes, 576x1024 -> ../out/ai-avatar.mp4 (~25 min)
.venv/Scripts/python avatar.py --scenes hook --width 448 --height 800   # one scene, faster
```

**Studio mode.** The Video Studio AI queue (`video/studio/aijobs.py`) runs these scripts as subprocesses.
These modes leave the default behaviour above unchanged. Each one prints `@@PROGRESS {json}` per diffusion
step and one `@@RESULT {json}` (timings, peak VRAM) at the end (`studio_io.py`):

```bash
.venv/Scripts/python broll.py --prompt "..." --seed 7 --steps 20 --width 480 --height 832 --frames 81 --out-file X.mp4 --progress
.venv/Scripts/python portrait.py --prompt "..." --seed 3 --out X.png --progress
.venv/Scripts/python avatar.py --job job.json   # {portrait, width, height, steps, teacache, seed, segments: [{wav, out, min_dur}]}
```

See the "AI layers" section of `video/studio/README.md` for presets and timings measured in the studio.

## A. B-roll with Wan2.2-TI2V-5B

- Pipeline: `WanPipeline` in diffusers, bf16 transformer, fp32 VAE with tiling.
  **Block-level group offloading** with CUDA streams on the transformer and leaf-level offloading on the UMT5 text encoder, so
  only a few blocks are on the GPU at a time.
- Settings: 704x1280 portrait, 121 frames (5.04 s @ 24 fps), 30 steps, CFG 5.0, seed 42.

| Clip | Prompt (short) | Gen time | Peak VRAM (torch alloc / reserved) |
|---|---|---|---|
| waves | glowing purple/teal sound-wave ribbons on dark navy | 1402 s (23.4 min) | 8.65 / 10.98 GB |
| phone | smartphone on dark desk, glowing waveform on screen | 1387 s (23.1 min) | 8.65 / 10.98 GB |
| mic | studio mic + pop filter, purple/teal bokeh, slow orbit | 1386 s (23.1 min) | 8.65 / 10.98 GB |

About 43 s per denoising step (CFG doubles the passes), plus about 1 min of VAE decode. Pipeline load takes about 7–15 s.
The peak is from the VAE decode. Combined with what other processes held, total GPU use reached about 15.5 / 16.3 GB,
so Windows WDDM probably spilled some memory into shared system RAM. That is a likely reason for the slow speed.
Expect it to be noticeably faster on an otherwise idle GPU. A 25-frame, 8-step smoke test took 53 s.

Assembly (`assemble.py`, ffmpeg only, about 20 s):
- Each clip is Lanczos-scaled to width 1080, center-cropped to 1080x1920, converted from 24 to 30 fps (frame duplication),
  and slowed by up to 1.07x to fill each scene.
- `waves` is reused reversed for the emotions scene, and `mic` reversed for the CTA.
- A gradient shade is applied and gamma lifted by 1.25.
- Text uses ffmpeg `drawtext` with harfbuzz shaping, so the Arabic chip "العربية" renders correctly. Fonts are Inter 800 and 500 (static instances
  cut from the variable `Inter.ttf` with fontTools) and Noto Sans Arabic.
- Scenes are joined with 0.4 s crossfades placed exactly at the timeline scene starts, so the voice-over stays in sync.
- Emoji in the emotions list are stripped, because drawtext has no colour-emoji support.

**Quality (judged from extracted frames, see `preview/clip_*.png` and `preview/broll_*.jpg`):**
- **mic**: the best. Photoreal, cinematic, correct mic, pop filter and shock-mount, nice bokeh. Motion is very subtle (slow
  push), with no visible artifacts. Usable as real promo b-roll.
- **waves**: clean, smooth, glossy purple light ribbons on navy with no artifacts. It ignored "teal" (all
  purple/blue). Usable as a background, but on its own it looks generic.
- **phone**: weak. The phone itself looks fine, but the whole scene is very dark and the "waveform" is just a moving purple blob
  on the screen, not a waveform. It only passes as a dim background under text. It needs prompt/seed iteration, for example
  "brightly lit desk" or "screen shows UI". Each iteration costs about 23 min.
- Upscaling from 704 to 1080 is soft but acceptable behind big text. Wan adds no text or logos (they are in the negative prompt).
- Overall: it looks like a stock-footage promo. The AI part adds atmosphere but carries no product information (no
  real app UI, no readable text). All the information comes from the overlays.

## B. Talking-head presenter

**Portrait:** generated with the same Wan2.2-TI2V-5B in text-to-image mode (`num_frames=1`, 704x1280, 40 steps,
about 22 s per image), so no extra model or license is needed. It shows a fictional woman presenter on a navy/purple bokeh background
(`build/presenter-1.png`, 3 candidates in `preview/portraits.png`). The result is photoreal and clean.
It reads as East Asian rather than Central Asian (the prompt asked for Central Asian).

**Animation model: EchoMimicV3-Flash** (Ant Group, 1.3B, built on Wan2.1-Fun-1.3B-InP). It is image + audio to video,
needs no face detector or mask, and all components are permissively licensed (see table). Candidates that were rejected:
- **SadTalker**: code is Apache-2.0, but its 3DMM pipeline uses the Basel Face Model (non-commercial) and needs Py3.8/torch 1.12.
- **Hallo / Hallo2 / Hallo3**: use InsightFace models (non-commercial).
- **LatentSync**: needs a driving video, and also uses InsightFace for detection.
- **MuseTalk**: needs a driving video and mmcv/mmpose (painful on Windows/Py3.12), and uses the SD VAE.
- **EchoMimic v1/v2**: viable fallback (Apache-2.0), but older SD1.5-based quality.

Each scene's wav is padded to the scene length and animated separately from the same portrait, at 25 fps, 8 steps, TeaCache 0.1.
The clips are then trimmed, concatenated and upscaled to 1080x1920. `voiceover.wav` is muxed in, with a headline lower-third per scene.
Running per scene keeps sequences at ≤ 125 frames. The trade-off is a small pose reset at every scene cut.

Required fixes:
1. Pipeline `enable_model_cpu_offload()` instead of the upstream `.to("cuda")`.
2. With transformers 5.x, the repo's Wav2Vec2 wrapper returns `hidden_states=None`, so per-layer outputs are captured with forward hooks.

Required fix #3 (the big one): the repo's `WanT5EncoderModel.from_pretrained` ignores `torch_dtype`.
The low-mem path fails against diffusers 0.43, so the 5.7B-param T5 loads as **fp32 (22.7 GB)**. With CPU offload it was
moved to the GPU on every call and spilled into WDDM shared memory: peak "allocated" was 26.5 GB on a 16 GB card,
the first scene took 31 min, and 9 of its 18 min were pure overhead. `avatar.py` now encodes the two fixed prompts **once on the
CPU** and caches the embeddings. The T5 never touches the GPU, and generation became **~8x faster**.

| Run | Res | Scene (frames) | Gen time | Peak VRAM alloc / reserved |
|---|---|---|---|---|
| before fix | 576x1024 | hook (113) | 1846 s | 26.6 / 28.3 GB (spilling) |
| before fix | 448x800 | hook (125) | 1111 s | 24.6 / 25.7 GB (spilling) |
| after fix | 448x800 | languages (113) / models (121) / emotions (125) / cta (61) | 128 / 143 / 149 / 49 s | 6.1–6.3 GB / 7.6 GB |
| **final, after fix** | **576x1024** | hook 125 / languages 113 / models 121 / emotions 125 / cta 61 | **363 / 303 / 342 / 362 / 108 s** | **8.0 / 10.6 GB** |

The final `ai-avatar.mp4` (21.4 s) took **~25 min of GPU time** for 21 s of video, about 70 s per second of video,
plus about 1 min of model load. Steps run at 40–60 s each at 576x1024 (3 passes per step: text CFG + audio CFG).

**Quality (frames in `preview/avatar_*.png|jpg`, `preview/portraits.png`):**
- **Identity**: stable across all 5 scenes. No warping, no teeth or eye artifacts were seen. It blinks naturally.
  Head motion is small and natural. It is the most "real" looking output of this experiment.
- **Lip sync**: audio-driven and clearly responsive. In the CTA scene the mouth makes "o" and open shapes while
  "Hoziroq sinab ko'ring" is spoken, then closes into a smile for the trailing 1 s silence
  (`preview/avatar_cta_mouth.png`). I could only check this from frames, not by watching with sound, so precise phoneme
  accuracy for Uzbek is unverified. The audio encoder is Chinese-trained, so expect "good enough", not dubbing-grade.
- **Scene cuts**: each scene restarts from the same portrait, so cuts are nearly seamless
  (`preview/avatar_scenecut.png`), with only a slight brightness or pose shift.
- **Weak points**:
  - 576x1024 upscaled to 1080x1920 is visibly soft.
  - It's only a static head-and-shoulders shot (no gestures or camera move).
  - The presenter looks East Asian although the prompt said Central Asian. Re-roll seeds or prompt harder.
  - A synthetic presenter needs an "AI-generated" disclosure on most platforms.

## Licenses (commercial use)

| Component | License | Commercial? | Notes |
|---|---|---|---|
| Wan2.2-TI2V-5B weights + code (Wan-AI) | Apache-2.0 | Yes | Model card: you are responsible for generated content and must not use it for illegal or harmful purposes |
| UMT5-XXL text encoder (in the Wan repo) | Apache-2.0 | Yes | |
| 🤗 diffusers / transformers / accelerate | Apache-2.0 | Yes | |
| PyTorch | BSD-3 | Yes | |
| EchoMimicV3 code + Flash weights (antgroup / BadToBest) | Apache-2.0 | Yes | |
| Wan2.1-Fun-V1.1-1.3B-InP (alibaba-pai) | Apache-2.0 | Yes | Includes the UMT5 encoder and the open-clip XLM-RoBERTa ViT-H/14 image encoder from Wan2.1 (shipped under Apache-2.0; the original OpenCLIP was trained on LAION, so there is a dataset-provenance caveat) |
| chinese-wav2vec2-base (TencentGameMate) | MIT | Yes | Trained on WenetSpeech, a Chinese corpus. Lip shapes for Uzbek are still plausible |
| moviepy (MIT), librosa (ISC), pyloudnorm (MIT), omegaconf (BSD), einops (MIT), opencv (Apache-2.0) | permissive | Yes | |
| ffmpeg build (gyan.dev, `--enable-gpl`) | GPL-3 | Yes, as a tool | Running it is fine; just don't distribute it inside a closed product |
| Inter / Noto Sans Arabic | OFL-1.1 | Yes | |

No InsightFace, Basel Face Model, SDXL-Turbo (non-commercial) or other NC components are used anywhere in this
folder. The presenter is synthetic and fictional, so there are no likeness rights. Many platforms
require disclosing an AI-generated person.

## Download sizes / disk

| Item | Size |
|---|---|
| Wan2.2-TI2V-5B-Diffusers (fp32 transformer shards 20 GB + UMT5 11.4 GB + VAE 2.8 GB) | 34.2 GB |
| EchoMimicV3-Flash total (flash 3.7 + Wan2.1-Fun-1.3B 19.8 + wav2vec 0.38) | 23.9 GB |
| Python venv (torch cu128 etc.) | ~8 GB |

Download speed was about 15–35 MB/s from Hugging Face, so roughly 25 min for Wan and 23 min for EchoMimic.

## Verdict / recommendations

- **B-roll (A): usable as a supporting layer, not as the main promo engine.**
  - On this shared GPU it takes about 23 min per 5 s clip, and prompt adherence is hit-or-miss (1 of 3 clips was weak, and "teal" was ignored).
  - It can't render the app UI, text or brand, so all the information still comes from the overlays.
  - Best use: generate a small library of 5–10 on-brand loops once (mic, waves, abstract light), then reuse them as backgrounds under a
    motion-graphics renderer (Remotion/HTML option) instead of flat gradients.
  - Free up the GPU first: with no other VRAM users, the 8.6 GB peak fits without spilling and it should be much faster.
  - Further speed-ups to try: fewer frames (81), 720p→ 480p + upscale, or FP8 layerwise casting.
- **Avatar (B): the surprise winner of this folder.**
  - EchoMimicV3-Flash gives a believable, lip-synced presenter at ~70 s of GPU time per second of video in ~8–10 GB, and the whole stack
    is Apache/MIT.
  - Fixing the fp32-T5 bug is essential. Without it, it is 8x slower.
  - Good for "presenter explains the update" formats. Combine it with the motion-graphics renderer: put the avatar in a
    rounded card or the lower half, and the text/chips above.
  - Next steps:
    - Generate the presenter at a wider framing (chest-up, more headroom).
    - Run 768x768 or 576x1024 with `--steps 12`.
    - Add a light face-restore/upscale pass (Real-ESRGAN, BSD-3) for 1080p sharpness.
- **Not recommended:** SadTalker, Hallo, LatentSync or MuseTalk for commercial use, because of InsightFace/BFM non-commercial dependencies or the
  driving-video requirement. SDXL-Turbo is non-commercial too. Use Wan2.2 T2I (as here) or FLUX.1-schnell for portraits.
