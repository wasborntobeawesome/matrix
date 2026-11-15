"""Warm AI model worker for the Video Studio: one process per model family keeps its weights in
system RAM between jobs and moves them to the GPU only while a job runs.

    .venv/Scripts/python worker.py --family wan        # Wan2.2-TI2V-5B (b-roll, portraits) + FastWan2.2-TI2V-5B
    .venv/Scripts/python worker.py --family a14b-t2v   # Wan2.2-T2V-A14B + Lightning 4-step LoRA
    .venv/Scripts/python worker.py --family a14b-i2v   # Wan2.2-I2V-A14B + Lightning 4-step LoRA
    .venv/Scripts/python worker.py --family echomimic  # EchoMimicV3-Flash talking head
    .venv/Scripts/python worker.py --family acestep    # ACE-Step v1.5 XL turbo music
    .venv/Scripts/python worker.py --family wan --once task.json   # one job, then exit (cold mode / CLI)

Protocol: the worker listens on 127.0.0.1:<random port> (announced in @@READY {"port": ...}) and reads one
JSON command per line from the studio's connection; machine-readable lines go to stdout (studio_io).
(Not stdin: on Windows a thread blocked reading the stdin pipe deadlocks DLL loading in other threads —
scipy/OpenBLAS hung on import.) When the connection closes (studio gone) the worker exits.
  {"op": "run", "id": j, "task": {...}}  -> @@PROGRESS {...}* then @@RESULT {"id": j, ...} or @@ERROR {"id": j, ...}
  {"op": "cancel", "id": j}               -> the running job stops at its next step (weights stay in RAM)
  {"op": "status"}                        -> @@STATUS {models, ram, vram, busy}
  {"op": "release_gpu"}                   -> everything back to RAM, CUDA cache emptied (when idle)
  {"op": "drop", "keys": [...]}           -> forget these models (RAM)
  {"op": "quit"}
Weights live in RAM as bf16 (VAE fp32 where the original scripts use it). Big transformers and the UMT5
text encoder use diffusers group offloading with CUDA streams: blocks stream from pinned RAM to the GPU
while the previous block computes, so after a job nothing but the CUDA context stays on the GPU.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import queue
import socket
import sys
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(HERE / ".models" / "hf"))
MODELS = HERE / ".models"

import torch  # noqa: E402

import studio_io as io  # noqa: E402

DEV = torch.device("cuda")
BF16 = torch.bfloat16


class Cancelled(Exception):
    pass


def _gb(module) -> float:
    n = 0
    for t in list(module.parameters()) + list(module.buffers()):
        n += t.numel() * t.element_size()
    return round(n / 2**30, 2)


def _rss_gb() -> float | None:
    try:
        import psutil
        m = psutil.Process().memory_info()
        return round(m.rss / 2**30, 2)
    except Exception:
        return None


class Ctx:
    """Per-job helpers: progress lines, cancel checks, timing."""

    def __init__(self, jid, cancel: threading.Event):
        self.jid, self.cancel_ev, self.t0 = jid, cancel, time.time()

    def progress(self, stage, done=0, total=0, **kw):
        io.progress(stage, done, total, **kw)

    def check(self):
        if self.cancel_ev.is_set():
            raise Cancelled()

    def step_cb(self, total, offset=0, **extra):
        """diffusers callback_on_step_end: progress + cancel between steps."""
        last = [time.time()]

        def cb(_pipe, i, _t, kw):
            now = time.time()
            self.progress("denoise", offset + i + 1, total, step_seconds=round(now - last[0], 2), **extra)
            last[0] = now
            self.check()
            return kw
        return cb


class Family:
    name = "?"

    def __init__(self):
        self.models: dict[str, dict] = {}     # key -> {"obj", "gb", "last"}
        self.lock = threading.RLock()

    def get(self, key, loader):
        m = self.models.get(key)
        if m is None:
            t = time.time()
            obj = loader()
            gb = obj[1] if isinstance(obj, tuple) else None
            obj = obj[0] if isinstance(obj, tuple) else obj
            m = self.models[key] = {"obj": obj, "gb": gb, "loaded_in": round(time.time() - t, 1), "last": time.time()}
        m["last"] = time.time()
        return m["obj"]

    def loaded(self, key) -> bool:
        return key in self.models

    def status(self) -> list[dict]:
        return [{"key": k, "gb": v["gb"], "last": v["last"], "loaded_in": v["loaded_in"]} for k, v in self.models.items()]

    def drop(self, keys):
        for k in keys:
            self.models.pop(k, None)
        self.after_drop()

    def after_drop(self):
        gc.collect()
        torch.cuda.empty_cache()
        try:
            torch._C._host_emptyCache()      # give pinned host blocks back to the OS
        except Exception:
            pass

    def release_gpu(self):
        gc.collect()
        torch.cuda.empty_cache()

    def run(self, task: dict, ctx: Ctx) -> dict:
        raise NotImplementedError


# ====================================================================== Wan family
OFFLOAD = os.environ.get("STUDIO_OFFLOAD", "registered")   # registered | pinned | lowmem | sync (see _group)


def _group(module, level="block_level", stream=True, mode=None):
    """Weights stay in RAM; blocks go to the GPU only while they compute.
    registered (default): weights page-locked in place with cudaHostRegister + CUDA streams; measured on Wan 5B
            (5 steps 480x832x81): 20.4 s denoise vs pinned 21.1, lowmem 26.3, sync 34.4 — identical output
    pinned: page-locked copies + CUDA streams (prefetch overlaps compute; PyTorch's pinned allocator rounds
            every tensor up to a power of two, so RAM use can reach ~1.5-2x the weights)
    lowmem: pageable weights, pinned on the fly per block (RAM = weights; extra CPU copies per step)
    sync:   pageable weights, plain blocking copies (no streams)"""
    from diffusers.hooks import apply_group_offloading
    mode = mode or OFFLOAD
    kw = {"num_blocks_per_group": 1} if level == "block_level" else {}
    if mode == "registered":
        # page-lock the weights where they are (cudaHostRegister): streams can copy straight from them, and no
        # pinned copies are made (pinned copies go through PyTorch's caching host allocator, which rounds every
        # tensor up to a power of two). Then group offloading must not copy: low_cpu_mem_usage=True.
        _register_host(module)
        apply_group_offloading(module, onload_device=DEV, offload_device=torch.device("cpu"), offload_type=level,
                               use_stream=stream, low_cpu_mem_usage=True, **kw)
        return module
    apply_group_offloading(module, onload_device=DEV, offload_device=torch.device("cpu"), offload_type=level,
                           use_stream=stream and mode != "sync", low_cpu_mem_usage=mode == "lowmem", **kw)
    return module


def _bf16(folder: Path, must: bool = False) -> Path:
    """The bf16 copy made by convert_bf16.py (.models/bf16/<repo without -Diffusers>/<part>) when it exists.
    The repos ship fp32 shards; loading those as bf16 first reads everything in fp32 (2x the RAM)."""
    alt = MODELS / "bf16" / folder.parent.name.replace("-Diffusers", "") / folder.name
    if (alt / "diffusion_pytorch_model.safetensors.index.json").exists():
        return alt
    if must:
        raise RuntimeError(f"run convert_bf16.py for {folder} first: loading the fp32 shards needs ~2x RAM "
                           f"(see video/ai/README.md)")
    return folder


_REGISTERED: list = []     # keep the registered tensors alive (and unregister when the model is dropped)


def _register_host(module) -> int:
    """cudaHostRegister every parameter/buffer of `module` in place (each gets its own fresh storage first, so
    nothing points into a read-only file mapping). Returns the bytes registered."""
    cudart = torch.cuda.cudart()
    n = 0
    with torch.no_grad():
        for t in list(module.parameters()) + list(module.buffers()):
            if t.device.type != "cpu" or t.numel() == 0:
                continue
            fresh = torch.empty_like(t.data, memory_format=torch.contiguous_format)
            fresh.copy_(t.data)
            t.data = fresh
            size = fresh.untyped_storage().nbytes()
            r = cudart.cudaHostRegister(fresh.data_ptr(), size, 0)
            if int(r) != 0:
                raise RuntimeError(f"cudaHostRegister failed ({r})")
            _REGISTERED.append(fresh)
            n += size
    return n


def _force_cuda(pipe):
    """Pipelines with offload hooks sometimes pick the CPU as execution device; we always compute on CUDA."""
    if getattr(type(pipe), "_studio_cuda", False):
        return pipe

    class _OnCuda(type(pipe)):
        _studio_cuda = True

        @property
        def _execution_device(self):
            return DEV
    pipe.__class__ = _OnCuda
    return pipe


class DMDScheduler:
    """Few-step DMD sampling for FastWan (FastVideo): predict x0 = x_t - sigma_t * v, then re-noise to the next
    timestep (sigma = t / 1000), exactly like FastVideo's DmdDenoisingStage; the last step returns x0."""
    order = 1

    def __init__(self, steps=(1000, 757, 522), seed=0):
        from diffusers.configuration_utils import FrozenDict
        self.steps = list(steps)
        self.gen = torch.Generator("cpu").manual_seed(int(seed) + 1)
        self.config = FrozenDict(num_train_timesteps=1000, shift=1.0)
        self.timesteps = torch.tensor(self.steps, dtype=torch.float32)
        self.sigmas = torch.tensor([s / 1000 for s in self.steps] + [0.0])
        self._i = 0

    def set_timesteps(self, num_inference_steps=None, device=None, **kw):
        self.timesteps = self.timesteps.to(device)
        self._i = 0

    def set_begin_index(self, i=0):
        self._i = i

    def scale_model_input(self, x, t=None):
        return x

    def step(self, model_output, timestep, sample, return_dict=False, **kw):
        i = self._i
        s = float(self.sigmas[i])
        x0 = sample.double() - s * model_output.double()
        if i + 1 < len(self.steps):
            sn = float(self.sigmas[i + 1])
            noise = torch.randn(sample.shape, generator=self.gen, dtype=torch.float32).to(sample.device)
            out = (1 - sn) * x0 + sn * noise.double()
        else:
            out = x0
        self._i += 1
        return (out.to(sample.dtype),)


