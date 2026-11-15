"""Generate a fictional presenter portrait with Wan2.2-TI2V-5B in text-to-image mode (num_frames=1).

Re-uses the b-roll model, so no extra download and the image stays under Apache-2.0.
Usage: .venv/Scripts/python portrait.py [--seeds 1 2 3 4] -> build/presenter-<seed>.png
Copy the chosen one to build/presenter.png.

Studio mode (Video Studio AI queue): --out X.png --seed N [--prompt "..."] --progress
writes exactly X.png and prints @@PROGRESS / @@RESULT json lines.
"""
import argparse
import time
from pathlib import Path

import torch

import studio_io as io
from broll import load_pipe, pick_offload

HERE = Path(__file__).resolve().parent

PROMPT = (
    "Professional studio portrait photo of a friendly fictional young Central Asian woman news "
    "presenter, head and shoulders, centered, looking straight into the camera with a warm natural "
    "smile, mouth closed, dark blazer, soft key light, dark navy studio background with subtle purple "
    "and teal bokeh lights, sharp focus on face, 85mm lens, photorealistic, high detail skin"
)
NEG = ("cartoon, painting, illustration, blurry, low quality, deformed face, asymmetric eyes, "
       "extra teeth, text, watermark, hands, multiple people")
# appended to studio prompts so the result works for the talking-head model
FRAMING = (", head and shoulders, centered, looking straight into the camera, mouth closed, "
           "plain dark background, soft key light, photorealistic, sharp focus on face")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=[1, 2, 3, 4])
    ap.add_argument("--seed", type=int, help="studio mode: one seed")
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--prompt", default=PROMPT)
    ap.add_argument("--negative", default=NEG)
    ap.add_argument("--out", help="studio mode: output PNG path")
    ap.add_argument("--progress", action="store_true")
    ap.add_argument("--offload", default="auto", help="studio mode: auto | model | group")
    args = ap.parse_args()
    if args.out:
        return studio(args)
    pipe = load_pipe("group")
    out = HERE / "build"
    out.mkdir(exist_ok=True)
    for s in args.seeds:
        t = time.time()
        img = pipe(prompt=args.prompt, negative_prompt=args.negative, height=args.height, width=args.width,
                   num_frames=1, num_inference_steps=args.steps, guidance_scale=5.0,
                   generator=torch.Generator("cpu").manual_seed(s), output_type="pil").frames[0][0]
        img.save(out / f"presenter-{s}.png")
        print(f"seed {s}: {time.time() - t:.1f}s", flush=True)


def studio(args):
    seed = args.seed if args.seed is not None else args.seeds[0]
    prompt = args.prompt if args.prompt == PROMPT else args.prompt.rstrip(". ") + FRAMING
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    io.progress("load", 0, args.steps)
    t0 = time.time()
    mode = pick_offload(args.offload)
    if mode == "full":
        mode = "model"   # one frame: model-level offload is already full speed
    pipe = load_pipe(mode)
    load_s = time.time() - t0
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()

    def on_step(_pipe, i, _t, kw):
        io.progress("denoise", i + 1, args.steps)
        return kw

    io.progress("denoise", 0, args.steps)
    img = pipe(prompt=prompt, negative_prompt=args.negative, height=args.height, width=args.width,
               num_frames=1, num_inference_steps=args.steps, guidance_scale=5.0,
               generator=torch.Generator("cpu").manual_seed(seed), output_type="pil",
               callback_on_step_end=on_step).frames[0][0]
    img.save(out)
    io.result(file=str(out), prompt=prompt, seed=seed, steps=args.steps, width=args.width, height=args.height,
              offload=mode, load_seconds=round(load_s, 1), gen_seconds=round(time.time() - t1, 1), **io.vram_stats())


if __name__ == "__main__":
    main()
