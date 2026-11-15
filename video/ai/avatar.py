"""Audio-driven talking-head presenter with EchoMimicV3-Flash (Apache-2.0, Wan2.1-Fun-1.3B base).

Usage (from video/ai, after portrait.py made build/presenter.png):
  .venv/Scripts/python avatar.py                    # all scenes -> out/ai-avatar.mp4
  .venv/Scripts/python avatar.py --scenes hook      # first scene only
  .venv/Scripts/python avatar.py --assemble-only

Studio mode (Video Studio AI queue):
  .venv/Scripts/python avatar.py --job job.json
job.json = {"portrait": png, "width": 448, "height": 800, "steps": 8, "teacache": 0.1, "seed": 43,
            "prompt": "...", "segments": [{"wav": seg.wav, "out": seg.mp4, "min_dur": 4.2}, ...]}
Animates every segment whose "out" does not exist yet (one pipeline load), printing
"@@PROGRESS {json}" per denoising step and "@@RESULT {json}" at the end.

Each scene's wav (shared/build/scene-<id>.wav) is animated separately from the same portrait
(keeps sequences short enough for 16 GB), then trimmed to the scene length, concatenated and
muxed with voiceover.wav.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parent
REPO = HERE / ".models" / "echomimic_v3" / "repo"
W_DIR = HERE / ".models" / "echomimic_v3" / "flash"
sys.path.insert(0, str(REPO))

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import pyloudnorm as pyln  # noqa: E402
import torch  # noqa: E402
from einops import rearrange  # noqa: E402
from omegaconf import OmegaConf  # noqa: E402
from PIL import Image  # noqa: E402
from safetensors.torch import load_file  # noqa: E402
from transformers import AutoTokenizer, Wav2Vec2FeatureExtractor  # noqa: E402

from src.cache_utils import get_teacache_coefficients  # noqa: E402
from src.fm_solvers_unipc import FlowUniPCMultistepScheduler  # noqa: E402
from src.pipeline_wan_fun_inpaint_audio_2512 import WanFunInpaintAudioPipeline  # noqa: E402
from src.utils import filter_kwargs, get_image_to_video_latent2, save_videos_grid  # noqa: E402
from src.wan_image_encoder import CLIPModel  # noqa: E402
from src.wan_text_encoder import WanT5EncoderModel  # noqa: E402
from src.wan_transformer3d_audio_2512 import WanTransformerAudioMask3DModel  # noqa: E402
from src.wan_vae import AutoencoderKLWan  # noqa: E402
from src.wav2vec2 import Wav2Vec2Model  # noqa: E402

import studio_io as io  # noqa: E402

FFMPEG = str(VIDEO / "bin" / "ffmpeg.exe")
SHARED = VIDEO / "shared" / "build"
CLIPS = HERE / "clips"
BUILD = HERE / "build"
FPS = 25
DT = torch.bfloat16


NEG_PROMPT = ("Gesture is bad. Gesture is unclear. Strange and twisted hands. Bad hands. Bad fingers. "
              "Unclear and blurry hands. Blurry face, distorted mouth, extra teeth.")


# free VRAM needed to keep transformer + VAE + CLIP resident (measured peak reserved + 0.6 GB):
# 448x800 12.0 GB; 576x1024 15.8 GB -> spills on a 16 GB card, so "auto" keeps model offload there
FULL_GPU_GB = {448: 12.6, 576: 16.4}


def load(model_dir: Path, steps: int, teacache: float, prompts, gpu: str = "offload"):
    cfg = OmegaConf.load(REPO / "config" / "config.yaml")
    tr = WanTransformerAudioMask3DModel.from_pretrained(
        str(model_dir / cfg["transformer_additional_kwargs"].get("transformer_subpath", "transformer")),
        transformer_additional_kwargs=OmegaConf.to_container(cfg["transformer_additional_kwargs"]),
        low_cpu_mem_usage=True, torch_dtype=DT)
    sd = load_file(str(W_DIR / "_em3" / "echomimicv3-flash-pro" / "diffusion_pytorch_model.safetensors"))
    m, u = tr.load_state_dict(sd.get("state_dict", sd), strict=False)
    print(f"flash weights: missing {len(m)}, unexpected {len(u)}")
    tr = tr.to(DT)
    vae = AutoencoderKLWan.from_pretrained(
        str(model_dir / cfg["vae_kwargs"]["vae_subpath"]),
        additional_kwargs=OmegaConf.to_container(cfg["vae_kwargs"])).to(DT)
    tok = AutoTokenizer.from_pretrained(str(model_dir / cfg["text_encoder_kwargs"]["tokenizer_subpath"]))
    te = WanT5EncoderModel.from_pretrained(
        str(model_dir / cfg["text_encoder_kwargs"]["text_encoder_subpath"]),
        additional_kwargs=OmegaConf.to_container(cfg["text_encoder_kwargs"]),
        low_cpu_mem_usage=True, torch_dtype=DT).eval()
    clip = CLIPModel.from_pretrained(
        str(model_dir / cfg["image_encoder_kwargs"]["image_encoder_subpath"])).to(DT).eval()
    cfg["scheduler_kwargs"]["shift"] = 1
    sch = FlowUniPCMultistepScheduler(**filter_kwargs(
        FlowUniPCMultistepScheduler, OmegaConf.to_container(cfg["scheduler_kwargs"])))
    pipe = WanFunInpaintAudioPipeline(transformer=tr, vae=vae, tokenizer=tok, text_encoder=te,
                                      scheduler=sch, clip_image_encoder=clip)
    # The repo's T5 loader ignores torch_dtype and yields fp32 (22.7 GB). Moving that to the GPU
    # on every call spilled into shared memory (~9 min/scene). Encode the two fixed prompts once
    # on the CPU, cache them, and never run the text encoder on the GPU.
    cache = {}
    with torch.no_grad():
        for p in prompts:
            cache[p] = [e.to(DT) for e in pipe._get_t5_prompt_embeds(prompt=p, device="cpu", dtype=torch.float32)]
    te.to(DT)  # the pipeline reads text_encoder.dtype as weight dtype

    def cached_embeds(prompt=None, num_videos_per_prompt=1, max_sequence_length=512, device=None, dtype=None):
        prompt = prompt[0] if isinstance(prompt, list) else prompt
        return [e.to(device or "cuda", dtype or DT) for e in cache[prompt]]
    pipe._get_t5_prompt_embeds = cached_embeds

    def ensure_prompts(prompts):
        """Warm worker: encode prompts the cache doesn't have yet (CPU, once)."""
        with torch.no_grad():
            for p in prompts:
                if p not in cache:
                    cache[p] = [e.to(DT) for e in raw_embeds(prompt=p, device="cpu", dtype=torch.float32)]
    raw_embeds = type(pipe)._get_t5_prompt_embeds.__get__(pipe)
    pipe.ensure_prompts = ensure_prompts
    if gpu == "full":
        # Full-GPU mode: transformer, VAE and CLIP image encoder stay on the GPU for the whole job
        # (no per-call moves); only the T5 text encoder stays on the CPU (its embeddings are cached).
        for m in (pipe.transformer, pipe.vae, pipe.clip_image_encoder):
            m.to("cuda")

        class _OnCuda(type(pipe)):
            @property
            def _execution_device(self):
                return torch.device("cuda")
        pipe.__class__ = _OnCuda
    else:
        # The upstream script does pipe.to("cuda"); offload instead.
        pipe.enable_model_cpu_offload()
    if teacache > 0:
        coeff = get_teacache_coefficients(str(model_dir))
        pipe.transformer.enable_teacache(coeff, steps, teacache, num_skip_start_steps=5, offload=False)
    return pipe