def _fit_image(path: str, w: int, h: int):
    """Cover-crop an image to w x h (keeps the centre; faces in portraits sit high, so keep the upper part)."""
    from PIL import Image
    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    if iw / ih > w / h:
        nw = round(ih * w / h)
        box = ((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih)
    else:
        nh = round(iw * h / w)
        y0 = round((ih - nh) * 0.3)
        box = (0, y0, iw, y0 + nh)
    return im.crop(box).resize((w, h), Image.LANCZOS)


class WanBase(Family):
    """Shared pieces: the UMT5-XXL text encoder (identical in every Wan2.2 repo) with a prompt cache."""
    TE_DIR = MODELS / "Wan2.2-TI2V-5B-Diffusers"

    def __init__(self):
        super().__init__()
        self.embeds: dict = {}

    def text(self):
        def load():
            from transformers import AutoTokenizer, UMT5EncoderModel
            te = UMT5EncoderModel.from_pretrained(str(self.TE_DIR / "text_encoder"), torch_dtype=BF16).eval()
            tok = AutoTokenizer.from_pretrained(str(self.TE_DIR / "tokenizer"))
            gb = _gb(te)
            _group(te, "leaf_level", stream=True)
            return (te, tok), gb
        return self.get("umt5", load)

    def encode(self, pipe, prompt: str, negative: str | None, cfg: bool):
        key = (prompt, negative if cfg else None)
        if key not in self.embeds:
            with torch.no_grad():
                pe, ne = pipe.encode_prompt(prompt=prompt, negative_prompt=negative if cfg else None,
                                            do_classifier_free_guidance=cfg, num_videos_per_prompt=1,
                                            max_sequence_length=512, device=DEV)
            self.embeds[key] = (pe.cpu(), ne.cpu() if ne is not None else None)
            while len(self.embeds) > 24:
                self.embeds.pop(next(iter(self.embeds)))
        pe, ne = self.embeds[key]
        return pe.to(DEV), (ne.to(DEV) if ne is not None else None)

    def decode_frames(self, pipe, latents, ctx, full_gb: float | None = None):
        """VAE decode on the GPU (then back to RAM) -> float frames [T, H, W, 3] 0..1.
        full_gb: VRAM an untiled decode needs; when that much is free the VAE decodes in one piece (bf16 VAE:
        480x832x81 takes 10 s untiled vs 18.5 s tiled vs ~32 s for the fp32 tiled decode)."""
        vae = pipe.vae
        ctx.progress("decode", 1, 1)
        vae.to(DEV)
        if full_gb is not None:
            if torch.cuda.mem_get_info()[0] / 2**30 > full_gb + 0.8:
                vae.disable_tiling()
            else:
                vae.enable_tiling()
        try:
            with torch.no_grad():
                lat = latents.to(DEV, dtype=vae.dtype)
                mean = torch.tensor(vae.config.latents_mean).view(1, vae.config.z_dim, 1, 1, 1).to(DEV, lat.dtype)
                std = 1.0 / torch.tensor(vae.config.latents_std).view(1, vae.config.z_dim, 1, 1, 1).to(DEV, lat.dtype)
                video = vae.decode(lat / std + mean, return_dict=False)[0]
                frames = pipe.video_processor.postprocess_video(video, output_type="np")[0]
        finally:
            vae.to("cpu")
            torch.cuda.empty_cache()
        return frames

    def write(self, frames, out: str, fps: int, ctx):
        from diffusers.utils import export_to_video
        ctx.progress("encode", 1, 1)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        if getattr(frames, "ndim", 0) == 4 and frames.shape[0] == 1:   # a still (portrait): PNG
            from PIL import Image
            Image.fromarray((frames[0] * 255).round().clip(0, 255).astype("uint8")).save(out)
        else:
            export_to_video(list(frames), out, fps=fps, quality=9)


class Wan(WanBase):
    """Wan2.2-TI2V-5B (provider wan22-5b, portraits) and FastWan2.2-TI2V-5B (provider fastwan-5b).
    Both are text+image-to-video with the Wan2.2 VAE; they share the UMT5 encoder and the VAE."""
    name = "wan"
    TRANSFORMERS = {"wan22-5b": MODELS / "Wan2.2-TI2V-5B-Diffusers",
                    "fastwan-5b": MODELS / "FastWan2.2-TI2V-5B-FullAttn-Diffusers"}

    def vae(self, bf16: bool = False):
        """fp32 + tiling for wan22-5b (unchanged); a bf16 copy for the fast provider (decode ~3x faster)."""
        def load():
            from diffusers import AutoencoderKLWan
            v = AutoencoderKLWan.from_pretrained(str(self.TE_DIR / "vae"), torch_dtype=BF16 if bf16 else torch.float32)
            v.enable_tiling()
            return v, _gb(v)
        return self.get("vae22-bf16" if bf16 else "vae22", load)

    def transformer(self, provider):
        def load():
            from diffusers import WanTransformer3DModel
            tr = WanTransformer3DModel.from_pretrained(str(_bf16(self.TRANSFORMERS[provider] / "transformer")), torch_dtype=BF16)
            gb = _gb(tr)
            _group(tr)
            return tr, gb
        return self.get(provider, load)

    def pipe(self, provider, i2v: bool):
        from diffusers import UniPCMultistepScheduler, WanImageToVideoPipeline, WanPipeline
        te, tok = self.text()
        sched = UniPCMultistepScheduler.from_pretrained(str(self.TE_DIR / "scheduler"))
        cls = WanImageToVideoPipeline if i2v else WanPipeline
        kw = dict(tokenizer=tok, text_encoder=te, vae=self.vae(bf16=provider == "fastwan-5b"), scheduler=sched,
                  transformer=self.transformer(provider), expand_timesteps=True)
        if i2v:
            kw.update(image_encoder=None, image_processor=None)
        p = cls(**kw)
        p.set_progress_bar_config(disable=True)
        return _force_cuda(p)

    def run(self, task, ctx):
        prov = task.get("provider") or "wan22-5b"
        if task.get("kind") == "portrait":            # Wan2.2 text-to-image (num_frames = 1), as portrait.py
            from portrait import FRAMING, NEG, PROMPT
            p = task.get("prompt") or PROMPT
            task = {**task, "prompt": p if p == PROMPT else p.rstrip(". ") + FRAMING, "negative": NEG, "frames": 1,
                    "guidance": 5.0, "width": task.get("width", 704), "height": task.get("height", 1280)}
        W, H, F = int(task["width"]), int(task["height"]), int(task.get("frames", 1))
        steps = int(task["steps"])
        fast = prov == "fastwan-5b"
        img = task.get("image")
        ctx.progress("load", 0, steps, warm=self.loaded(prov) and self.loaded("umt5"))
        t0 = time.time()
        free0 = torch.cuda.mem_get_info()[0] / 2**30
        pipe = self.pipe(prov, bool(img))
        load_s = time.time() - t0
        if fast:
            pipe.scheduler = DMDScheduler(task.get("dmd_steps") or (1000, 757, 522), task["seed"])
            steps = len(pipe.scheduler.steps)
        guidance = float(task.get("guidance", 1.0 if fast else 5.0))
        cfg = guidance > 1.0
        negative = task.get("negative") or None
        ctx.check()
        torch.cuda.reset_peak_memory_stats()
        t1 = time.time()
        pe, ne = self.encode(pipe, task["prompt"], negative, cfg)
        enc_s = time.time() - t1
        ctx.progress("denoise", 0, steps)
        kw = dict(prompt_embeds=pe, negative_prompt_embeds=ne, height=H, width=W, num_frames=F,
                  num_inference_steps=steps, guidance_scale=guidance,
                  generator=torch.Generator("cpu").manual_seed(int(task["seed"])),
                  callback_on_step_end=ctx.step_cb(steps), output_type="latent")
        if img:
            kw["image"] = _fit_image(img, W, H)
            pipe.vae.to(DEV)                    # the pipeline encodes the start image with the VAE
        t2 = time.time()
        try:
            with torch.no_grad():
                lat = pipe(**kw).frames
        finally:
            if img:
                pipe.vae.to("cpu")
        denoise_s = time.time() - t2
        # untiled bf16 decode needs ~8.1 GB at 480x832x81 (measured), scaling with the pixel count
        full_gb = 8.1 * (W * H * F) / (480 * 832 * 81) if fast else None
        frames = self.decode_frames(pipe, lat, ctx, full_gb=full_gb)
        decode_s = time.time() - t2 - denoise_s
        fps = int(task.get("fps", 24))
        self.write(frames, task["out"], fps, ctx)
        gen_s = time.time() - t1
        return dict(file=task["out"], provider=prov, width=W, height=H, frames=F, fps=fps, steps=steps, guidance=guidance,
                    seconds=round(F / fps, 2), i2v=bool(img), load_seconds=round(load_s, 1), encode_seconds=round(enc_s, 1),
                    denoise_seconds=round(denoise_s, 1), decode_seconds=round(decode_s, 1), gen_seconds=round(gen_s, 1),
                    offload="group (warm)", full_gpu=False, free_vram_at_start_gb=round(free0, 1), **io.vram_stats())


class A14B(WanBase):
    """Wan2.2 A14B (two 14B experts: high noise -> low noise) with the Wan2.2-Lightning 4-step LoRAs fused in.
    Both experts (2 x 28.6 GB bf16) live in pinned RAM and stream block by block to the GPU."""
    LIGHT = MODELS / "Wan2.2-Lightning"

    def __init__(self, variant):
        super().__init__()
        self.variant = variant           # "t2v" | "i2v"
        self.name = f"a14b-{variant}"
        self.dir = MODELS / ("Wan2.2-T2V-A14B-Diffusers" if variant == "t2v" else "Wan2.2-I2V-A14B-Diffusers")
        self.lora = self.LIGHT / ("Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V1.1" if variant == "t2v"
                                  else "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1")

    def build(self):
        def load():
            from diffusers import AutoencoderKLWan, FlowMatchEulerDiscreteScheduler, WanImageToVideoPipeline, WanPipeline
            from diffusers import WanTransformer3DModel
            te, tok = self.text()
            vae = AutoencoderKLWan.from_pretrained(str(self.dir / "vae"), torch_dtype=torch.float32)
            vae.enable_tiling()
            t1 = WanTransformer3DModel.from_pretrained(str(_bf16(self.dir / "transformer", must=True)), torch_dtype=BF16)
            t2 = WanTransformer3DModel.from_pretrained(str(_bf16(self.dir / "transformer_2", must=True)), torch_dtype=BF16)
            cfg = json.loads((self.dir / "model_index.json").read_text())
            cls = WanPipeline if self.variant == "t2v" else WanImageToVideoPipeline
            kw = dict(tokenizer=tok, text_encoder=te, vae=vae, transformer=t1, transformer_2=t2,
                      scheduler=FlowMatchEulerDiscreteScheduler(shift=5.0), boundary_ratio=cfg.get("boundary_ratio"))
            if self.variant == "i2v":
                kw.update(image_encoder=None, image_processor=None)
            pipe = cls(**kw)
            # Lightning: 4 steps, no CFG. Fuse the LoRAs into the bf16 weights once, then drop the adapters.
            pipe.load_lora_weights(str(self.lora / "high_noise_model.safetensors"), adapter_name="light")
            pipe.load_lora_weights(str(self.lora / "low_noise_model.safetensors"), adapter_name="light_2",
                                   load_into_transformer_2=True)
            pipe.fuse_lora(components=["transformer"], adapter_names=["light"], lora_scale=1.0)
            pipe.fuse_lora(components=["transformer_2"], adapter_names=["light_2"], lora_scale=1.0)
            pipe.unload_lora_weights()
            gb = _gb(t1) + _gb(t2) + _gb(vae)
            _group(t1)
            _group(t2)
            pipe.set_progress_bar_config(disable=True)
            return _force_cuda(pipe), round(gb, 2)
        return self.get(self.name, load)

    def run(self, task, ctx):
        W, H, F = int(task["width"]), int(task["height"]), int(task["frames"])
        steps = int(task.get("steps", 4))
        img = task.get("image")
        if self.variant == "i2v" and not img:
            raise ValueError("a14b-i2v needs an image")
        ctx.progress("load", 0, steps, warm=self.loaded(self.name))
        t0 = time.time()
        free0 = torch.cuda.mem_get_info()[0] / 2**30
        pipe = self.build()
        load_s = time.time() - t0
        ctx.check()
        torch.cuda.reset_peak_memory_stats()
        t1 = time.time()
        pe, _ = self.encode(pipe, task["prompt"], None, False)
        enc_s = time.time() - t1
        from diffusers import FlowMatchEulerDiscreteScheduler
        pipe.scheduler = FlowMatchEulerDiscreteScheduler(shift=float(task.get("shift", 5.0)))
        ctx.progress("denoise", 0, steps)
        kw = dict(prompt_embeds=pe, height=H, width=W, num_frames=F, num_inference_steps=steps, guidance_scale=1.0,
                  guidance_scale_2=1.0, generator=torch.Generator("cpu").manual_seed(int(task["seed"])),
                  callback_on_step_end=ctx.step_cb(steps), output_type="latent")
        if img:
            kw["image"] = _fit_image(img, W, H)
            pipe.vae.to(DEV)
        t2 = time.time()
        try:
            with torch.no_grad():
                lat = pipe(**kw).frames
        finally:
            if img:
                pipe.vae.to("cpu")
        denoise_s = time.time() - t2
        frames = self.decode_frames(pipe, lat, ctx)
        decode_s = time.time() - t2 - denoise_s
        fps = int(task.get("fps", 16))
        self.write(frames, task["out"], fps, ctx)
        return dict(file=task["out"], provider="wan22-a14b", variant=self.variant, width=W, height=H, frames=F, fps=fps,
                    steps=steps, guidance=1.0, seconds=round(F / fps, 2), i2v=bool(img), load_seconds=round(load_s, 1),
                    encode_seconds=round(enc_s, 1), denoise_seconds=round(denoise_s, 1), decode_seconds=round(decode_s, 1),
                    gen_seconds=round(time.time() - t1, 1), offload="group (warm)", full_gpu=False,
                    free_vram_at_start_gb=round(free0, 1), **io.vram_stats())


# ====================================================================== EchoMimic (presenter)
class EchoMimic(Family):
    name = "echomimic"

    def build(self, steps, teacache, prompts):
        import avatar as AV

        def load():
            p = AV.load(AV.W_DIR / "Wan2.1-Fun-V1.1-1.3B-InP", steps, teacache, prompts, "offload")
            p.set_progress_bar_config(disable=True)
            p._studio_tc = (steps, teacache)
            gb = sum(_gb(m) for m in (p.transformer, p.vae, p.text_encoder, p.clip_image_encoder))
            return p, round(gb, 2)
        pipe = self.get("echomimic", load)
        pipe.ensure_prompts(prompts)
        if teacache > 0 and getattr(pipe, "_studio_tc", None) != (steps, teacache):
            from src.cache_utils import get_teacache_coefficients
            coeff = get_teacache_coefficients(str(AV.W_DIR / "Wan2.1-Fun-V1.1-1.3B-InP"))
            pipe.transformer.enable_teacache(coeff, steps, teacache, num_skip_start_steps=5, offload=False)
            pipe._studio_tc = (steps, teacache)
        return pipe

    def run(self, job, ctx):
        import avatar as AV
        from PIL import Image
        from src.utils import save_videos_grid
        W, H = int(job.get("width", 448)), int(job.get("height", 800))
        steps, seed = int(job.get("steps", 8)), int(job.get("seed", 43))
        prompt = job.get("prompt") or AV.DEFAULT_PROMPT
        todo = [sg for sg in job["segments"] if not Path(sg["out"]).exists()]
        total = max(1, len(todo) * steps)
        ctx.progress("load", 0, total, segments=len(todo), warm=self.loaded("echomimic"))
        if not todo:
            return dict(generated=0, segments=len(job["segments"]))
        portrait = Image.open(job["portrait"]).convert("RGB").resize((W, H), Image.LANCZOS)
        free0 = torch.cuda.mem_get_info()[0] / 2**30
        t0 = time.time()
        pipe = self.build(steps, float(job.get("teacache", 0.1)), [prompt, AV.NEG_PROMPT])
        load_s = time.time() - t0
        torch.cuda.reset_peak_memory_stats()
        t1 = time.time()
        done, video_s, per = 0, 0.0, []
        for k, sg in enumerate(todo):
            ctx.check()
            ts = time.time()
            vid, n = AV.gen_scene(pipe, portrait, Path(sg["wav"]), [H, W], steps, seed, prompt,
                                  min_dur=float(sg.get("min_dur") or 0),
                                  callback=ctx.step_cb(total, offset=done, segment=k + 1, segments=len(todo)))
            out = Path(sg["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.stem + ".part.mp4")
            save_videos_grid(vid, str(tmp), fps=AV.FPS)
            tmp.replace(out)
            done += steps
            video_s += n / AV.FPS
            per.append({"out": out.name, "frames": n, "seconds": round(time.time() - ts, 1)})
        pipe.maybe_free_model_hooks()
        return dict(generated=len(todo), segments=len(job["segments"]), width=W, height=H, steps=steps,
                    video_seconds=round(video_s, 2), load_seconds=round(load_s, 1), gpu_mode="offload (warm)",
                    full_gpu=False, free_vram_at_start_gb=round(free0, 1), gen_seconds=round(time.time() - t1, 1),
                    per_segment=per, **io.vram_stats())


# ====================================================================== ACE-Step (music)
class AceStep(Family):
    name = "acestep"
    DIR = MODELS / "acestep-v15-xl-turbo-diffusers"

    def build(self):
        def load():
            from diffusers import AceStepPipeline
            p = AceStepPipeline.from_pretrained(str(self.DIR), torch_dtype=BF16)
            gb = sum(_gb(m) for m in p.components.values() if isinstance(m, torch.nn.Module))
            p.vae.enable_tiling()
            p.enable_model_cpu_offload()      # each part moves to the GPU while it runs, then back to RAM
            p.set_progress_bar_config(disable=True)
            return p, round(gb, 2)
        return self.get("acestep", load)

    def run(self, task, ctx):
        import numpy as np
        import soundfile as sf
        steps = int(task.get("steps", 8))
        ctx.progress("load", 0, steps, warm=self.loaded("acestep"))
        t0 = time.time()
        free0 = torch.cuda.mem_get_info()[0] / 2**30
        pipe = self.build()
        load_s = time.time() - t0
        torch.cuda.reset_peak_memory_stats()
        ctx.progress("denoise", 0, steps)
        t1 = time.time()
        instrumental = task.get("instrumental", True)
        with torch.no_grad():
            out = pipe(prompt=task["prompt"], lyrics="[Instrumental]" if instrumental else (task.get("lyrics") or ""),
                       vocal_language="unknown" if instrumental else task.get("vocal_language", "en"),
                       audio_duration=float(task["duration"]), num_inference_steps=steps, bpm=task.get("bpm"),
                       keyscale=task.get("key") or None, timesignature=task.get("timesignature") or None,
                       generator=torch.Generator(device="cuda").manual_seed(int(task["seed"])),
                       callback_on_step_end=ctx.step_cb(steps))
        gen_s = time.time() - t1
        ctx.progress("encode", 1, 1)
        audio = out.audios[0].float().cpu().numpy()          # (channels, samples)
        Path(task["out"]).parent.mkdir(parents=True, exist_ok=True)
        sf.write(task["out"], np.ascontiguousarray(audio.T), pipe.sample_rate, subtype="PCM_16")
        return dict(file=task["out"], sample_rate=pipe.sample_rate, seconds=round(audio.shape[1] / pipe.sample_rate, 2),
                    steps=steps, load_seconds=round(load_s, 1), gen_seconds=round(gen_s, 1),
                    free_vram_at_start_gb=round(free0, 1), **io.vram_stats())


FAMILIES = {"wan": Wan, "a14b-t2v": lambda: A14B("t2v"), "a14b-i2v": lambda: A14B("i2v"),
            "echomimic": EchoMimic, "acestep": AceStep}


# ====================================================================== process loop
def status(fam: Family, busy) -> dict:
    try:
        vram = round(torch.cuda.memory_reserved() / 2**30, 2) if torch.cuda.is_initialized() else 0.0
    except Exception:
        vram = None
    return dict(family=fam.name, pid=os.getpid(), busy=busy, models=fam.status(), ram_gb=_rss_gb(), vram_gb=vram)


def run_task(fam: Family, jid, task, cancel: threading.Event) -> None:
    ctx = Ctx(jid, cancel)
    before = set(fam.models)
    try:
        res = fam.run(task, ctx)
        # warm = every model this job used was already in RAM (nothing new had to be loaded)
        res.setdefault("warm", bool(before) and not (set(fam.models) - before))
        res["models_loaded"] = sorted(set(fam.models) - before)
        res["process_ram_gb"] = _rss_gb()
        res["models_gb"] = round(sum(m.get("gb") or 0 for m in fam.models.values()), 2)
        res["offload_mode"] = OFFLOAD
        io.emit("RESULT", id=jid, **res)
    except Cancelled:
        io.emit("ERROR", id=jid, cancelled=True, error="cancelled")
    except torch.OutOfMemoryError as e:
        io.emit("ERROR", id=jid, error=f"CUDA out of memory: {str(e)[:300]}", oom=True)
    except Exception as e:
        traceback.print_exc()
        io.emit("ERROR", id=jid, error=f"{type(e).__name__}: {e}"[:600])
    finally:
        fam.release_gpu()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True, choices=list(FAMILIES))
    ap.add_argument("--once", help="run this task JSON file, print @@RESULT, exit")
    args = ap.parse_args()
    fam = FAMILIES[args.family]()
    if args.once:
        task = json.loads(Path(args.once).read_text(encoding="utf-8"))
        run_task(fam, task.get("id", "once"), task, threading.Event())
        return
    cmds: queue.Queue = queue.Queue()
    cancel = threading.Event()
    state = {"busy": None}

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def lines():
        conn, _ = srv.accept()
        srv.close()
        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                yield raw.decode("utf-8", "replace")

    def reader():
        for line in lines():
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except ValueError:
                continue
            op = c.get("op")
            if op == "cancel":
                if state["busy"] and (not c.get("id") or c.get("id") == state["busy"]):
                    cancel.set()
            elif op == "status":
                io.emit("STATUS", **status(fam, state["busy"]))
            else:
                cmds.put(c)
        cmds.put({"op": "quit"})          # connection closed: the studio went away

    threading.Thread(target=reader, daemon=True).start()
    io.emit("READY", port=port, **status(fam, None))
    while True:
        c = cmds.get()
        op = c.get("op")
        if op == "quit":
            break
        if op == "run":
            cancel.clear()
            state["busy"] = c.get("id")
            run_task(fam, c.get("id"), c.get("task") or {}, cancel)
            state["busy"] = None
            io.emit("STATUS", **status(fam, None))
        elif op == "release_gpu":
            fam.release_gpu()
            io.emit("STATUS", **status(fam, None))
        elif op == "drop":
            fam.drop(c.get("keys") or [])
            io.emit("STATUS", **status(fam, None))


if __name__ == "__main__":
    main()
