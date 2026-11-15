"""Projects on disk: video/projects/<id>/{project.json, assets/, thumbs/, build/, renders/}."""
from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

import brief as B

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
PROJECTS = VIDEO / "projects"
FFMPEG = str(VIDEO / "bin" / "ffmpeg.exe")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v"}
MAX_IMAGE = 30 * 1024 * 1024
MAX_VIDEO = 300 * 1024 * 1024
MAX_RECORDING_SECONDS = 90
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_lock = threading.RLock()


class StoreError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def now() -> float:
    return round(time.time(), 3)


def sanitize(name: str) -> str:
    """Basename only, [A-Za-z0-9._-], lower-case extension, no leading dot."""
    name = re.split(r"[\\/]", str(name or ""))[-1]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-._")[:80] or "file"
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:5]
    return f"{stem}.{ext}" if ext else stem


def kind_of(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    return "image" if ext in IMAGE_EXT else "video" if ext in VIDEO_EXT else None


def probe(path: Path) -> dict:
    """Width/height/duration of a video via `ffmpeg -i` (no ffprobe here)."""
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace")
    info = {}
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    if m:
        info["duration"] = round(int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]), 3)
    m = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", r.stderr)
    if m:
        info["w"], info["h"] = int(m[1]), int(m[2])
    return info


