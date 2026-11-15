"""Install or update a local ComfyUI for Video Studio, reproducibly, at a pinned version.

    py -3.12 video/comfyui/setup.py                 # ComfyUI + venv + the models of both sample workflows
    py -3.12 video/comfyui/setup.py --models draft  # only the Wan2.2 TI2V-5B draft models (~19 GB)
    py -3.12 video/comfyui/setup.py --models none   # code + venv only
    py -3.12 video/comfyui/setup.py --check         # report what is installed / missing, change nothing

Everything lands in video/comfyui/ (git-ignored except this script, extra_model_paths.yaml and README):

    ComfyUI/      git checkout of github.com/comfyanonymous/ComfyUI at COMFY_TAG (core nodes only — no
                  ComfyUI-Manager, no custom nodes; custom_nodes/ stays empty)
    .venv/        Python 3.12 + torch cu128 (RTX 50xx / Blackwell sm_120 needs CUDA >= 12.8)
    models/       single-file checkpoints, outside the checkout so a re-install or update keeps them;
                  ComfyUI finds them through extra_model_paths.yaml (--extra-model-paths-config)
    input/ output/ temp/ user/   ComfyUI's working folders (the Studio passes them on the command line)

Model files are the Comfy-Org repackaged Wan 2.2 files on Hugging Face (Apache-2.0). Downloads resume
(.part files) and are checked against their SHA-256. The lightx2v 4-step LoRAs are byte-identical to the
ones the Studio's own A14B worker already has under video/ai/.models/Wan2.2-Lightning: when those exist
(same SHA-256) they are hard-linked instead of downloaded.

ComfyUI is GPL-3.0. The Studio never imports it: it starts it as a separate program and talks to it over
its HTTP API only (see video/comfy-workflows/README.md, "Licences").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMFY = HERE / "ComfyUI"
VENV = HERE / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
MODELS = HERE / "models"
AI_MODELS = HERE.parent / "ai" / ".models"

COMFY_REPO = "https://github.com/comfyanonymous/ComfyUI.git"
COMFY_TAG = "v0.45.3"
TORCH = ["torch==2.8.0", "torchvision==0.23.0", "torchaudio==2.8.0"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"

HF = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/"
HF_API = "https://huggingface.co/api/models/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/tree/main/split_files/"
# (folder, file name, size in bytes, sha256, used by, hard-link source under video/ai/.models)
FILES = [
    ("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors", None, None, {"draft"}, None),
    ("vae", "wan2.2_vae.safetensors", None, None, {"draft"}, None),
    ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors", None, None, {"draft", "quality"}, None),
    ("diffusion_models", "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors", None, None, {"quality"}, None),
    ("diffusion_models", "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors", None, None, {"quality"}, None),
    ("vae", "wan_2.1_vae.safetensors", None, None, {"quality"}, None),
    ("loras", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors", 1226977424,
     "698321cb86bd30c4af06c9b84e656a1048c8cb54e06d50694536fb5de37fde41", {"quality"},
     "Wan2.2-Lightning/Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V1.1/high_noise_model.safetensors"),
    ("loras", "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors", 1226977424,
     "ec95216e614b3c132c11bfb387b11feedf62163150ccc9068bca8a189771e75a", {"quality"},
     "Wan2.2-Lightning/Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V1.1/low_noise_model.safetensors"),
]
MODEL_DIRS = ["diffusion_models", "text_encoders", "vae", "loras", "checkpoints", "clip_vision", "upscale_models",
              "unet", "clip"]


def log(*a) -> None:
    print("[comfyui-setup]", *a, flush=True)


def run(cmd: list, **kw) -> None:
    log("$", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


# ---------------------------------------------------------------- code
def install_code() -> None:
    if not (COMFY / ".git").exists():
        run(["git", "clone", "--depth", "1", "--branch", COMFY_TAG, COMFY_REPO, COMFY])
        return
    cur = subprocess.run(["git", "-C", COMFY, "describe", "--tags", "--exact-match"], capture_output=True,
                         text=True).stdout.strip()
    if cur == COMFY_TAG:
        log(f"ComfyUI {COMFY_TAG} already checked out")
        return
    log(f"updating ComfyUI {cur or '(untagged)'} -> {COMFY_TAG}")
    run(["git", "-C", COMFY, "fetch", "--depth", "1", "origin", "tag", COMFY_TAG])
    run(["git", "-C", COMFY, "checkout", "--force", COMFY_TAG])


def install_venv(python: str | None, templates: str = "light") -> None:
    if not PY.exists():
        exe = python or _find_py312()
        run([exe, "-m", "venv", VENV])
    out = subprocess.run([PY, "-c", "import sys;print(sys.version_info[:2])"], capture_output=True, text=True).stdout
    if "(3, 12)" not in out:
        log(f"warning: the venv runs Python {out.strip()}, tested with 3.12")
    run([PY, "-m", "pip", "install", "--upgrade", "pip", "wheel"])
    have = subprocess.run([PY, "-c", "import torch;print(torch.__version__)"], capture_output=True, text=True).stdout.strip()
    if have != TORCH[0].split("==")[1] + "+cu128":
        run([PY, "-m", "pip", "install", *TORCH, "--index-url", TORCH_INDEX])
    else:
        log(f"torch {have} already installed")
    # ComfyUI's own requirements; torch is already satisfied, so pip leaves the cu128 build alone.
    # comfyui-workflow-templates pulls ~670 MB of template preview media (only used by the web UI's template
    # browser): "light" installs the template JSON without the media, "full" everything, "none" nothing.
    req = (COMFY / "requirements.txt").read_text(encoding="utf-8").splitlines()
    tpl = [r.strip() for r in req if r.strip().startswith("comfyui-workflow-templates")]
    rest = HERE / ".requirements-studio.txt"
    rest.write_text("\n".join(r for r in req if r.strip() not in tpl) + "\n", encoding="utf-8")
    run([PY, "-m", "pip", "install", "-r", rest])
    if templates == "full" and tpl:
        run([PY, "-m", "pip", "install", *tpl])
    elif templates == "light" and tpl:
        ver = tpl[0].split("==")[1] if "==" in tpl[0] else ""
        url = "https://pypi.org/pypi/comfyui-workflow-templates/" + (f"{ver}/json" if ver else "json")
        with urllib.request.urlopen(url, timeout=60) as r:
            dist = json.loads(r.read())["info"]["requires_dist"] or []
        deps = [d.split(";")[0].strip() for d in dist if "extra ==" not in d and ("-core" in d or "-json" in d)]
        run([PY, "-m", "pip", "install", "--no-deps", *tpl, *deps])
    run([PY, "-c", "import torch;print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', "
                   "torch.cuda.get_device_name(0) if torch.cuda.is_available() else None, "
                   "'arch', torch.cuda.get_device_capability(0) if torch.cuda.is_available() else None)"])


def _find_py312() -> str:
    if sys.version_info[:2] == (3, 12):
        return sys.executable
    for cand in (["py", "-3.12"], ["python3.12"]):
        try:
            out = subprocess.run([*cand, "-c", "import sys;print(sys.executable)"], capture_output=True, text=True,
                                 timeout=30).stdout.strip()
            if out:
                return out
        except (OSError, subprocess.SubprocessError):
            pass
    raise SystemExit("Python 3.12 not found: run this script with Python 3.12 or pass --python <path>")


def folders() -> None:
    for d in MODEL_DIRS:
        (MODELS / d).mkdir(parents=True, exist_ok=True)
    for d in ("input", "output", "temp", "user"):
        (HERE / d).mkdir(exist_ok=True)


# ---------------------------------------------------------------- models
def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


_TREE: dict[str, dict] = {}


def remote_meta(folder: str, name: str) -> tuple[int | None, str | None]:
    """Size and SHA-256 of a repackaged file, from the Hugging Face tree API (LFS oid = SHA-256)."""
    if folder not in _TREE:
        url = HF_API + folder
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "video-studio-setup"}),
                                        timeout=60) as r:
                _TREE[folder] = {Path(f["path"]).name: f for f in json.loads(r.read()) if f.get("type") == "file"}
        except (OSError, ValueError) as e:
            log(f"could not read the file list of {folder} ({e!r}); downloading without a checksum")
            _TREE[folder] = {}
    f = _TREE[folder].get(name) or {}
    return f.get("size"), (f.get("lfs") or {}).get("oid")


def download(url: str, dest: Path, size: int | None) -> None:
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "video-studio-setup"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    t0, last = time.time(), 0.0
    with urllib.request.urlopen(req, timeout=120) as r, open(part, "ab" if have and r.status == 206 else "wb") as f:
        if r.status != 206:
            have = 0
        done = have
        while chunk := r.read(1 << 22):
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 15:
                last = time.time()
                rate = (done - have) / max(1e-3, time.time() - t0) / 2**20
                log(f"  {dest.name}: {done / 2**30:.2f}/{(size or 0) / 2**30:.2f} GB  {rate:.1f} MB/s")
    part.replace(dest)


def get_model(folder: str, name: str, size: int | None, sha: str | None, link_from: str | None, verify: bool) -> dict:
    dest = MODELS / folder / name
    url = HF + f"{folder}/{name}"
    if size is None or sha is None:
        size, sha = remote_meta(folder, name)
    if dest.exists() and (size is None or dest.stat().st_size == size):
        if verify and sha and sha256(dest) != sha:
            log(f"{name}: checksum mismatch, downloading again")
            dest.unlink()
        else:
            return {"file": name, "status": "present", "gb": round(dest.stat().st_size / 1e9, 2)}
    src = AI_MODELS / link_from if link_from else None
    if src and src.exists() and src.stat().st_size == size and (not sha or sha256(src) == sha):
        dest.unlink(missing_ok=True)
        try:
            os.link(src, dest)
            how = "hard-linked"
        except OSError:
            shutil.copy2(src, dest)
            how = "copied"
        log(f"{name}: {how} from video/ai/.models ({src.name}, identical SHA-256)")
        return {"file": name, "status": how, "gb": round(size / 1e9, 2)}
    log(f"{name}: downloading {(size or 0) / 1e9:.2f} GB")
    t = time.time()
    for attempt in range(5):
        try:
            download(url, dest, size)
            break
        except (OSError, urllib.error.URLError) as e:
            log(f"{name}: {e!r} — retrying ({attempt + 1}/5)")
            time.sleep(5)
    else:
        raise SystemExit(f"{name}: download failed")
    if size and dest.stat().st_size != size:
        raise SystemExit(f"{name}: wrong size {dest.stat().st_size} != {size}")
    if sha and sha256(dest) != sha:
        dest.unlink()
        raise SystemExit(f"{name}: SHA-256 mismatch — deleted, run again")
    secs = time.time() - t
    log(f"{name}: done in {secs:.0f} s ({(size or 0) / 2**20 / max(1, secs):.1f} MB/s)")
    return {"file": name, "status": "downloaded", "gb": round((size or 0) / 1e9, 2), "seconds": round(secs)}


def models(which: set, verify: bool, parallel: int) -> list[dict]:
    todo = [f for f in FILES if f[4] & which]
    out: list[dict] = []
    lock = threading.Lock()
    queue = list(todo)

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                folder, name, size, sha, _, link = queue.pop(0)
            try:
                r = get_model(folder, name, size, sha, link, verify)
            except BaseException as e:  # noqa: BLE001 - a thread must not die silently (SystemExit included)
                log(f"{name}: FAILED: {e}")
                r = {"file": name, "status": f"failed: {e}", "gb": 0}
            with lock:
                out.append({"folder": folder, **r})
    ts = [threading.Thread(target=worker) for _ in range(max(1, parallel))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def check() -> dict:
    tag = subprocess.run(["git", "-C", COMFY, "describe", "--tags"], capture_output=True, text=True).stdout.strip() \
        if (COMFY / ".git").exists() else None
    rows = []
    for folder, name, size, *_ in FILES:
        p = MODELS / folder / name
        rows.append({"file": f"{folder}/{name}", "present": p.exists(),
                     "gb": round(p.stat().st_size / 1e9, 2) if p.exists() else None})
    return {"comfyui": tag, "pinned": COMFY_TAG, "venv": PY.exists(), "models": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="all", choices=["all", "draft", "quality", "none"])
    ap.add_argument("--python", help="Python 3.12 interpreter for the venv (default: this one or `py -3.12`)")
    ap.add_argument("--skip-code", action="store_true", help="do not clone/update ComfyUI or touch the venv")
    ap.add_argument("--templates", default="light", choices=["light", "full", "none"],
                    help="ComfyUI web-UI workflow templates: light = JSON only (default), full = + ~670 MB preview media")
    ap.add_argument("--verify", action="store_true", help="re-hash files that are already present")
    ap.add_argument("--parallel", type=int, default=2, help="downloads at the same time (default 2)")
    ap.add_argument("--check", action="store_true", help="only report what is installed")
    a = ap.parse_args()
    if a.check:
        print(json.dumps(check(), indent=1))
        return
    t = time.time()
    folders()
    if not a.skip_code:
        install_code()
        install_venv(a.python, a.templates)
    which = {"all": {"draft", "quality"}, "draft": {"draft"}, "quality": {"quality"}, "none": set()}[a.models]
    res = models(which, a.verify, a.parallel) if which else []
    (HERE / "setup-report.json").write_text(json.dumps({"comfyui": COMFY_TAG, "torch": TORCH[0], "at": time.time(),
                                                        "seconds": round(time.time() - t), "models": res}, indent=1))
    log(f"done in {time.time() - t:.0f} s")
    failed = [r for r in res if str(r["status"]).startswith("failed")]
    for r in res:
        log(f"  {r['folder']}/{r['file']}: {r['status']} ({r['gb']} GB)")
    if failed:
        raise SystemExit(f"{len(failed)} model file(s) failed — run the script again (downloads resume)")


if __name__ == "__main__":
    main()
