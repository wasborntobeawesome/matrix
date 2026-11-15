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

**GPU modes** (`broll.py --offload auto|full|group|model`, `avatar.py` job `"gpu": "auto"|"full"|"offload"`):

- `full` runs each Wan stage with the GPU to itself:
  1. The UMT5 encoder encodes the prompts, then moves back to the CPU, and the CUDA cache is emptied.
  2. The whole transformer stays resident for every diffusion step.
  3. The VAE decodes.
- `auto` picks `full` only when it fits without spilling into shared memory.
- On the RTX 5070 Ti, with Ovoz unloaded (14.7 GB free):

| Resolution | `full` | `group` |
|---|---|---|
| draft, 480×832×81 | 3.89 s/step; 10.9 / 11.7 GB (alloc / reserved) | 3.99 s/step |
| final, 704×1280×121 | 32 s/step; 14.6 / 17.0 GB, **spills 2.2 GB** | **23 s/step**; 8.7 / 11.0 GB |

- At final resolution, `auto` therefore keeps group offload.
- Group offload with CUDA streams is within ~3 % of full residency. The big win is a free GPU: 23 s/step
  instead of 43 s/step when it shared VRAM with the TTS models and spilled.
- EchoMimic `full` keeps the transformer, VAE and CLIP image encoder resident (the T5 stays on the CPU, its
  embeddings are cached) instead of model-level CPU offload.

## Warm workers, more video generators, music (v2 phase 3)

### `worker.py`: one warm process per model family

The studio no longer starts `broll.py` / `portrait.py` / `avatar.py` for every job. It keeps one
`worker.py --family <f>` process per model family alive (`video/studio/workers.py`), so the weights stay in
system RAM between jobs and only move to the GPU while a job runs. The old scripts are unchanged for CLI use.

| Family | Models (RAM while warm) | Used for |
|---|---|---|
| `wan` | UMT5-XXL encoder 10.6 GB + Wan2.2 VAE (fp32 2.8 GB / bf16 1.3 GB) + Wan2.2-TI2V-5B 9.3 GB + FastWan 5B 9.3 GB | `wan22-5b`, `fastwan-5b`, portraits |
| `a14b-t2v` / `a14b-i2v` | UMT5 10.6 GB + two 14B experts with the Lightning LoRA fused (53.8 GB) → process ≈ 68 GB | `wan22-a14b` |
| `echomimic` | EchoMimicV3-Flash pipeline (T5 kept on the CPU, prompt embeddings cached; wav2vec2 cached) | presenter |
| `acestep` | ACE-Step v1.5 XL turbo 10.3 GB → process ≈ 19 GB | music |

```bash
.venv/Scripts/python worker.py --family wan --once task.json     # one job, then exit (what "warm off" does)
```

- **Protocol.** The worker listens on `127.0.0.1:<random port>` (announced as `@@READY {"port": …}`) and reads
  one JSON command per line: `run`, `cancel`, `status`, `release_gpu`, `drop`, `quit`. Output keeps the
  `@@PROGRESS` / `@@RESULT` lines (+ `@@ERROR`, `@@STATUS`). Not stdin: on Windows a thread blocked reading
  the stdin pipe deadlocked DLL loading in the main thread (scipy/OpenBLAS hung on import; found with
  py-spy). When the studio goes away the socket closes and the worker exits.
