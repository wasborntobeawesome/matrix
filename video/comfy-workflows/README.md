# ComfyUI workflows for Video Studio

Every folder here is one ComfyUI workflow that the Studio can run as a b-roll video generator. In the
Studio it is the provider **`comfy:<folder name>`**: in the AI page's *Generate new clip* form, in the
editor (scene background, project default) and in briefs (`background.provider`, `broll_provider`).

```
video/comfy-workflows/<id>/
    workflow_api.json   the graph in ComfyUI's "Export (API)" format
    manifest.json       name, licence, capabilities, input mapping, outputs, sizes, estimates
    preview.jpg         optional (the first clip the workflow makes becomes its preview)
```

Two samples come with the Studio (core nodes only):

| id | What | Models (Comfy-Org single-file repackage) | Licence |
|---|---|---|---|
| `wan22-5b-draft` | fast drafts, text **or image** → video. Wan 2.2 TI2V-5B, 20 steps, 24 fps | `wan2.2_ti2v_5B_fp16` (10.0 GB), `umt5_xxl_fp8_e4m3fn_scaled` (6.7 GB), `wan2.2_vae` (1.4 GB) | Apache-2.0 |
| `wan22-14b-lightx2v` | quality, text → video. Wan 2.2 T2V-A14B high + low noise experts in fp8, LightX2V 4-step LoRA, 16 fps | `wan2.2_t2v_{high,low}_noise_14B_fp8_scaled` (2 × 14.3 GB), `wan2.2_t2v_lightx2v_4steps_lora_v1.1_{high,low}_noise` (2 × 1.2 GB), umt5 (shared), `wan_2.1_vae` (0.25 GB) | Apache-2.0 |

Measured on the RTX 5070 Ti (16 GB) as Studio jobs: draft sample 68 s at 480×832×81 (295 s at 704×1280×121),
also 68 s image-to-video; quality sample 89 s at 480×832×81 (298 s at 720×1280×81); peak VRAM 11–13 GB
(ComfyUI's dynamic VRAM uses what is free). Details: `video/studio/README.md` → *ComfyUI provider*.

## Installing ComfyUI

```
py -3.12 video/comfyui/setup.py                  # ComfyUI (pinned tag) + venv (torch cu128) + both samples' models
py -3.12 video/comfyui/setup.py --models draft   # only what the draft sample needs (~18 GB)
py -3.12 video/comfyui/setup.py --check          # what is installed
py -3.12 video/comfyui/setup.py --verify         # re-hash the model files (SHA-256)
```

- ComfyUI is cloned from `github.com/comfyanonymous/ComfyUI` at the tag pinned in `setup.py`
  (`COMFY_TAG`), into `video/comfyui/ComfyUI`, with its own venv `video/comfyui/.venv` (Python 3.12,
  torch 2.8.0 + CUDA 12.8 — RTX 50xx / Blackwell needs CUDA ≥ 12.8). No ComfyUI-Manager, no custom nodes.
  Running the script again updates to the pinned tag and skips everything that is already there (8 s).
  ComfyUI's web-UI templates are installed as JSON only (`--templates full` adds ~670 MB of preview media
  from PyPI, which downloaded at ~100 kB/s here; `--templates none` skips them).
- Models go to `video/comfyui/models/<folder>/`, outside the checkout, and ComfyUI finds them through
  `video/comfyui/extra_model_paths.yaml`. Downloads resume and are checked against the SHA-256 that Hugging
  Face publishes. The two LightX2V LoRAs are byte-identical to the ones the Studio's own A14B worker uses
  (`video/ai/.models/Wan2.2-Lightning/…-Seko-V1.1/`), so they are hard-linked, not downloaded again.
  (The diffusers folders in `video/ai/.models` cannot be shared otherwise: ComfyUI loads single-file
  checkpoints.)
- The Studio starts and stops this ComfyUI itself (AI page → *ComfyUI*), on `127.0.0.1:8188`, with
  `--reserve-vram 1 --disable-partner-nodes --disable-all-custom-nodes --preview-method none` plus the
  output/input/temp/user folders under `video/comfyui/`. Or switch the mode to **external** and give the URL
  of a ComfyUI you run yourself (then the Studio never starts or stops it).

## Making a workflow for the Studio

1. Build and test it in the ComfyUI web UI (*Open ComfyUI* on the AI page). Start from a template
   (*Workflow → Browse Templates → Video*; e.g. "Wan 2.2 14B Text to Video" — turn its *Enable Lightning LoRA*
   switch on for 4-step speed) or from the samples (drag `workflow_api.json` into ComfyUI).
