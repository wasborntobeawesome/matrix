"""ComfyUI workflow library: video/comfy-workflows/<id>/{workflow_api.json, manifest.json, preview.(jpg|png|webp)}.

A workflow is a ComfyUI graph in the "Export (API)" format ({node id: {class_type, inputs, _meta}}). Its manifest
says what the Studio may change before queueing it and where the result comes out:

    {"id", "name", "licence", "commercial", "notes", "capabilities": ["t2v", "i2v", "image", "upscale", "interpolate"],
     "inputs": {"prompt": [{"node": "6", "input": "text"}], "negative": [...], "seed": [...], "width": [...],
                "height": [...], "length": [...], "fps": [...], "steps": [...], "image": [...]},
     "outputs": ["58"],
     "presets": {"draft": {"width", "height", "length", "fps", "steps"?}, "final": {...}},   # portrait sizes;
                                                                                             # landscape = swapped
     "estimate": {"draft": {"seconds", "vram_gb"}, "final": {...}}, "min_vram_gb", "ram_gb",
     "models": [{"node", "input", "file", "folder"}], "source": "builtin" | "upload", "created", ...}

Each mapping entry is a list of {node, input} targets (one value can go into several nodes, e.g. the seed of
both samplers of a two-stage Wan 2.2 14B graph). `detect()` proposes a mapping from common core nodes;
`validate()` checks a graph + mapping against a running ComfyUI's /object_info (missing nodes, missing model
files, mapping errors); `fill()` makes the graph for one job.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
import time
from pathlib import Path

VIDEO = Path(__file__).resolve().parent.parent
WF = VIDEO / "comfy-workflows"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")
PREVIEWS = ("preview.jpg", "preview.png", "preview.webp")
KEYS = ("prompt", "negative", "seed", "width", "height", "length", "fps", "steps", "image")
INT_KEYS = ("seed", "width", "height", "length", "steps")
CAPS = ("t2v", "i2v", "image", "upscale", "interpolate")
SAMPLERS = ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced")
TEXT_ENCODERS = ("CLIPTextEncode", "TextEncodeHunyuanVideo_ImageToVideo", "CLIPTextEncodeFlux", "CLIPTextEncodeSD3")
LATENTS = ("EmptyHunyuanLatentVideo", "Wan22ImageToVideoLatent", "EmptyLatentImage", "EmptySD3LatentImage",
           "EmptyMochiLatentVideo", "EmptyLTXVLatentVideo", "EmptyCosmosLatentVideo", "WanImageToVideo",
           "WanFirstLastFrameToVideo", "WanVaceToVideo", "EmptyHunyuanImageLatent", "EmptyChromaRadianceLatentImage",
           "HunyuanImageToVideo", "LTXVImgToVideo", "CosmosImageToVideoLatent")
VIDEO_OUT = {"SaveVideo": "video", "SaveAnimatedWEBP": "webp", "SaveWEBM": "webm", "VHS_VideoCombine": "video",
             "SaveAnimatedPNG": "apng"}
IMAGE_OUT = ("SaveImage",)
FPS_NODES = {"CreateVideo": "fps", "SaveAnimatedWEBP": "fps", "SaveWEBM": "fps", "SaveAnimatedPNG": "fps",
             "VHS_VideoCombine": "frame_rate"}
MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft", ".onnx")
LOADER_FOLDERS = {"UNETLoader": {"unet_name": "diffusion_models"}, "CLIPLoader": {"clip_name": "text_encoders"},
                  "DualCLIPLoader": {"clip_name1": "text_encoders", "clip_name2": "text_encoders"},
                  "VAELoader": {"vae_name": "vae"}, "LoraLoaderModelOnly": {"lora_name": "loras"},
                  "LoraLoader": {"lora_name": "loras"}, "CheckpointLoaderSimple": {"ckpt_name": "checkpoints"},
                  "CLIPVisionLoader": {"clip_name": "clip_vision"}, "UpscaleModelLoader": {"model_name": "upscale_models"}}
_lock = threading.RLock()


class FlowError(Exception):
    def __init__(self, msg: str, status: int = 422, errors: list | None = None):
        super().__init__(msg)
        self.status = status
        self.errors = errors or [msg]


# ---------------------------------------------------------------- files
def folder(wid: str) -> Path:
    if not ID_RE.match(wid or ""):
        raise FlowError("bad workflow id", 404)
    return WF / wid


def exists(wid: str) -> bool:
    return bool(wid and ID_RE.match(wid) and (WF / wid / "workflow_api.json").is_file()
                and (WF / wid / "manifest.json").is_file())


def load(wid: str) -> tuple[dict, dict]:
    d = folder(wid)
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        g = json.loads((d / "workflow_api.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise FlowError(f"workflow '{wid}' not found or unreadable: {e}", 404) from None
    m["id"] = wid
    return m, g


def graph_sha(g: dict) -> str:
    return hashlib.sha1(json.dumps(g, sort_keys=True).encode()).hexdigest()[:12]


def preview_file(wid: str) -> Path | None:
    d = folder(wid)
    for n in PREVIEWS:
        if (d / n).is_file():
            return d / n
    return None


def list_all() -> list[dict]:
    out = []
    if WF.exists():
        for d in sorted(WF.iterdir()):
            if d.is_dir() and exists(d.name):
                try:
                    m, g = load(d.name)
                except FlowError:
                    continue
                out.append({**m, "nodes": len(g), "sha": graph_sha(g), "preview": bool(preview_file(d.name))})
    return sorted(out, key=lambda m: (m.get("source") != "builtin", m.get("created") or 0))


def _write(p: Path, d) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------- parsing
def parse(raw) -> dict:
    """Bytes/str/dict -> an API-format graph; a clear error for the UI ("Save") format."""
    if isinstance(raw, (bytes, str)):
        try:
            raw = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
        except (ValueError, UnicodeDecodeError) as e:
            raise FlowError(f"not valid JSON: {e}") from None
    if isinstance(raw, dict) and isinstance(raw.get("prompt"), dict) and "nodes" not in raw:
        raw = raw["prompt"]                       # {"prompt": {...}} as sent to POST /prompt
    if isinstance(raw, dict) and isinstance(raw.get("nodes"), list) and "links" in raw:
        raise FlowError("this is the ComfyUI *UI* format (Workflow → Save). Export it with Workflow → Export (API) "
                        "and upload that file instead.")
    if not isinstance(raw, dict) or not raw:
        raise FlowError("expected a ComfyUI API workflow: an object {node id: {class_type, inputs}}")
    g = {}
    for nid, n in raw.items():
        if not isinstance(n, dict) or not isinstance(n.get("class_type"), str) or not isinstance(n.get("inputs", {}), dict):
            raise FlowError(f"node '{nid}' is not an API-format node (needs class_type and inputs)")
        g[str(nid)] = {"class_type": n["class_type"], "inputs": n.get("inputs") or {},
                       **({"_meta": n["_meta"]} if isinstance(n.get("_meta"), dict) else {})}
    return g


def _link(v) -> tuple[str, int] | None:
    if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) and isinstance(v[0], (str, int)):
        return str(v[0]), v[1]
    return None


def _title(g: dict, nid: str) -> str:
    n = g.get(nid) or {}
    return (n.get("_meta") or {}).get("title") or n.get("class_type", "?")


# ---------------------------------------------------------------- detection
def _trace_text(g: dict, link, want: str, seen=None) -> list[str]:
    """Follow a conditioning link back to the text encoder node(s) that make it."""
    seen = seen or set()
    lk = _link(link)
    if not lk or lk[0] in seen or lk[0] not in g:
        return []
    nid, out = lk
    seen.add(nid)
    n = g[nid]
    ct, inp = n["class_type"], n.get("inputs") or {}
    if ct in TEXT_ENCODERS or (ct.startswith("CLIPTextEncode") and isinstance(inp.get("text"), str)):
        return [nid]
    if ct == "ConditioningZeroOut":
        return []
    # pass-through nodes with positive/negative pairs (WanImageToVideo, InstructPix2Pix…): out 0 = positive
    if "positive" in inp and "negative" in inp:
        key = "positive" if out == 0 else "negative" if out == 1 else want
        return _trace_text(g, inp.get(key), want, seen)
    res = []
    for k, v in inp.items():
        if k.startswith("conditioning") or k in ("cond", want):
            res += _trace_text(g, v, want, seen)
    return res


PRIMITIVES = ("PrimitiveInt", "PrimitiveFloat", "PrimitiveString", "PrimitiveStringMultiline", "PrimitiveNode",
              "PrimitiveBoolean")


def _val(g: dict, nid: str, k: str, types=(int, float), notes: list | None = None, key: str = ""):
    """Where the value of input `k` of node `nid` lives: (nid, k) when it is a widget value, or (primitive, "value")
    when it is linked to a Primitive* node (the newer ComfyUI templates drive steps, fps … from primitives)."""
    v = (g.get(nid) or {}).get("inputs", {}).get(k)
    if isinstance(v, types) and not isinstance(v, bool):
        return nid, k
    lk = _link(v)
    if lk and lk[0] in g:
        src = g[lk[0]]
        if src["class_type"] in PRIMITIVES and isinstance(src["inputs"].get("value"), types):
            return lk[0], "value"
        if notes is not None and key:
            notes.append(f"{key}: node {nid} ({g[nid]['class_type']}) gets '{k}' from node {lk[0]} "
                         f"({_title(g, lk[0])}) — not mapped, the workflow's own value is used")
    return None


def _targets(pairs) -> list[dict]:
    seen, out = set(), []
    for nid, inp in pairs:
        if (nid, inp) not in seen:
            seen.add((nid, inp))
            out.append({"node": nid, "input": inp})
    return out


def detect(g: dict) -> dict:
    """Propose an input mapping, outputs, capabilities and the model files from common core nodes."""
    notes: list[str] = []
    m: dict[str, list] = {k: [] for k in KEYS}
    samplers = [nid for nid, n in g.items() if n["class_type"] in SAMPLERS]
    guiders = [nid for nid, n in g.items() if n["class_type"] in ("CFGGuider", "BasicGuider", "DualCFGGuider")]
    pos, neg = [], []
    for nid in samplers + guiders:
        inp = g[nid]["inputs"]
        pos += _trace_text(g, inp.get("positive") or inp.get("conditioning") or inp.get("cond1"), "positive")
        neg += _trace_text(g, inp.get("negative"), "negative")
        lk = _link(inp.get("guider"))
        if lk and lk[0] in g:
            gi = g[lk[0]]["inputs"]
            pos += _trace_text(g, gi.get("positive") or gi.get("conditioning"), "positive")
            neg += _trace_text(g, gi.get("negative"), "negative")
    if not pos:
        enc = [nid for nid, n in g.items() if n["class_type"] in TEXT_ENCODERS]
        if len(enc) >= 1:
            pos = [enc[0]]
            notes.append(f"positive prompt: no sampler link found, guessed node {enc[0]} ({_title(g, enc[0])})")
        if len(enc) >= 2:
            neg = [enc[1]]
    pos = list(dict.fromkeys(pos))
    neg = [x for x in dict.fromkeys(neg) if x not in pos]
    m["prompt"] = _targets(t for t in (_val(g, nid, "text", (str,), notes, "prompt") for nid in pos) if t)
    m["negative"] = _targets(t for t in (_val(g, nid, "text", (str,)) for nid in neg) if t)
    # seed / steps
    seeds, steps = [], []
    for nid, n in g.items():
        inp, ct = n["inputs"], n["class_type"]
        for k in ("seed", "noise_seed"):
            if k in inp and (ct in SAMPLERS or ct == "RandomNoise"):
                t = _val(g, nid, k, (int,))
                if t:
                    seeds.append(t)
        if ct in ("KSampler", "SamplerCustom", "BasicScheduler") and "steps" in inp:
            t = _val(g, nid, "steps", (int,), notes, "steps")
            if t:
                steps.append(t)
    adv = [nid for nid in samplers if g[nid]["class_type"] == "KSamplerAdvanced"]
    if len(adv) == 1 and isinstance(g[adv[0]]["inputs"].get("steps"), int) and not steps:
        i = g[adv[0]]["inputs"]
        if i.get("start_at_step", 0) == 0 and i.get("end_at_step", 10000) >= i["steps"]:
            steps.append((adv[0], "steps"))
    elif len(adv) > 1:
        notes.append("steps not mapped: a multi-stage sampler (start/end steps per stage) keeps the workflow's own step split")
    m["seed"] = _targets(seeds)
    m = {k: _targets((t["node"], t["input"]) for t in v) if v and isinstance(v[0], dict) else v for k, v in m.items()}
    m["steps"] = _targets(steps) if len({_targets([s])[0]["node"] for s in steps}) <= 2 else []
    # size / length: the latent node(s) feeding a sampler, else any known latent node
    lat = [nid for nid, n in g.items() if n["class_type"] in LATENTS or
           (n["class_type"].startswith("Empty") and "Latent" in n["class_type"])]
    lat = [nid for nid in lat if "width" in g[nid]["inputs"]]
    for k in ("width", "height", "length"):
        alias = {"length": ("length", "frames", "num_frames", "video_length")}.get(k, (k,))
        for nid in lat:
            for a in alias:
                if a in g[nid]["inputs"]:
                    t = _val(g, nid, a, (int,), notes, k)
                    if t:
                        m[k].append({"node": t[0], "input": t[1]})
                    break
    if len(lat) > 1:
        notes.append(f"{len(lat)} latent nodes set the size: all are mapped")
    # fps
    for nid, n in g.items():
        k = FPS_NODES.get(n["class_type"])
        if k and k in n["inputs"]:
            t = _val(g, nid, k, (int, float), notes, "fps")
            if t:
                m["fps"].append({"node": t[0], "input": t[1]})
    # image input
    li = [nid for nid, n in g.items() if n["class_type"] in ("LoadImage", "LoadImageMask") and
          isinstance(n["inputs"].get("image"), str)]
    if li:
        m["image"] = [{"node": li[0], "input": "image"}]
        if len(li) > 1:
            notes.append(f"{len(li)} LoadImage nodes: only node {li[0]} is mapped to the input image")
    # outputs + capabilities
    outs = [nid for nid, n in g.items() if n["class_type"] in VIDEO_OUT]
    imgs = [nid for nid, n in g.items() if n["class_type"] in IMAGE_OUT]
    caps = []
    if outs:
        caps.append("i2v" if li else "t2v")
        if li and _image_optional(g, li[0]):
            caps.insert(0, "t2v")
    elif imgs:
        caps.append("image")
    if any(n["class_type"] in ("UpscaleModelLoader", "ImageUpscaleWithModel") for n in g.values()):
        caps.append("upscale")
    if any("interpol" in n["class_type"].lower() or "rife" in n["class_type"].lower() or "film" == n["class_type"].lower()
           for n in g.values()):
        caps.append("interpolate")
    if not outs and not imgs:
        notes.append("no output node found (SaveVideo, SaveAnimatedWEBP, SaveWEBM, VHS_VideoCombine or SaveImage)")
    for k in ("prompt", "seed"):
        if not m[k]:
            notes.append(f"'{k}' could not be detected — map it by hand")
    return {"inputs": {k: v for k, v in m.items() if v}, "outputs": outs or imgs, "capabilities": caps,
            "models": models_of(g), "notes": notes, "values": current_values(g, m)}


OPTIONAL_IMAGE_INPUTS = {"Wan22ImageToVideoLatent": "start_image", "WanImageToVideo": "start_image",
                         "WanFirstLastFrameToVideo": "start_image", "WanVaceToVideo": "reference_image"}


def _image_optional(g: dict, image_node: str, oi: dict | None = None) -> bool:
    """True when every input fed by the LoadImage node (directly or through image-only helper nodes) is optional."""
    users = _consumers(g, image_node)
    if not users:
        return True
    for nid, inp in users:
        ct = g[nid]["class_type"]
        if oi and ct in oi:
            if inp in ((oi[ct].get("input") or {}).get("required") or {}):
                # an image helper (resize, crop…) in between: look at its consumers instead
                if ct.startswith("Image") or "Resize" in ct or "Scale" in ct or "Crop" in ct:
                    if not _image_optional(g, nid, oi):
                        return False
                    continue
                return False
        elif OPTIONAL_IMAGE_INPUTS.get(ct) != inp:
            if ct.startswith("Image") or "Resize" in ct or "Scale" in ct:
                if not _image_optional(g, nid, oi):
                    return False
                continue
            return False
    return True


def _consumers(g: dict, nid: str) -> list[tuple[str, str]]:
    out = []
    for other, n in g.items():
        for k, v in (n.get("inputs") or {}).items():
            lk = _link(v)
            if lk and lk[0] == nid:
                out.append((other, k))
    return out


def models_of(g: dict, oi: dict | None = None) -> list[dict]:
    """Model files the graph loads: known loader inputs, plus (with object_info) any combo input whose
    value looks like a model file."""
    out = []
    for nid, n in g.items():
        ct, inp = n["class_type"], n["inputs"]
        known = LOADER_FOLDERS.get(ct, {})
        for k, v in inp.items():
            if not isinstance(v, str):
                continue
            if k in known or v.lower().endswith(MODEL_EXT):
                out.append({"node": nid, "class_type": ct, "input": k, "file": v, "folder": known.get(k)})
    return out


def current_values(g: dict, m: dict) -> dict:
    vals = {}
    for k, ts in m.items():
        for t in ts or []:
            v = (g.get(t["node"]) or {}).get("inputs", {}).get(t["input"])
            if v is not None and not _link(v):
                vals[k] = v
                break
    return vals


# ---------------------------------------------------------------- validation
def validate(g: dict, manifest: dict | None, oi: dict | None) -> dict:
    """Check a graph (+ its mapping) against /object_info. Without object_info only the mapping is checked."""
    res = {"ok": True, "checked": oi is not None, "missing_nodes": [], "missing_models": [], "custom_nodes": [],
           "mapping_errors": [], "warnings": []}
    mp = (manifest or {}).get("inputs") or {}
    for k, ts in mp.items():
        if k not in KEYS:
            res["mapping_errors"].append(f"unknown input '{k}'")
            continue
        for t in ts or []:
            n = g.get(str(t.get("node")))
            if not n:
                res["mapping_errors"].append(f"{k}: node {t.get('node')} does not exist")
                continue
            if t.get("input") not in n["inputs"]:
                res["mapping_errors"].append(f"{k}: node {t['node']} ({n['class_type']}) has no input '{t.get('input')}'")
            elif _link(n["inputs"][t["input"]]):
                res["mapping_errors"].append(f"{k}: node {t['node']} input '{t['input']}' is connected to another node "
                                             "(map the node that provides the value instead)")
    for o in (manifest or {}).get("outputs") or []:
        if str(o) not in g:
            res["mapping_errors"].append(f"output node {o} does not exist")
    if manifest is not None and not (manifest.get("outputs") or []):
        res["mapping_errors"].append("no output node")
    if manifest is not None and not mp.get("prompt") and set(manifest.get("capabilities") or []) & {"t2v", "i2v", "image"}:
        res["warnings"].append("no prompt mapping: every job would use the workflow's own prompt")
    if oi is not None:
        for nid, n in g.items():
            ct = n["class_type"]
            spec = oi.get(ct)
            if spec is None:
                if ct not in res["missing_nodes"]:
                    res["missing_nodes"].append(ct)
                continue
            mod = str(spec.get("python_module") or "")
            if mod.startswith("custom_nodes"):
                pack = mod.split(".")[1] if "." in mod else mod
                if not any(c["node"] == ct for c in res["custom_nodes"]):
                    res["custom_nodes"].append({"node": ct, "pack": pack})
            req = (spec.get("input") or {}).get("required") or {}
            opt = (spec.get("input") or {}).get("optional") or {}
            for k, v in n["inputs"].items():
                s = req.get(k) or opt.get(k)
                if s is None or _link(v):
                    continue
                opts = _combo_options(s)
                if opts is not None and isinstance(v, str) and v not in opts:
                    if v.lower().endswith(MODEL_EXT) or k in LOADER_FOLDERS.get(ct, {}):
                        res["missing_models"].append({"node": nid, "class_type": ct, "input": k, "file": v,
                                                      "folder": LOADER_FOLDERS.get(ct, {}).get(k) or _guess_folder(k)})
                    elif k == "image" and ct in ("LoadImage", "LoadImageMask"):
                        pass                       # replaced per job (upload) or dropped
                    else:
                        res["warnings"].append(f"node {nid} ({ct}): '{k}' = '{v}' is not one of its options")
            for k in req:
                if k not in n["inputs"] and not any(x.startswith(k + ".") for x in n["inputs"]):
                    res["warnings"].append(f"node {nid} ({ct}): required input '{k}' is missing")
    res["ok"] = not (res["missing_nodes"] or res["missing_models"] or res["mapping_errors"])
    return res


def _combo_options(spec) -> list | None:
    if isinstance(spec, list) and spec:
        if isinstance(spec[0], list):
            return spec[0]
        if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
            return spec[1].get("options")
    return None


def _guess_folder(k: str) -> str | None:
    k = k.lower()
    for key, f in (("lora", "loras"), ("vae", "vae"), ("clip", "text_encoders"), ("unet", "diffusion_models"),
                   ("ckpt", "checkpoints"), ("upscale", "upscale_models")):
        if key in k:
            return f
    return None


# ---------------------------------------------------------------- per-job graph
def fill(g: dict, manifest: dict, values: dict, prefix: str) -> dict:
    """A copy of the graph with the job's values put into the mapped inputs. values: prompt, negative, seed,
    width, height, length, fps, steps, image (an uploaded ComfyUI input name, or None = text-to-video)."""
    g = copy.deepcopy(g)
    mp = manifest.get("inputs") or {}
    for k in KEYS:
        if k == "image" or values.get(k) is None:
            continue
        v = values[k]
        if k in INT_KEYS:
            v = int(v)
        elif k == "fps":
            v = float(v)
        for t in mp.get(k) or []:
            n = g.get(str(t["node"]))
            if n is not None:
                old = n["inputs"].get(t["input"])
                n["inputs"][t["input"]] = int(v) if k == "fps" and isinstance(old, int) else v
    img_t = mp.get("image") or []
    if values.get("image"):
        if not img_t:
            raise FlowError("this workflow has no image input mapped (needed for image-to-video)")
        for t in img_t:
            g[str(t["node"])]["inputs"][t["input"]] = values["image"]
    else:
        for t in img_t:
            drop_image(g, str(t["node"]), manifest)
    for o in manifest.get("outputs") or []:
        n = g.get(str(o))
        if n and isinstance(n["inputs"].get("filename_prefix"), str):
            n["inputs"]["filename_prefix"] = prefix
    return g


def drop_image(g: dict, nid: str, manifest: dict) -> None:
    """Text-to-video with an image-to-video graph: remove the LoadImage node (and image-only helpers in
    between) and disconnect the optional inputs it fed."""
    if nid not in g:
        return
    if "t2v" not in (manifest.get("capabilities") or []):
        raise FlowError("this workflow needs an input image (image-to-video only)")
    stack, gone = [nid], set()
    while stack:
        cur = stack.pop()
        if cur in gone:
            continue
        gone.add(cur)
        for other, k in _consumers(g, cur):
            ct = g[other]["class_type"]
            if OPTIONAL_IMAGE_INPUTS.get(ct) == k or k in ("start_image", "end_image", "reference_image", "clip_vision_output"):
                g[other]["inputs"].pop(k, None)
            elif ct.startswith("Image") or "Resize" in ct or "Scale" in ct or ct == "CLIPVisionEncode":
                stack.append(other)
            else:
                g[other]["inputs"].pop(k, None)
    for x in gone:
        g.pop(x, None)


# ---------------------------------------------------------------- import / edit
def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:36]
    return s if len(s) >= 2 else "workflow"


def new_manifest(g: dict, meta: dict, det: dict | None = None) -> dict:
    det = det or detect(g)
    v = det.get("values") or {}
    w, h = int(v.get("width") or 480), int(v.get("height") or 832)
    if w > h:
        w, h = h, w                           # presets are stored as portrait sizes
    length, fps = int(v.get("length") or 81), float(v.get("fps") or 16)
    base = {"width": w, "height": h, "length": length, "fps": fps}
    if v.get("steps"):
        base["steps"] = int(v["steps"])
    return {"name": (meta.get("name") or "Uploaded workflow")[:80], "licence": (meta.get("licence") or "").strip()[:200],
            "commercial": bool(meta.get("commercial")), "notes": (meta.get("notes") or "")[:2000],
            "capabilities": det["capabilities"], "inputs": det["inputs"], "outputs": det["outputs"],
            "presets": {"draft": dict(base), "final": dict(base)},
            "estimate": {"draft": {"seconds": None, "vram_gb": None}, "final": {"seconds": None, "vram_gb": None}},
            "min_vram_gb": 6.0, "ram_gb": None, "models": det["models"], "detect_notes": det["notes"],
            "source": meta.get("source") or "upload", "created": time.time(), "speed": "medium"}


def save(g: dict, manifest: dict, wid: str | None = None, replace: bool = False, preview: bytes | None = None,
         preview_ext: str = ".jpg") -> dict:
    with _lock:
        wid = wid or _slug(manifest.get("name") or "workflow")
        if not ID_RE.match(wid):
            raise FlowError("workflow id: 2-41 characters a-z, 0-9, - or _")
        d = WF / wid
        if d.exists() and not replace:
            base, i = wid, 2
            while (WF / f"{base[:36]}-{i}").exists():
                i += 1
            wid = f"{base[:36]}-{i}"
            d = WF / wid
        d.mkdir(parents=True, exist_ok=True)
        manifest = {**manifest, "id": wid}
        _write(d / "workflow_api.json", g)
        _write(d / "manifest.json", manifest)
        if preview:
            for n in PREVIEWS:
                (d / n).unlink(missing_ok=True)
            (d / ("preview" + (preview_ext if preview_ext in (".jpg", ".png", ".webp") else ".jpg"))).write_bytes(preview)
    return manifest


def update(wid: str, patch: dict) -> dict:
    m, g = load(wid)
    errs = []
    if "name" in patch:
        m["name"] = str(patch["name"] or m["name"]).strip()[:80] or m["name"]
    for k in ("licence", "notes"):
        if k in patch:
            m[k] = str(patch[k] or "").strip()[:2000 if k == "notes" else 200]
    if "commercial" in patch:
        m["commercial"] = bool(patch["commercial"])
    if "capabilities" in patch:
        caps = [c for c in patch["capabilities"] or [] if c in CAPS]
        m["capabilities"] = caps
    if "inputs" in patch:
        mp = {}
        for k, ts in (patch["inputs"] or {}).items():
            if k not in KEYS:
                errs.append(f"unknown input '{k}'")
                continue
            if isinstance(ts, dict):
                ts = [ts]
            lst = []
            for t in ts or []:
                if not isinstance(t, dict) or not str(t.get("node") or "").strip() or not str(t.get("input") or "").strip():
                    continue
                lst.append({"node": str(t["node"]).strip(), "input": str(t["input"]).strip()})
            if lst:
                mp[k] = lst
        m["inputs"] = mp
    if "outputs" in patch:
        m["outputs"] = [str(x).strip() for x in patch["outputs"] or [] if str(x).strip()]
    if "presets" in patch:
        pr = {}
        for name in ("draft", "final"):
            src = (patch["presets"] or {}).get(name) or m.get("presets", {}).get(name) or {}
            p = {}
            for k in ("width", "height", "length", "steps"):
                if src.get(k) not in (None, ""):
                    p[k] = int(float(src[k]))
            if src.get("fps") not in (None, ""):
                p["fps"] = float(src["fps"])
            if p.get("width") and p.get("height") and p["width"] > p["height"]:
                p["width"], p["height"] = p["height"], p["width"]
            for k, lo, hi in (("width", 64, 4096), ("height", 64, 4096), ("length", 1, 1000), ("steps", 1, 200)):
                if k in p and not lo <= p[k] <= hi:
                    errs.append(f"{name}.{k} must be {lo}–{hi}")
            pr[name] = p
        m["presets"] = pr
    for k in ("min_vram_gb", "ram_gb"):
        if k in patch and patch[k] not in (None, ""):
            m[k] = round(float(patch[k]), 1)
    if "estimate" in patch and isinstance(patch["estimate"], dict):
        m["estimate"] = patch["estimate"]
    if errs:
        raise FlowError("; ".join(errs), errors=errs)
    v = validate(g, m, None)
    if v["mapping_errors"]:
        raise FlowError("; ".join(v["mapping_errors"]), errors=v["mapping_errors"])
    m["updated"] = time.time()
    _write(folder(wid) / "manifest.json", m)
    return m


def set_preview(wid: str, src: Path) -> None:
    d = folder(wid)
    for n in PREVIEWS:
        (d / n).unlink(missing_ok=True)
    shutil.copy2(src, d / ("preview" + src.suffix.lower()))


def delete(wid: str) -> None:
    m, _ = load(wid)
    if m.get("source") == "builtin":
        raise FlowError("the sample workflows that come with the Studio cannot be deleted", 409)
    with _lock:
        shutil.rmtree(folder(wid), ignore_errors=True)


def nodes_view(g: dict) -> list[dict]:
    """Compact node list for the mapping editor: id, class, title, scalar inputs."""
    out = []
    for nid, n in sorted(g.items(), key=lambda kv: (len(kv[0]), kv[0])):
        out.append({"id": nid, "class_type": n["class_type"], "title": _title(g, nid),
                    "inputs": {k: v for k, v in n["inputs"].items() if not _link(v) and not isinstance(v, (dict, list))}})
    return out