- **Cancel** stops at the next denoising step and keeps the weights (the studio kills the process only if it
  doesn't stop within 25 s).
- **GPU per job.** Big transformers and the UMT5 encoder use diffusers group offloading (block level, CUDA
  streams); the VAE moves to the GPU for the decode and back. After a job only the CUDA context (~0.3 GB)
  remains; `release_gpu` empties the cache. Weights are page-locked *in place* with `cudaHostRegister`
  (`STUDIO_OFFLOAD=registered`, the default) instead of pinned copies. Measured on Wan 5B (5 steps,
  480×832×81, identical output, PSNR ∞): registered 20.4 s · pinned copies 21.1 s · pageable + pinned on
  the fly (`lowmem`) 26.3 s · plain blocking copies (`sync`) 34.4 s.
- **bf16 copies of the fp32 checkpoints.** The Wan repos ship fp32 transformers (A14B: 2 × 57 GB).
  `from_pretrained(torch_dtype=bf16)` reads every fp32 shard first: one A14B expert peaked at ~85 GB and the
  second one ran the 128 GB machine out of memory (a watchdog killed it at 2.8 GB free). `convert_bf16.py`
  writes bf16 shards once, tensor by tensor (85–114 s per expert); the worker loads them when they exist
  (`.models/bf16/<repo>/<part>`, required for A14B):

```bash
.venv/Scripts/python convert_bf16.py .models/Wan2.2-T2V-A14B-Diffusers/transformer   .models/bf16/Wan2.2-T2V-A14B/transformer
.venv/Scripts/python convert_bf16.py .models/Wan2.2-T2V-A14B-Diffusers/transformer_2 .models/bf16/Wan2.2-T2V-A14B/transformer_2
.venv/Scripts/python convert_bf16.py .models/Wan2.2-TI2V-5B-Diffusers/transformer      .models/bf16/Wan2.2-TI2V-5B/transformer
.venv/Scripts/python convert_bf16.py .models/Wan2.2-I2V-A14B-Diffusers/transformer   .models/bf16/Wan2.2-I2V-A14B/transformer
.venv/Scripts/python convert_bf16.py .models/Wan2.2-I2V-A14B-Diffusers/transformer_2 .models/bf16/Wan2.2-I2V-A14B/transformer_2
```

### Video generators (providers)

Downloads (`huggingface_hub.snapshot_download`, into `.models/`), plus `pip install peft` (Apache-2.0, for
the LoRA):

```python
sd("FastVideo/FastWan2.2-TI2V-5B-FullAttn-Diffusers", local_dir=".models/FastWan2.2-TI2V-5B-FullAttn-Diffusers",
   ignore_patterns=["text_encoder/*", "vae/*", "assets/*", "examples/*"])            # 10 GB (UMT5 + VAE = the 5B's)
sd("Wan-AI/Wan2.2-T2V-A14B-Diffusers", local_dir=".models/Wan2.2-T2V-A14B-Diffusers",
   ignore_patterns=["text_encoder/*", "assets/*", "examples/*"])                       # 115 GB (fp32), then convert_bf16.py
sd("Wan-AI/Wan2.2-I2V-A14B-Diffusers", local_dir=".models/Wan2.2-I2V-A14B-Diffusers",
   ignore_patterns=["text_encoder/*", "assets/*", "examples/*"])                       # 115 GB (fp32), then convert_bf16.py
sd("lightx2v/Wan2.2-Lightning", local_dir=".models/Wan2.2-Lightning", allow_patterns=[
   "Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V1.1/*.safetensors", "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/*.safetensors"])
sd("ACE-Step/acestep-v15-xl-turbo-diffusers", local_dir=".models/acestep-v15-xl-turbo-diffusers")   # 11 GB
```

The UMT5 shards are byte-identical in all Wan2.2 repos (same LFS sha256), so only the 5B's copy is used.

- **`fastwan-5b`** — FastWan2.2-TI2V-5B (FastVideo, DMD distillation of Wan2.2-TI2V-5B). 3 steps, no CFG.
  `worker.py` reproduces FastVideo's `DmdDenoisingStage` as a small scheduler (predict x₀ = xₜ − σₜ·v, re-noise
  to the next of the timesteps 1000/757/522, σ = t/1000) inside diffusers' `WanPipeline` /
  `WanImageToVideoPipeline`. Its VAE runs in bf16 and un-tiled when VRAM allows: the decode of
  480×832×81 takes 10.5 s instead of ~32 s (fp32 tiled) — the decode was 3/4 of the job.
- **`wan22-a14b`** — Wan2.2-T2V-A14B (and I2V-A14B for image-to-video) with the Wan2.2-Lightning 4-step
  LoRAs fused at load: 4 steps (2 per expert, boundary 0.875), guidance 1, Euler flow-matching with shift 5,
  16 fps. Draft 480×832×81, final 720×1280×81.
- **`wan22-5b`** — unchanged settings (20/30 steps, CFG 5, fp32 VAE); only the transformer now loads from
  the bf16 copy.
- **Image-to-video:** all three are text+image models. The start image (a project image asset or a library
  portrait) is cover-cropped to the clip size; for the 5B models it is the first latent frame (`expand_timesteps`).

**Comparison** — same 3 prompts, seed 42, draft presets, 9:16, Ovoz unloaded, RTX 5070 Ti
(`video/studio/preview/v2-phase3-providers-compare.png`, first/middle/last frame of each clip):

| Provider | Output | Job time cold → warm | Steps (s/step) | VAE decode | Torch peak VRAM | GPU total in use | Worker RAM |
|---|---|---|---|---|---|---|---|
| `wan22-5b` | 480×832, 81 f, 24 fps | 129 s → 115 s | 20 × 4.0 s | 32 s (fp32 tiled) | 9.1 GB | 9.5 GB | 37–64 GB* |
| `fastwan-5b` | 480×832, 81 f, 24 fps | 50 s → **20 s** | 3 × 2.0 s | 10.5 s (bf16) | 10.5 GB | 12.0 GB | 36 GB* |
| `wan22-a14b` | 480×832, 81 f, 16 fps | 257 s → 165 s | 4 × 35 s | 22 s | 10.7 GB | 13.1 GB | 68 GB |

