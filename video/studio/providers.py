"""B-roll video generators behind one interface (local, free models only).

Every provider has: id, name, licence (+ caveats), capabilities (t2v / i2v, resolutions per preset, max
seconds, fps), VRAM / RAM needs, `estimate()` and `generate()` (= the AI queue job it becomes).
Generation itself runs in the warm worker of the provider's model family (video/ai/worker.py).

    fastwan-5b  FastWan2.2-TI2V-5B, 3-step DMD distillation — the default    Apache-2.0
    wan22-5b    Wan2.2-TI2V-5B (the original b-roll model; legacy: its draft-size clips came out near-black)
                                                                              Apache-2.0
    wan22-a14b  Wan2.2-T2V/I2V-A14B + Wan2.2-Lightning 4-step LoRA — quality   Apache-2.0

    comfy:<id>  every workflow in video/comfy-workflows/<id>/ (comfyflows.py), run by a local ComfyUI over its
                HTTP API (comfy.py); licence and capabilities come from the workflow's manifest.json

A paid cloud API could be added as one more provider (see `CloudProvider` below and the README); none is
implemented or called.
"""
from __future__ import annotations

import hashlib
import json
import time

DEFAULT = "fastwan-5b"     # default video generator (drafts and finals)
ORIGINAL = "wan22-5b"      # the first b-roll model: its timing / clip-cache keys keep their old format
LEGACY = {"wan22-5b"}      # still selectable, marked "legacy" in the UI
COMFY = "comfy:"

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
        "summary": "The original b-roll model (legacy): at draft size its clips often come out nearly black — "
                   "use FastWan for drafts.",
        "speed": "medium", "legacy": True,
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


def is_comfy(pid: str | None) -> bool:
    return bool(pid) and str(pid).startswith(COMFY)


_cache: tuple[float, dict] = (0.0, {})


def comfy_providers() -> dict[str, dict]:
    """One provider per ComfyUI workflow in video/comfy-workflows (re-read at most every 2 s)."""
    global _cache
    if time.time() - _cache[0] < 2.0:
        return _cache[1]
    import comfyflows as F
    out = {}
    for m in F.list_all():
        try:
            _, g = F.load(m["id"])
            out[COMFY + m["id"]] = _comfy_entry(m, g)
        except Exception:  # noqa: BLE001 - a broken workflow must not break the provider list
            continue
    _cache = (time.time(), out)
    return out


def _comfy_entry(m: dict, g: dict) -> dict:
    import comfyflows as F
    gsteps = sum(x for x in (_steps_of(n) for n in g.values() if n.get("class_type") in F.SAMPLERS) if x) or None
    presets = {}
    for name in ("draft", "final"):
        pr = (m.get("presets") or {}).get(name) or (m.get("presets") or {}).get("draft") or {}
        w, h = int(pr.get("width") or 480), int(pr.get("height") or 832)
        presets[name] = {"steps": int(pr["steps"]) if pr.get("steps") else gsteps, "frames": int(pr.get("length") or 81),
                         "fps": float(pr.get("fps") or 16), "guidance": None, "portrait": (w, h), "landscape": (h, w)}
    caps = m.get("capabilities") or []
    modes = [c for c in ("t2v", "i2v") if c in caps]
    est = m.get("estimate") or {}
    return {"id": COMFY + m["id"], "workflow": m["id"], "comfy": True, "name": m.get("name") or m["id"],
            "vendor": "ComfyUI workflow" + (" (sample)" if m.get("source") == "builtin" else " (uploaded)"),
            "model": ", ".join(x.get("file", "") for x in (m.get("models") or [])[:4]) or "see the workflow",
            "licence": m.get("licence") or "unknown — set it in the workflow editor",
            "commercial": bool(m.get("commercial")), "licence_note": m.get("licence_note") or m.get("notes") or "",
            "family": {"t2v": "comfy", "i2v": "comfy"}, "modes": modes or ["t2v"], "capabilities": caps,
            "presets": presets, "max_seconds": round(max(p["frames"] / p["fps"] for p in presets.values()), 2),
            "vram_gb": float((est.get("final") or {}).get("vram_gb") or m.get("min_vram_gb") or 8),
            "min_vram_gb": float(m.get("min_vram_gb") or 6), "ram_gb": float(m.get("ram_gb") or 16),
            "summary": (m.get("notes") or "")[:300], "speed": m.get("speed") or "medium",
            "estimate_seed": est, "source": m.get("source")}