class Project:
    def __init__(self, pid: str):
        if not ID_RE.match(pid or ""):
            raise StoreError("bad project id", 404)
        self.id = pid
        self.dir = PROJECTS / pid
        self.assets_dir = self.dir / "assets"
        self.thumbs_dir = self.dir / "thumbs"
        self.build_dir = self.dir / "build"
        self.renders_dir = self.dir / "renders"

    # ---------- lifecycle ----------
    @classmethod
    def create(cls, brief: dict, source: str = "api") -> "Project":
        PROJECTS.mkdir(parents=True, exist_ok=True)
        with _lock:
            while True:
                pid = f"{B.slug(brief.get('product') or brief.get('title'), 'project')[:30]}-{secrets.token_hex(2)}"
                if not (PROJECTS / pid).exists():
                    break
            p = cls(pid)
            for d in (p.assets_dir, p.thumbs_dir, p.build_dir, p.renders_dir):
                d.mkdir(parents=True)
            p._write({"id": pid, "created": now(), "updated": now(), "source": source, "brief": brief})
        return p

    @classmethod
    def get(cls, pid: str) -> "Project":
        p = cls(pid)
        if not (p.dir / "project.json").exists():
            raise StoreError("project not found", 404)
        return p

    @classmethod
    def all(cls) -> list["Project"]:
        if not PROJECTS.exists():
            return []
        out = []
        for d in PROJECTS.iterdir():
            if d.is_dir() and ID_RE.match(d.name) and (d / "project.json").exists():
                out.append(cls(d.name))
        return out

    def delete(self) -> None:
        with _lock:
            shutil.rmtree(self.dir, ignore_errors=True)

    # ---------- project.json ----------
    def read(self) -> dict:
        return json.loads((self.dir / "project.json").read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        tmp = self.dir / "project.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.dir / "project.json")

    @property
    def brief(self) -> dict:
        return self.read()["brief"]

    def save_brief(self, brief: dict) -> None:
        with _lock:
            d = self.read()
            d["brief"] = brief
            d["updated"] = now()
            self._write(d)

    def touch(self) -> None:
        with _lock:
            d = self.read()
            d["updated"] = now()
            self._write(d)

    # ---------- assets ----------
    def _index_path(self) -> Path:
        return self.dir / "assets.json"

    def assets(self) -> dict:
        """{name: {type, w, h, duration?, size, original?}} for files present in assets/."""
        try:
            idx = json.loads(self._index_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            idx = {}
        out, changed = {}, False
        if self.assets_dir.exists():
            for f in sorted(self.assets_dir.iterdir()):
                k = kind_of(f.name)
                if not f.is_file() or not k:
                    continue
                meta = idx.get(f.name)
                if not meta or meta.get("size") != f.stat().st_size:
                    meta = self._describe(f)
                    changed = True
                out[f.name] = meta
        if changed or len(out) != len(idx):
            self._index_path().write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out

    def _describe(self, f: Path) -> dict:
        k = kind_of(f.name)
        meta = {"type": k, "size": f.stat().st_size}
        if k == "image":
            with Image.open(f) as im:
                meta["w"], meta["h"] = im.size
        else:
            meta.update(probe(f))
        self._thumb(f, k)
        return meta

    def _thumb(self, f: Path, k: str) -> None:
        self.thumbs_dir.mkdir(exist_ok=True)
        out = self.thumbs_dir / (f.name + ".jpg")
        try:
            if k == "image":
                with Image.open(f) as im:
                    im = im.convert("RGB")
                    im.thumbnail((360, 720))
                    im.save(out, "JPEG", quality=82)
            else:
                subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", "0.5", "-i", str(f), "-frames:v", "1",
                                "-vf", "scale='min(360,iw)':-2", str(out)], capture_output=True, timeout=60)
                if not out.exists():
                    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(f), "-frames:v", "1",
                                    "-vf", "scale='min(360,iw)':-2", str(out)], capture_output=True, timeout=60)
        except Exception:  # thumbnails are cosmetic
            pass

    def asset_path(self, name: str) -> Path:
        """Resolve an asset name safely (no traversal)."""
        if sanitize(name) != name:
            raise StoreError("bad asset name", 404)
        p = (self.assets_dir / name).resolve()
        if p.parent != self.assets_dir.resolve() or not p.is_file():
            raise StoreError("asset not found", 404)
        return p

    def add_asset(self, src: Path, original_name: str) -> tuple[str, dict]:
        """Validate + store one uploaded file (moved from `src`). Videos -> VP9 WebM.

        Returns (stored name, meta). Raises StoreError on invalid input.
        """
        name = sanitize(original_name)
        k = kind_of(name)
        if not k:
            raise StoreError(f"{original_name}: unsupported file type (images: png jpg webp; recordings: mp4 mov webm)")
        size = src.stat().st_size
        if size == 0:
            raise StoreError(f"{original_name}: empty file")
        if size > (MAX_IMAGE if k == "image" else MAX_VIDEO):
            raise StoreError(f"{original_name}: too large ({size / 1e6:.0f} MB; limit "
                             f"{(MAX_IMAGE if k == 'image' else MAX_VIDEO) // 2**20} MB)", 413)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        if k == "image":
            try:
                with Image.open(src) as im:
                    im.verify()
                with Image.open(src) as im:
                    w, h = im.size
                    fmt = (im.format or "").lower()
            except Exception:
                raise StoreError(f"{original_name}: not a valid image") from None
            if w * h > 60_000_000:
                raise StoreError(f"{original_name}: image too large ({w}x{h})")
            ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}.get(fmt)
            if not ext:
                raise StoreError(f"{original_name}: unsupported image format {fmt}")
            if Path(name).suffix.lower() not in (ext, ".jpeg" if ext == ".jpg" else ext):
                name = Path(name).stem + ext
            dest = self.assets_dir / name
            shutil.move(str(src), dest)
        else:
            dest = self.assets_dir / (Path(name).stem + ".webm")
            self.transcode(src, dest, original_name)
            try:
                src.unlink()
            except OSError:
                pass
        meta = self._describe(dest)
        if dest.name != original_name:
            meta["original"] = original_name
        self.assets()  # refresh index
        idx = json.loads(self._index_path().read_text(encoding="utf-8"))
        idx[dest.name] = meta
        self._index_path().write_text(json.dumps(idx, indent=1), encoding="utf-8")
        return dest.name, meta

    @staticmethod
    def transcode(src: Path, dest: Path, label: str = "") -> None:
        """Screen recording -> VP9 WebM, no audio, 30 fps, long side <= 1600 px.

        Playwright's Chromium cannot decode H.264, so every recording is converted.
        Short GOP keeps frame-accurate seeking fast in the renderer.
        """
        info = probe(src)
        if "w" not in info:
            raise StoreError(f"{label or src.name}: not a readable video")
        tmp = dest.with_suffix(".tmp.webm")
        scale = ("scale=w='trunc(min(1,1600/max(iw,ih))*iw/2)*2':h='trunc(min(1,1600/max(iw,ih))*ih/2)*2'"
                 ":flags=lanczos,fps=30,format=yuv420p")
        r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-t", str(MAX_RECORDING_SECONDS),
                            "-an", "-vf", scale, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "32",
                            "-deadline", "good", "-cpu-used", "5", "-row-mt", "1", "-g", "30",
                            "-f", "webm", str(tmp)], capture_output=True, text=True, errors="replace", timeout=1800)
        if r.returncode != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise StoreError(f"{label or src.name}: could not convert video ({r.stderr.strip()[-300:]})")
        tmp.replace(dest)

    def delete_asset(self, name: str) -> None:
        p = self.asset_path(name)
        p.unlink()
        (self.thumbs_dir / (name + ".jpg")).unlink(missing_ok=True)
        self.assets()

    # ---------- renders ----------
    def renders(self) -> list[dict]:
        out = []
        if self.renders_dir.exists():
            for f in sorted(self.renders_dir.glob("*.mp4"), reverse=True):
                meta = {}
                mj = f.with_suffix(".json")
                if mj.exists():
                    try:
                        meta = json.loads(mj.read_text(encoding="utf-8"))
                    except ValueError:
                        pass
                out.append({"name": f.name, "size": f.stat().st_size, "created": f.stat().st_mtime, **meta})
        return out

    def render_path(self, name: str) -> Path:
        if not re.match(r"^[\w.-]+\.mp4$", name or ""):
            raise StoreError("bad render name", 404)
        p = self.renders_dir / name
        if not p.is_file():
            raise StoreError("render not found", 404)
        return p

    def summary(self) -> dict:
        d = self.read()
        b = d["brief"]
        assets = self.assets()
        thumb = None
        for s in b.get("scenes", []):
            cand = s.get("asset") or next((i.get("asset") for i in s.get("items") or [] if isinstance(i, dict)), None)
            if cand and cand in assets:
                thumb = cand
                break
        if not thumb and assets:
            thumb = next(iter(assets))
        renders = self.renders()
        return {"id": self.id, "title": b.get("title"), "product": b.get("product"), "language": b.get("language"),
                "format": b.get("format"), "scenes": len(b.get("scenes", [])), "assets": len(assets),
                "renders": len(renders), "latest_render": renders[0]["name"] if renders else None,
                "thumb": thumb, "created": d.get("created"), "updated": d.get("updated"), "source": d.get("source")}


def import_project(raw_brief, files: list[tuple[Path, str]], source: str) -> tuple[Project, list[str]]:
    """Create a project from a brief (dict) + files [(temp path, original name)].

    Assets are stored first so the brief can be checked against them; recordings
    are renamed to .webm and the brief references rewritten.
    """
    if raw_brief is None:
        brief = B.blank()
        warnings = ["no brief given — created a blank project"]
    else:
        brief, warnings = B.normalize(raw_brief)
    p = Project.create(brief, source)
    mapping, errors = {}, []
    try:
        for src, orig in files:
            try:
                stored, _ = p.add_asset(src, orig)
                mapping[orig] = stored
                if sanitize(orig) != stored:
                    mapping[sanitize(orig)] = stored
            except StoreError as e:
                errors.append(str(e))
        if raw_brief is not None:
            fixed = B.rename_assets(raw_brief, mapping)
            brief, warnings = B.normalize(fixed, p.assets())
        p.save_brief(brief)
    except Exception:
        p.delete()
        raise
    return p, errors + warnings
