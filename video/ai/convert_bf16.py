"""Convert a diffusers model folder's fp32 safetensors shards to bf16, one tensor at a time.

    .venv/Scripts/python convert_bf16.py .models/Wan2.2-T2V-A14B-Diffusers/transformer .models/bf16/Wan2.2-T2V-A14B/transformer

Why: the Wan2.2 repos ship the transformers in fp32 (A14B: 2 x 57 GB). Loading them with torch_dtype=bf16
reads every fp32 shard into RAM first and converts afterwards, so one A14B expert peaked at ~85 GB and the
two experts ran a 128 GB machine out of memory. The bf16 copy loads at its real size (28.6 GB per expert),
loads faster, and is what the warm worker keeps in RAM. Peak RAM while converting = one tensor + one 5 GB
output shard. The weights are unchanged except for the dtype (the same rounding torch_dtype=bf16 does).
"""
import json
import shutil
import sys
import time
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import save_file

MAX_SHARD = 5 * 2**30


def convert(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.suffix == ".json" and "index" not in f.name:
            shutil.copy2(f, dst / f.name)
    files = sorted(src.glob("*.safetensors"))
    weight_map, shard, size, n = {}, {}, 0, 0
    total = 0

    def flush():
        nonlocal shard, size, n
        if not shard:
            return
        n += 1
        name = f"diffusion_pytorch_model-{n:05d}.safetensors"
        save_file(shard, str(dst / name), metadata={"format": "pt"})
        for k in shard:
            weight_map[k] = name
        shard, size = {}, 0

    t0 = time.time()
    for f in files:
        with safe_open(str(f), framework="pt") as sf:
            for k in sf.keys():
                t = sf.get_tensor(k)
                if t.is_floating_point():
                    t = t.to(torch.bfloat16)
                b = t.numel() * t.element_size()
                if size + b > MAX_SHARD:
                    flush()
                shard[k] = t.contiguous()
                size += b
                total += b
        print(f"{f.name} done ({time.time() - t0:.0f} s)", flush=True)
    flush()
    # rename to the "-of-N" pattern diffusers expects
    final = {}
    for i in range(1, n + 1):
        old = dst / f"diffusion_pytorch_model-{i:05d}.safetensors"
        new = dst / f"diffusion_pytorch_model-{i:05d}-of-{n:05d}.safetensors"
        old.replace(new)
        final[old.name] = new.name
    weight_map = {k: final[v] for k, v in weight_map.items()}
    (dst / "diffusion_pytorch_model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": total}, "weight_map": weight_map}, indent=1))
    print(f"wrote {n} shards, {total / 2**30:.1f} GB, in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