\* the `wan` worker holds whatever of 5B / FastWan / both VAEs it has loaded; 37 GB with one 5B model loaded
from the old fp32 files, 64 GB with both models and both VAEs.

Quality, judged from the frames:
- **abstract** (glowing purple/teal sound-wave ribbons): wan22-5b produced an almost black frame with one
  tiny squiggle — unusable at draft settings. FastWan: bright purple/teal light strands, good. A14B: dense,
  detailed layered waveform ribbons with particles in purple, teal and orange — clearly the best.
- **product** (phone on a desk with a purple app screen): wan22-5b very dark, the phone a sliver. FastWan: a
  believable phone with a purple app grid on a wooden desk, almost static. A14B: a photoreal modern phone
  with a lamp, notebook and laptop, slow dolly — best, but the app UI is invented (as always).
- **people** (woman with a laptop in a café): all three plausible. wan22-5b: warm backlit shot, slight
  smearing. FastWan: natural, sharp, little motion. A14B: the most natural (cup, people walking behind,
  smile), best skin and hands.
- Motion: all three are calm; A14B moves most naturally. FastWan clips are nearly still, which suits dimmed
  backgrounds.
- **Image-to-video** on the presenter portrait, same prompt, seed 3 (`preview/v2-phase3-i2v-portrait.png`):
  FastWan (cold 78 s) starts on the portrait and animates a smile, but within ~10 frames the face gets
  rounder, the lighting brighter and the bokeh bigger — fine for a draft, not for a recognisable person.
  Wan2.2-I2V-A14B + Lightning (cold 264 s: load 79 s, 4 steps 156 s, decode 22 s; 12.0 GB torch peak; worker
  68 GB RAM) keeps the identity, framing and background and adds a natural smile and nod — usable.

### Other models checked (not integrated)

Licences read from the model cards / LICENSE files (or the Hugging Face licence tag where stated) in
November 2026:

| Model | Licence | Why not |
|---|---|---|
| Kandinsky 6.0 Lite / Pro (Oct 2026, 3B / 29B, T2V+I2V+audio) | MIT on the card | Its `vae/` file is **byte-identical to the HunyuanVideo VAE** (LFS sha256 `7c68a629…`, also in Kandinsky 5.0). That VAE is under the Tencent Hunyuan Community Licence: not licensed in the EU, UK and South Korea, and §5c forbids using or displaying *Output* outside that territory — a problem for promo videos shown worldwide. |
| HunyuanVideo 1.5 (8.3B) | Tencent Hunyuan Community Licence | same territory clause; >100M MAU needs a separate licence |
| LTX-2 / 2.3 / 2.5 (Lightricks) | LTX-2 Community Licence (HF tag "other") | free only below $10M annual revenue (revenue-capped) |
| CogVideoX 1.5 5B | CogVideoX licence (HF tag "other") | custom licence; older; not needed |
| Mochi 1 (10B, 2024) | Apache-2.0 | older and weaker than Wan2.2 A14B, heavy for 16 GB |
| Helios-Base / -Distilled (14B, Wan2.1-based, 2026) | Apache-2.0 (HF tag) | promising "real-time" candidate, but 138 GB download; not tested in this phase |
| SANA-Video 2B, Motif-Video-2B | Apache-2.0 (HF tag) | small/fast candidates, not tested; check their text encoders' terms (Gemma-based encoders come with the Gemma Terms of Use) |
| Wan 2.5 / 2.6 / 3.0 | API only | no weights |

### Music: ACE-Step v1.5 XL turbo

- `ACE-Step/acestep-v15-xl-turbo-diffusers` with diffusers' `AceStepPipeline` (5B DiT, guidance-distilled,
  8 steps; Qwen3-Embedding-0.6B text encoder, Apache-2.0; its own 48 kHz Oobleck VAE — 337 MB, a different
  file and different strides than Stable Audio Open's). Licence: MIT (LICENSE in ace-step/ACE-Step-1.5); the
  model card says it was trained on licensed, royalty-free/public-domain and synthetic (MIDI) music and that
  generated music can be used commercially. Their README asks users to check outputs for accidental
  similarity to existing songs and to disclose AI involvement.
- Instrumental only (`lyrics="[Instrumental]"`). The studio asks for 6 s more than needed, trims leading
  silence and the faded/silent tail, fades out the last 1.5 s, encodes OGG and runs `music.analyze()`.
- Rejected: MusicGen / AudioGen (CC-BY-NC weights), Stable Audio Open / Stable Audio 3 (Stability AI
  licence "other", revenue-capped community licence).

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