2. It must end in a node that **saves a video**: `SaveVideo` (best: MP4), `SaveWEBM`, `SaveAnimatedWEBP`,
   `SaveAnimatedPNG` or `VHS_VideoCombine` (custom). The Studio converts the result to H.264 and to the VP9
   WebM the renderer uses.
3. Put the prompt into a `CLIPTextEncode` that feeds the sampler's *positive* input, and use a plain
   number widget for seed, width/height/length (`EmptyHunyuanLatentVideo`, `Wan22ImageToVideoLatent`,
   `EmptyLatentImage`, …), fps (`CreateVideo`) and steps — not a "primitive" node or a converted input, so the
   value sits on the node the Studio writes into.
4. For image-to-video add a `LoadImage`. When the image input it feeds is optional (e.g.
   `Wan22ImageToVideoLatent.start_image`), the same workflow also does text-to-video: the Studio removes the
   `LoadImage` node when no image is given.
5. **Workflow → Export (API)** (in older frontends enable *Dev mode* in the settings first). This saves the
   `…_api.json` file; the normal *Save* format (with `nodes` and `links`) is rejected with a hint.
6. AI page → *ComfyUI workflows* → *Upload a workflow*: pick the JSON, give a name, the **licences of the
   models** and whether they allow commercial use, optionally a preview image. The Studio shows what it
   detected and what is missing before you save; after saving the mapping editor opens.

## Input mapping

`manifest.json → inputs` says which node input receives each Studio value:

```json
"inputs": {
  "prompt":   [{"node": "6",  "input": "text"}],
  "negative": [{"node": "7",  "input": "text"}],
  "seed":     [{"node": "57", "input": "noise_seed"}, {"node": "58", "input": "noise_seed"}],
  "width":    [{"node": "59", "input": "width"}],  "height": [{"node": "59", "input": "height"}],
  "length":   [{"node": "59", "input": "length"}], "fps":    [{"node": "60", "input": "fps"}],
  "steps":    [{"node": "3",  "input": "steps"}],  "image":  [{"node": "56", "input": "image"}]
},
"outputs": ["61"],
"presets": {"draft": {"width": 480, "height": 832, "length": 81, "fps": 16},
            "final": {"width": 720, "height": 1280, "length": 81, "fps": 16}}
```

- Each value is a list: one value can go to several nodes (both samplers of a two-stage Wan 2.2 14B graph).
- **Unmapped inputs keep the workflow's value.** The negative prompt is only replaced when a Studio user
  types one; the samples keep Wan's own (Chinese) default. Steps of a two-stage `KSamplerAdvanced` graph are
  not mapped (the 0-2 / 2-4 split is part of the graph).
- `presets` are portrait sizes for the Studio's *Draft* / *Final*; landscape (16:9 projects) swaps width and
  height. Wan needs sizes in multiples of 16 (TI2V-5B: 32) and `length` = 4n + 1 frames.
- **Auto-detection** (`comfyflows.detect`): the positive / negative prompt is found by following each
  sampler's (or guider's) `positive` / `negative` link back through pass-through nodes (e.g.
  `WanImageToVideo`, `ConditioningZeroOut` = no negative) to the text encoder; the seed from `KSampler.seed`,
  `KSamplerAdvanced.noise_seed`, `RandomNoise.noise_seed`; steps from a single `KSampler` / `BasicScheduler`;
  size and length from the empty-latent / image-to-video-latent node; fps from `CreateVideo`,
  `SaveAnimatedWEBP`, `SaveWEBM`, `VHS_VideoCombine.frame_rate`; the input image from the first `LoadImage`;
  outputs from the save nodes. Capabilities: t2v / i2v (+ t2v when the image is optional), image,
  upscale (`UpscaleModelLoader`), interpolate. Anything it could not find is listed as a note.
- **Validation** against the running ComfyUI's `/object_info`: node types that do not exist (= custom
  nodes that are not installed), model files that are not in the loader's list (with the folder they
  belong in, e.g. `→ models/loras`), mapped nodes or inputs that do not exist or are connected to other
  nodes, and no output node. A workflow that does not validate does not run (the job fails with the same
  list).

## How a Studio job runs

1. The job waits in the one-at-a-time AI GPU queue like every other AI job.
2. The warm AI workers give back their GPU memory (`release_gpu`, weights stay in RAM); ComfyUI is started if
   it is managed and not running (autostart); the workflow is validated.
