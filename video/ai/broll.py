"""Generate vertical b-roll clips with Wan2.2-TI2V-5B (Apache-2.0) via diffusers.

Usage (from video/ai):
  .venv/Scripts/python broll.py                       # all default prompts
  .venv/Scripts/python broll.py --only waves --steps 30 --frames 81
  .venv/Scripts/python broll.py --prompt "..." --name custom

Writes clips/<name>.mp4 and appends timing / peak-VRAM stats to clips/stats.json.

Studio mode (used by the Video Studio AI queue):
  .venv/Scripts/python broll.py --prompt "..." --out-file X.mp4 --progress [--negative "..."]
writes exactly X.mp4, prints "@@PROGRESS {json}" per denoising step and one "@@RESULT {json}"
line (timings + peak VRAM) at the end, and does not touch clips/stats.json.
"""
import argparse
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(HERE / ".models" / "hf"))

import torch  # noqa: E402
from diffusers import AutoencoderKLWan, WanPipeline  # noqa: E402
from diffusers.hooks import apply_group_offloading  # noqa: E402
from diffusers.utils import export_to_video  # noqa: E402

import studio_io as io  # noqa: E402

MODEL_DIR = HERE / ".models" / "Wan2.2-TI2V-5B-Diffusers"
MODEL_ID = "Wan-AI/Wan2.2-TI2V-5B-Diffusers"

PROMPTS = {
    "waves": (
        "Abstract glowing sound waves flowing smoothly through dark space, luminous ribbons of "
        "purple and teal light undulating like an audio waveform, soft particles and bokeh, deep "
        "navy black background, cinematic, elegant motion graphics, high detail, slow camera drift"
    ),
    "phone": (
        "A modern smartphone lying on a dark wooden desk at night, its screen shows a bright glowing "
        "purple and teal audio waveform animating, soft reflections, shallow depth of field, moody "
        "cinematic lighting, slow dolly-in camera movement, photorealistic"
    ),
    "mic": (
        "Close-up of a professional studio microphone with a pop filter in a dark recording studio, "
        "soft purple and teal bokeh lights in the background, gentle haze, cinematic lighting, slow "
        "orbiting camera, photorealistic, shallow depth of field"
    ),
}

NEGATIVE = (
    "bright colors, overexposed, static, blurred details, subtitles, text, watermark, logo, style, "
    "artwork, painting, still picture, overall gray, worst quality, low quality, JPEG compression "
    "residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, "
    "disfigured, misshapen limbs, fused fingers, still frame, messy background, many people, "
    "walking backwards"
)


# Full-GPU mode ("full"): the three stages run one after another, each with the GPU to itself and the
# CUDA cache emptied in between: UMT5 text encoder (11.4 GB) encodes the prompts -> back to the CPU;
# the whole transformer (10 GB) is resident for every diffusion step (no weight streaming); then the
# VAE decodes. Plain model-level offload ("model") keeps the encoder's freed blocks cached and spilled
# ~2.3 GB into shared memory at 704x1280. Needs ~13 GB free; otherwise fall back to block-level group
# offloading (weights stream in block by block, ~8-11 GB peak).
FULL_GPU_GB = 13.0


