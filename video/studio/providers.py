"""B-roll video generators behind one interface (local, free models only).

Every provider has: id, name, licence (+ caveats), capabilities (t2v / i2v, resolutions per preset, max
seconds, fps), VRAM / RAM needs, `estimate()` and `generate()` (= the AI queue job it becomes).
Generation itself runs in the warm worker of the provider's model family (video/ai/worker.py).

    wan22-5b    Wan2.2-TI2V-5B (the original b-roll model, unchanged)        Apache-2.0
    fastwan-5b  FastWan2.2-TI2V-5B, 3-step DMD distillation — fast drafts      Apache-2.0
    wan22-a14b  Wan2.2-T2V/I2V-A14B + Wan2.2-Lightning 4-step LoRA — quality   Apache-2.0

A paid cloud API could be added as one more provider (see `CloudProvider` below and the README); none is
implemented or called.
"""
from __future__ import annotations

import hashlib
import json

DEFAULT = "wan22-5b"

PROVIDERS: dict[str, dict] = {
    "wan22-5b": {
        "id": "wan22-5b", "name": "Wan2.2 TI2V 5B", "vendor": "Wan-AI (Alibaba)",
        "model": "Wan2.2-TI2V-5B (Wan-AI)", "licence": "Apache-2.0", "commercial": True,
        "licence_note": "Weights, code and the UMT5 text encoder are Apache-2.0. Model card: you are responsible for "
                        "generated content (no illegal or harmful use).",
        "family": {"t2v": "wan", "i2v": "wan"}, "modes": ["t2v", "i2v"],
        "presets": {
            "draft": {"steps": 20, "frames": 81, "fps": 24, "guidance": 5.0, "portrait": (480, 832), "landscape": (832, 480)},
            "final": {"steps": 30, "frames": 121, "fps": 24, "guidance": 5.0, "portrait": (704, 1280), "landscape": (1280, 704)},
        },
        "max_seconds": 5.0, "vram_gb": 7.7, "ram_gb": 36.0,
        "summary": "The original b-roll model. Good for dark, abstract or product-style backgrounds.",
        "speed": "medium",
    },
    "fastwan-5b": {
        "id": "fastwan-5b", "name": "FastWan2.2 5B (fast drafts)", "vendor": "FastVideo / Hao AI Lab",
        "model": "FastWan2.2-TI2V-5B-FullAttn (FastVideo), DMD-distilled from Wan2.2-TI2V-5B", "licence": "Apache-2.0",
        "commercial": True,
        "licence_note": "Apache-2.0 fine-tune of Wan2.2-TI2V-5B (Apache-2.0); same UMT5 encoder and VAE.",
        "family": {"t2v": "wan", "i2v": "wan"}, "modes": ["t2v", "i2v"],
        "presets": {
            "draft": {"steps": 3, "frames": 81, "fps": 24, "guidance": 1.0, "portrait": (480, 832), "landscape": (832, 480)},
            "final": {"steps": 3, "frames": 121, "fps": 24, "guidance": 1.0, "portrait": (704, 1280), "landscape": (1280, 704)},
        },
        "max_seconds": 5.0, "vram_gb": 11.0, "ram_gb": 36.0,
        "summary": "3 denoising steps instead of 20–30: a clip in well under a minute. For quick drafts and ideas.",
        "speed": "fast",
    },
    "wan22-a14b": {
        "id": "wan22-a14b", "name": "Wan2.2 A14B + Lightning (quality)", "vendor": "Wan-AI (Alibaba) + LightX2V",
        "model": "Wan2.2-T2V-A14B / I2V-A14B (Wan-AI) with the Wan2.2-Lightning 4-step LoRAs (lightx2v)",
        "licence": "Apache-2.0", "commercial": True,
        "licence_note": "Both 14B experts, the UMT5 encoder, the Wan2.1 VAE and the Lightning LoRAs are Apache-2.0.",
        "family": {"t2v": "a14b-t2v", "i2v": "a14b-i2v"}, "modes": ["t2v", "i2v"],
        "presets": {
            "draft": {"steps": 4, "frames": 81, "fps": 16, "guidance": 1.0, "portrait": (480, 832), "landscape": (832, 480)},
            "final": {"steps": 4, "frames": 81, "fps": 16, "guidance": 1.0, "portrait": (720, 1280), "landscape": (1280, 720)},
        },
        "max_seconds": 5.06, "vram_gb": 11.2, "ram_gb": 68.0,
        "summary": "Two 14B experts (27B total): the best motion and detail here. Needs ~70 GB of RAM while warm.",
        "speed": "slow",
    },
}