_W2V: dict = {}   # the audio encoder is loaded once per process (the warm worker reuses it across jobs)


def audio_embeds(wav: np.ndarray, n_frames: int):
    if not _W2V:
        d = str(W_DIR / "chinese-wav2vec2-base")
        enc = Wav2Vec2Model.from_pretrained(d, local_files_only=True).to("cpu")
        enc.feature_extractor._freeze_parameters()
        _W2V["enc"], _W2V["fe"] = enc, Wav2Vec2FeatureExtractor.from_pretrained(d, local_files_only=True)
    enc, fe = _W2V["enc"], _W2V["fe"]
    feat = torch.from_numpy(np.squeeze(fe(wav, sampling_rate=16000).input_values)).float()[None]
    # transformers 5.x no longer fills hidden_states through the repo's custom forward, so
    # capture each encoder layer's output with hooks (== hidden_states[1:] in transformers 4.x).
    hs = []
    hooks = [l.register_forward_hook(lambda m, i, o: hs.append(o[0] if isinstance(o, tuple) else o))
             for l in enc.encoder.layers]
    with torch.no_grad():
        enc(feat, seq_len=n_frames, output_hidden_states=True)
    for h in hooks:
        h.remove()
    emb = rearrange(torch.stack(hs, dim=1).squeeze(0), "b s d -> s b d")
    idx = torch.arange(n_frames)[:, None] + (torch.arange(5) - 2)[None]
    idx = idx.clamp(0, emb.shape[0] - 1)
    return emb[idx][None].to("cuda", DT)  # [1, F, 5, 12, 768]