def full_need_gb(width: int, height: int, frames: int) -> float:
    """VRAM the full-GPU mode needs, from measured peaks on an RTX 5070 Ti (torch reserved + 0.5 GB):
    480x832x81 (8.2k latent tokens) 11.65 GB, 704x1280x121 (27.3k tokens) 16.95 GB."""
    tokens = ((frames - 1) // 4 + 1) * (width // 32) * (height // 32)
    return 9.4 + 0.28 * tokens / 1000 + 0.5


def pick_offload(mode: str, need_gb: float = FULL_GPU_GB) -> str:
    """auto: full GPU only when it fits without spilling into shared memory (spilling made it slower
    than group offload: 32 vs 23 s/step at 704x1280); otherwise group offload, which streams weights
    with CUDA streams and is within ~3 % of full-GPU speed when the GPU is otherwise free."""
    if mode != "auto":
        return mode
    free = torch.cuda.mem_get_info()[0] / 2**30
    print(f"free VRAM {free:.1f} GB, full-GPU mode needs ~{need_gb:.1f} GB -> "
          f"{'full GPU' if free >= need_gb else 'group offload'}", flush=True)
    return "full" if free >= need_gb else "group"


def load_pipe(offload: str):
    offload = pick_offload(offload)
    src = str(MODEL_DIR) if (MODEL_DIR / "model_index.json").exists() else MODEL_ID
    vae = AutoencoderKLWan.from_pretrained(src, subfolder="vae", torch_dtype=torch.float32)
    pipe = WanPipeline.from_pretrained(src, vae=vae, torch_dtype=torch.bfloat16)
    if offload == "full":
        pass  # components are moved stage by stage in run_full()
    elif offload == "none":
        pipe.to("cuda")
    elif offload == "model":
        pipe.enable_model_cpu_offload()
    elif offload == "sequential":
        pipe.enable_sequential_cpu_offload()
    elif offload == "group":
        # Stream weights block-by-block: near full speed, transformer never fully resident.
        onload, offload_dev = torch.device("cuda"), torch.device("cpu")
        pipe.transformer.enable_group_offload(
            onload_device=onload, offload_device=offload_dev,
            offload_type="block_level", num_blocks_per_group=1, use_stream=True,
        )
        apply_group_offloading(
            pipe.text_encoder, onload_device=onload, offload_device=offload_dev,
            offload_type="leaf_level",
        )
        pipe.vae.to("cuda")
    else:
        raise ValueError(offload)
    pipe.vae.enable_tiling()
    return pipe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="subset of default prompt names")
    ap.add_argument("--prompt", help="custom prompt (use with --name)")
    ap.add_argument("--name", default="custom")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--guidance", type=float, default=5.0)
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--frames", type=int, default=121, help="4k+1; 121 = 5 s @ 24 fps")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--offload", default="group", choices=["auto", "full", "none", "model", "group", "sequential"],
                    help="auto = full GPU when >= 13 GB are free, else group")
    ap.add_argument("--out", default=str(HERE / "clips"))
    ap.add_argument("--out-file", help="studio mode: write this one mp4 (needs --prompt)")
    ap.add_argument("--negative", default=NEGATIVE, help="negative prompt")
    ap.add_argument("--progress", action="store_true", help="print @@PROGRESS / @@RESULT json lines")
    args = ap.parse_args()
    if args.out_file:
        return studio(args)

    jobs = {args.name: args.prompt} if args.prompt else {
        k: v for k, v in PROMPTS.items() if not args.only or k in args.only}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stats_path = out / "stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else []

    t0 = time.time()
    pipe = load_pipe(args.offload)
    load_s = time.time() - t0
    print(f"pipeline loaded in {load_s:.1f}s", flush=True)

    for name, prompt in jobs.items():
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t = time.time()
        frames = pipe(
            prompt=prompt, negative_prompt=args.negative,
            height=args.height, width=args.width, num_frames=args.frames,
            num_inference_steps=args.steps, guidance_scale=args.guidance,
            generator=torch.Generator("cpu").manual_seed(args.seed),
        ).frames[0]
        torch.cuda.synchronize()
        gen_s = time.time() - t
        path = out / f"{name}.mp4"
        export_to_video(frames, str(path), fps=args.fps, quality=9)
        rec = {
            "name": name, "prompt": prompt, "seed": args.seed, "steps": args.steps,
            "guidance": args.guidance, "size": f"{args.width}x{args.height}",
            "frames": args.frames, "fps": args.fps, "offload": args.offload,
            "gen_seconds": round(gen_s, 1), "load_seconds": round(load_s, 1),
            "peak_vram_allocated_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
            "peak_vram_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 2),
            "torch": torch.__version__,
        }
        stats.append(rec)
        stats_path.write_text(json.dumps(stats, indent=2))
        print(json.dumps(rec), flush=True)