3. Image-to-video: the image is uploaded with `POST /upload/image` (subfolder `studio`).
4. The mapped inputs are filled, output `filename_prefix` becomes `studio/<job id>`, and the graph is queued
   with `POST /prompt`. Progress comes from the ComfyUI WebSocket (`/ws?clientId=…`): `execution_start` →
   loading, sampler `progress` events → step x/y with an ETA from the measured step time, `VAEDecode` →
   decoding, save node → encoding. (Without the WebSocket it falls back to polling `/history`.)
5. **Cancel** deletes the prompt from ComfyUI's queue (`POST /queue {"delete"}`) and interrupts it
   (`POST /interrupt {"prompt_id"}`).
6. The video is fetched with `/view`, converted (H.264 + VP9 WebM + thumbnail) and added to the b-roll
   library with: workflow id + graph hash, all filled inputs, seed, prompt id, models, licence, timings,
   peak ComfyUI VRAM. The library id includes the graph hash: editing the workflow makes new clips.
7. Before any **other** AI job and before the "Make video" voice-over, the Studio calls
   `POST /free {"unload_models": true, "free_memory": true}` and waits until ComfyUI's VRAM (system monitor,
   owner *ComfyUI*) has dropped. After a "Make video" AI stage that ran ComfyUI jobs it frees it again.

## Custom nodes and licences

- **Custom nodes are code.** A custom node pack is a Python package that runs inside ComfyUI with full
  access to this PC (files, network). The Studio **never installs custom nodes** and, in managed mode,
  starts ComfyUI with `--disable-all-custom-nodes`. If an uploaded workflow needs a pack, the Studio lists
  the missing node types. To use one: install it yourself into `video/comfyui/ComfyUI/custom_nodes/` (only
  from a source you trust, after reading its code or at least its repository), then turn on *Load custom
  nodes* in the ComfyUI settings on the AI page and restart ComfyUI. Validation then shows which pack each
  custom node comes from.
- **ComfyUI is GPL-3.0.** The Studio does not import, link or ship ComfyUI code: it starts it as a separate
  program from its own folder and talks to it only over its HTTP / WebSocket API (an "aggregate", not a
  derivative work). Keep it that way — do not copy ComfyUI code into the Studio. ComfyUI's outputs (your
  videos) are not covered by its licence.
- **Model licences decide what you may do with the clips.** Every workflow states them in its manifest
  (`licence`, `commercial`, `licence_note`); the library stores them with every clip. The samples use only
  Apache-2.0 models (Wan 2.2 by Wan-AI, the Comfy-Org repackage, LightX2V Wan2.2-Lightning) — checked on
  their Hugging Face model cards. For uploads, look up every checkpoint, LoRA, VAE and text encoder; many
  popular LoRAs and some video models are non-commercial. ComfyUI's paid "partner/API nodes" are disabled
  (`--disable-partner-nodes`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| *ComfyUI is not installed* | `py -3.12 video/comfyui/setup.py` |
| *port 8188 is used by another program* | another ComfyUI is running: switch to **external** with its URL, or change the port in `library/comfy-settings.json` |
| ComfyUI state *error*, log shows a CUDA / `sm_120` error | the venv needs torch built for CUDA ≥ 12.8: run `setup.py` again (it pins `torch==2.8.0` from the cu128 index) |
| *Missing nodes: …* | a custom node pack — see above; or the workflow was made for a newer ComfyUI: raise `COMFY_TAG` in `setup.py` and run it |
| *Missing model files: x → models/loras* | put the file in `video/comfyui/models/loras/` (or add it to `setup.py`), click *Check again* on the workflow |
| *this is the ComfyUI UI format* | export with *Workflow → Export (API)* |
| the job uses the workflow's own prompt | the prompt is not mapped: *Edit mapping* → Prompt → the `CLIPTextEncode` that feeds the sampler's positive input |
| out of memory / very slow | lower the preset size or length; raise *Reserve VRAM*; make sure Ovoz is unloaded (Make video does it). Keep dynamic VRAM on: with `--disable-dynamic-vram` the 14B sample loaded partially and had not finished one step after 10 minutes (89 s with it on); `--lowvram` has no effect while dynamic VRAM is on |
| a sample workflow made something unexpected | that is the model, not the mapping — the job's library metadata (`comfy.inputs`) shows exactly what was sent; try another seed |
| the clip is black / noise | wrong sampler settings for the model (e.g. cfg 1 without the 4-step LoRA); test the workflow in ComfyUI first |
| a job hangs at "queued in ComfyUI" | someone else's prompt runs in that ComfyUI (external mode) — the Studio's job waits for it |
| log | AI page → ComfyUI → *ComfyUI log*, or `video/comfyui/comfyui.log` |