# Measured on the RTX 5070 Ti with Ovoz unloaded; replaced by real job timings (ai-timings.json).
# load = model load when cold, per_unit = seconds per step, tail = VAE decode + encode + library.
TIME_DEFAULTS = {
    "broll/fastwan-5b/draft": {"load": 25, "per_unit": 2.2, "tail": 12},
    "broll/fastwan-5b/final": {"load": 40, "per_unit": 9.0, "tail": 110},
    "broll/wan22-a14b/draft": {"load": 85, "per_unit": 36.5, "tail": 25},
    "broll/wan22-a14b/final": {"load": 85, "per_unit": 100.0, "tail": 70},
}
NEED_GB = {"broll/fastwan-5b/draft": 7.0, "broll/fastwan-5b/final": 10.5,
           "broll/wan22-a14b/draft": 9.0, "broll/wan22-a14b/final": 12.0}


def get(pid: str | None) -> dict:
    return PROVIDERS.get(pid or DEFAULT) or PROVIDERS[DEFAULT]


def exists(pid: str | None) -> bool:
    return bool(pid) and pid in PROVIDERS


def timing_key(provider: str, preset: str) -> str:
    """The original model keeps its old key so measured timings carry over."""
    return f"broll/{preset}" if provider == DEFAULT else f"broll/{provider}/{preset}"


def size(provider: str, preset: str, orientation: str) -> tuple[int, int]:
    p = get(provider)["presets"][preset]
    return tuple(p[orientation if orientation in ("portrait", "landscape") else "portrait"])


def clip_key(provider: str, prompt: str, seed: int, preset: str, orientation: str, image_sha: str | None = None) -> str:
    """Library id. wan22-5b text-to-video keeps the old hash (existing clips stay cached)."""
    import library as L
    pr = get(provider)["presets"][preset]
    w, h = size(provider, preset, orientation)
    if provider == DEFAULT and not image_sha:
        return L.broll_key(prompt, seed, pr["steps"], w, h, pr["frames"])
    raw = json.dumps([provider, prompt.strip(), int(seed), pr["steps"], f"{w}x{h}x{pr['frames']}", image_sha])
    return "b-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def public(ai=None) -> list[dict]:
    """For the UI / API: metadata + per-preset estimates."""
    out = []
    for p in PROVIDERS.values():
        d = {k: v for k, v in p.items() if k != "family"}
        d["presets"] = {}
        for name, pr in p["presets"].items():
            d["presets"][name] = {**{k: v for k, v in pr.items()}, "seconds": round(pr["frames"] / pr["fps"], 2)}
            if ai is not None:
                d["presets"][name].update(estimate=ai.estimate_broll(p["id"], name),
                                          need_gb=ai.need_gb_key(timing_key(p["id"], name)),
                                          measured=bool(ai.timings.get(timing_key(p["id"], name), {}).get("runs")))
        out.append(d)
    return out


class CloudProvider:
    """Sketch only — DISABLED. How a paid cloud video API could plug in later (none is implemented or called).

    A cloud provider would set `family = None`, implement `generate(task) -> mp4 path` with an HTTP client
    (API key from an env var, never stored in projects), report `estimate()` from the service's queue time,
    `vram_gb = 0`, and its own licence/terms text so the UI can show them next to the local models. The AI
    queue would run it without the GPU gate. See video/studio/README.md ("Cloud providers").
    """
    enabled = False