def run_full(pipe, args, on_step):
    """Full-GPU generation: encoder, transformer and VAE each get the GPU alone (see FULL_GPU_GB)."""
    dev = torch.device("cuda")

    class _OnCuda(type(pipe)):
        @property
        def _execution_device(self):
            return dev
    pipe.__class__ = _OnCuda
    pipe.text_encoder.to(dev)
    with torch.no_grad():
        pe, ne = pipe.encode_prompt(prompt=args.prompt, negative_prompt=args.negative, do_classifier_free_guidance=True,
                                    num_videos_per_prompt=1, max_sequence_length=512, device=dev)
    pipe.text_encoder.to("cpu")
    torch.cuda.empty_cache()
    pipe.transformer.to(dev)
    latents = pipe(prompt_embeds=pe, negative_prompt_embeds=ne, height=args.height, width=args.width,
                   num_frames=args.frames, num_inference_steps=args.steps, guidance_scale=args.guidance,
                   generator=torch.Generator("cpu").manual_seed(args.seed), callback_on_step_end=on_step,
                   output_type="latent").frames
    del pe, ne
    pipe.transformer.to("cpu")
    torch.cuda.empty_cache()
    vae = pipe.vae.to(dev)
    with torch.no_grad():
        latents = latents.to(dev, dtype=vae.dtype)
        mean = torch.tensor(vae.config.latents_mean).view(1, vae.config.z_dim, 1, 1, 1).to(dev, latents.dtype)
        std = 1.0 / torch.tensor(vae.config.latents_std).view(1, vae.config.z_dim, 1, 1, 1).to(dev, latents.dtype)
        video = vae.decode(latents / std + mean, return_dict=False)[0]
        return pipe.video_processor.postprocess_video(video, output_type="np")[0]


def studio(args):
    """One clip -> args.out_file, with progress lines for the studio queue."""
    if not args.prompt:
        raise SystemExit("--out-file needs --prompt")
    if args.width % 32 or args.height % 32 or (args.frames - 1) % 4:
        raise SystemExit("width/height must be multiples of 32 and frames = 4k+1")
    out = Path(args.out_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    io.progress("load", 0, args.steps)
    t0 = time.time()
    free0 = torch.cuda.mem_get_info()[0] / 2**30
    args.offload = pick_offload(args.offload, full_need_gb(args.width, args.height, args.frames))
    pipe = load_pipe(args.offload)
    load_s = time.time() - t0
    print(f"pipeline loaded in {load_s:.1f}s", flush=True)
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    step_t = [t1]

    def on_step(_pipe, i, _t, kw):
        now = time.time()
        step_t.append(now)
        io.progress("denoise", i + 1, args.steps, step_seconds=round(now - step_t[-2], 2))
        if i + 1 == args.steps:
            io.progress("decode", args.steps, args.steps)  # the VAE decode runs next, inside pipe()
        return kw

    io.progress("denoise", 0, args.steps)
    if args.offload == "full":
        frames = run_full(pipe, args, on_step)
    else:
        frames = pipe(
            prompt=args.prompt, negative_prompt=args.negative,
            height=args.height, width=args.width, num_frames=args.frames,
            num_inference_steps=args.steps, guidance_scale=args.guidance,
            generator=torch.Generator("cpu").manual_seed(args.seed),
            callback_on_step_end=on_step, output_type="np",
        ).frames[0]
    denoise_s = step_t[-1] - t1
    torch.cuda.synchronize()
    io.progress("encode", args.steps, args.steps)
    export_to_video(frames, str(out), fps=args.fps, quality=9)
    gen_s = time.time() - t1
    io.result(file=str(out), prompt=args.prompt, negative=args.negative, seed=args.seed, steps=args.steps,
              guidance=args.guidance, width=args.width, height=args.height, frames=args.frames, fps=args.fps,
              seconds=round(args.frames / args.fps, 2), offload=args.offload, free_vram_at_start_gb=round(free0, 1),
              full_gpu=args.offload in ("full", "none"), load_seconds=round(load_s, 1),
              denoise_seconds=round(denoise_s, 1), decode_seconds=round(gen_s - denoise_s, 1),
              gen_seconds=round(gen_s, 1), torch=torch.__version__, **io.vram_stats())


if __name__ == "__main__":
    main()