def gen_scene(pipe, portrait: Image.Image, wav_path: Path, size, steps, seed, prompt, min_dur=0.0, callback=None):
    wav, _ = librosa.load(str(wav_path), sr=16000)
    loud = pyln.Meter(16000).integrated_loudness(wav)
    if abs(loud) < 100:
        wav = pyln.normalize.loudness(wav, loud, -23)
    dur = max(len(wav) / 16000, min_dur)  # cover the scene's trailing pause too
    n = math.ceil(dur * FPS)
    n = ((n - 1 + 3) // 4) * 4 + 1  # round up to 4k+1
    wav = np.pad(wav, (0, max(0, int(n / FPS * 16000) - len(wav))))
    emb = audio_embeds(wav, n)
    vid, mask, clip_img = get_image_to_video_latent2(portrait, None, video_length=n, sample_size=size)
    g = torch.Generator("cuda").manual_seed(seed)
    with torch.no_grad():
        out = pipe(prompt, num_frames=n, negative_prompt=NEG_PROMPT,
            audio_embeds=emb, audio_scale=1.0, ip_mask=None, use_un_ip_mask=False,
            height=size[0], width=size[1], generator=g, neg_scale=1.0, neg_steps=0,
            use_dynamic_cfg=False, use_dynamic_acfg=False, guidance_scale=6.0,
            audio_guidance_scale=3.0, num_inference_steps=steps, video=vid, mask_video=mask,
            clip_image=clip_img, cfg_skip_ratio=0.0, shift=5.0,
            **({"callback_on_step_end": callback} if callback else {})).videos
    return out[:, :, :n], n


def assemble(scenes, tl):
    font = BUILD / "Inter-800.ttf"
    parts, fc = [], []
    for i, sc in enumerate(scenes):
        parts += ["-i", str(CLIPS / f"avatar-{sc['id']}.mp4")]
        fc.append(f"[{i}:v]trim=0:{sc['end'] - sc['start']:.3f},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=1,fps=30,trim=0:{sc['end'] - sc['start']:.3f},"
                  f"scale=1080:1920:flags=lanczos,setsar=1[s{i}]")
    fc.append("".join(f"[s{i}]" for i in range(len(scenes))) + f"concat=n={len(scenes)}:v=1:a=0[cat]")
    last = "[cat]"
    if font.exists():  # headline lower-third per scene (font built by assemble.py)
        ff = str(font).replace("\\", "/").replace(":", r"\:")
        dt = []
        for j, sc in enumerate(scenes):
            tf = BUILD / f"avatar-head-{sc['id']}.txt"
            tf.write_text(sc["headline"] if sc.get("layout") != "stat" else f"{sc['stat']} {sc['headline']}",
                          encoding="utf-8")
            st = 0 if j == 0 else scenes[j]["start"] - scenes[0]["start"]
            en = scenes[j]["end"] - scenes[0]["start"]
            dt.append(f"drawtext=fontfile='{ff}':textfile='{str(tf).replace(chr(92), '/').replace(':', chr(92) + ':')}':"
                      f"expansion=none:fontsize=84:fontcolor=#F5F7FF:x=(w-text_w)/2:y=1560:"
                      f"box=1:boxcolor=#7C5CFF@0.85:boxborderw=28:enable='between(t,{st:.3f},{en:.3f})'")
        fc.append(f"[cat]{','.join(dt)}[v]")
        last = "[v]"
    a0, a1 = scenes[0]["start"], scenes[-1]["end"]
    out = VIDEO / "out" / "ai-avatar.mp4"
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *parts,
           "-ss", f"{a0:.3f}", "-t", f"{a1 - a0:.3f}", "-i", str(SHARED / "voiceover.wav"),
           "-filter_complex", ";".join(fc), "-map", last, "-map", f"{len(scenes)}:a",
           "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--portrait", default=str(BUILD / "presenter.png"))
    ap.add_argument("--scenes", nargs="*")
    ap.add_argument("--width", type=int, default=576)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--teacache", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=43)
    ap.add_argument("--prompt", default="A friendly female presenter is speaking to the camera, natural head movement, clear lip movement.")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--job", help="studio mode: JSON job file (see module docstring)")
    args = ap.parse_args()
    if args.job:
        return studio_job(Path(args.job))

    tl = json.loads((SHARED / "timeline.json").read_text(encoding="utf-8"))
    scenes = [s for s in tl["scenes"] if not args.scenes or s["id"] in args.scenes]
    if not args.assemble_only:
        portrait = Image.open(args.portrait).convert("RGB").resize((args.width, args.height), Image.LANCZOS)
        t0 = time.time()
        pipe = load(W_DIR / "Wan2.1-Fun-V1.1-1.3B-InP", args.steps, args.teacache,
                    [args.prompt, NEG_PROMPT])
        load_s = time.time() - t0
        stats_path = CLIPS / "avatar_stats.json"
        stats = json.loads(stats_path.read_text()) if stats_path.exists() else []
        for sc in scenes:
            torch.cuda.reset_peak_memory_stats()
            t = time.time()
            vid, n = gen_scene(pipe, portrait, SHARED / sc["audio"], [args.height, args.width],
                               args.steps, args.seed, args.prompt,
                               min_dur=sc["end"] - sc["start"])
            torch.cuda.synchronize()
            gen_s = time.time() - t
            save_videos_grid(vid, str(CLIPS / f"avatar-{sc['id']}.mp4"), fps=FPS)
            rec = {"scene": sc["id"], "frames": n, "seconds_of_video": round(n / FPS, 2),
                   "size": f"{args.width}x{args.height}", "steps": args.steps,
                   "teacache": args.teacache, "gen_seconds": round(gen_s, 1),
                   "load_seconds": round(load_s, 1),
                   "peak_vram_allocated_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                   "peak_vram_reserved_gb": round(torch.cuda.max_memory_reserved() / 2**30, 2)}
            stats.append(rec)
            stats_path.write_text(json.dumps(stats, indent=2))
            print(json.dumps(rec), flush=True)
    assemble(scenes, tl)


DEFAULT_PROMPT = "A friendly presenter is speaking to the camera, natural head movement, clear lip movement."


def studio_job(job_path: Path):
    job = json.loads(job_path.read_text(encoding="utf-8"))
    W, H = int(job.get("width", 448)), int(job.get("height", 800))
    steps, seed = int(job.get("steps", 8)), int(job.get("seed", 43))
    prompt = job.get("prompt") or DEFAULT_PROMPT
    todo = [sg for sg in job["segments"] if not Path(sg["out"]).exists()]
    total = max(1, len(todo) * steps)
    io.progress("load", 0, total, segments=len(todo))
    if not todo:
        io.result(generated=0, segments=len(job["segments"]))
        return
    portrait = Image.open(job["portrait"]).convert("RGB").resize((W, H), Image.LANCZOS)
    gpu = job.get("gpu", "auto")
    free0 = torch.cuda.mem_get_info()[0] / 2**30
    if gpu == "auto":
        gpu = "full" if free0 >= FULL_GPU_GB.get(W, 11.0) else "offload"
    print(f"free VRAM {free0:.1f} GB -> {gpu}", flush=True)
    t0 = time.time()
    pipe = load(W_DIR / "Wan2.1-Fun-V1.1-1.3B-InP", steps, float(job.get("teacache", 0.1)), [prompt, NEG_PROMPT], gpu)
    load_s = time.time() - t0
    print(f"pipeline loaded in {load_s:.1f}s", flush=True)
    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    done, video_s, per = 0, 0.0, []
    for k, sg in enumerate(todo):
        base = done

        def cb(_pipe, i, _t, kw, base=base, k=k):
            io.progress("denoise", base + i + 1, total, segment=k + 1, segments=len(todo))
            return kw

        ts = time.time()
        vid, n = gen_scene(pipe, portrait, Path(sg["wav"]), [H, W], steps, seed, prompt,
                           min_dur=float(sg.get("min_dur") or 0), callback=cb)
        out = Path(sg["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(out.stem + ".part.mp4")
        save_videos_grid(vid, str(tmp), fps=FPS)
        tmp.replace(out)
        done += steps
        video_s += n / FPS
        per.append({"out": out.name, "frames": n, "seconds": round(time.time() - ts, 1)})
        print(json.dumps(per[-1]), flush=True)
    io.result(generated=len(todo), segments=len(job["segments"]), width=W, height=H, steps=steps,
              video_seconds=round(video_s, 2), load_seconds=round(load_s, 1), gpu_mode=gpu,
              full_gpu=gpu == "full", free_vram_at_start_gb=round(free0, 1),
              gen_seconds=round(time.time() - t1, 1), per_segment=per, **io.vram_stats())


if __name__ == "__main__":
    main()