def _steps_of(n: dict) -> int | None:
    i = n.get("inputs") or {}
    st = i.get("steps")
    if not isinstance(st, int):
        return None
    if n.get("class_type") == "KSamplerAdvanced":
        a, b = i.get("start_at_step", 0), i.get("end_at_step", st)
        if isinstance(a, int) and isinstance(b, int):
            return max(0, min(b, st) - a)
    return st


def all_providers() -> dict[str, dict]:
    return {**PROVIDERS, **comfy_providers()}


def get(pid: str | None) -> dict:
    if is_comfy(pid):
        c = comfy_providers().get(pid)
        if c:
            return c
    return PROVIDERS.get(pid or DEFAULT) or PROVIDERS[DEFAULT]


def default_for(preset: str | None = None) -> str:
    """The generator used when nothing is chosen (FastWan for drafts and finals; wan22-5b is legacy)."""
    return DEFAULT


def exists(pid: str | None) -> bool:
    return bool(pid) and (pid in PROVIDERS or (is_comfy(pid) and pid in comfy_providers()))


def timing_key(provider: str, preset: str) -> str:
    """The original model keeps its old key so measured timings carry over."""
    return f"broll/{preset}" if provider == ORIGINAL else f"broll/{provider}/{preset}"


def size(provider: str, preset: str, orientation: str) -> tuple[int, int]:
    p = get(provider)["presets"][preset]
    return tuple(p[orientation if orientation in ("portrait", "landscape") else "portrait"])


def clip_key(provider: str, prompt: str, seed: int, preset: str, orientation: str, image_sha: str | None = None) -> str:
    """Library id. wan22-5b text-to-video keeps the old hash (existing clips stay cached)."""
    import library as L
    pr = get(provider)["presets"][preset]
    w, h = size(provider, preset, orientation)
    if provider == ORIGINAL and not image_sha:
        return L.broll_key(prompt, seed, pr["steps"], w, h, pr["frames"])
    if is_comfy(provider):                 # the workflow's graph is part of the key: editing it makes new clips
        import comfyflows as F
        try:
            _, g = F.load(provider[len(COMFY):])
            gsha = F.graph_sha(g)
        except F.FlowError:
            gsha = None
        raw = json.dumps([provider, prompt.strip(), int(seed), pr["steps"], f"{w}x{h}x{pr['frames']}@{pr['fps']}",
                          image_sha, gsha])
        return "b-" + hashlib.sha1(raw.encode()).hexdigest()[:10]
    raw = json.dumps([provider, prompt.strip(), int(seed), pr["steps"], f"{w}x{h}x{pr['frames']}", image_sha])
    return "b-" + hashlib.sha1(raw.encode()).hexdigest()[:10]


def public(ai=None) -> list[dict]:
    """For the UI / API: metadata + per-preset estimates."""
    out = []
    for p in all_providers().values():
        d = {k: v for k, v in p.items() if k not in ("family", "estimate_seed")}
        d["presets"] = {}
        for name, pr in p["presets"].items():
            d["presets"][name] = {**{k: v for k, v in pr.items()}, "seconds": round(pr["frames"] / pr["fps"], 2)}
            if ai is not None:
                d["presets"][name].update(estimate=ai.estimate_broll(p["id"], name),
                                          need_gb=p["min_vram_gb"] if p.get("comfy") else
                                          ai.need_gb_key(timing_key(p["id"], name)),
                                          measured=bool(ai.timings.get(timing_key(p["id"], name), {}).get("runs")))
        out.append(d)
    out.sort(key=lambda d: bool(d.get("legacy")))      # legacy generators last in the pickers
    return out


class CloudProvider:
    """Sketch only — DISABLED. How a paid cloud video API could plug in later (none is implemented or called).

    A cloud provider would set `family = None`, implement `generate(task) -> mp4 path` with an HTTP client
    (API key from an env var, never stored in projects), report `estimate()` from the service's queue time,
    `vram_gb = 0`, and its own licence/terms text so the UI can show them next to the local models. The AI
    queue would run it without the GPU gate. See video/studio/README.md ("Cloud providers").
    """
    enabled = False
