"""Video templates: video/templates/<id>/manifest.json (+ template.css, template.js, fonts, images).

A template changes the look of the renderer (video/html) without touching it:
  kind "theme" = design tokens (colours, fonts, radii, shadows, caption / progress / logo style) and
                 optional background pictures. No code.
  kind "full"  = a theme plus template.css and an optional template.js ES module that can replace the
                 HTML + animation of any layout and add transitions (see video/templates/TEMPLATES.md).

This module discovers, validates, imports (zip) and resolves templates for a brief. The renderer gets
`timeline.template` = {id, kind, base, css, js, version, tokens, options, default_transition, ...};
`version` changes whenever a file in the folder changes, so the live preview reloads edited files.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

VIDEO = Path(__file__).resolve().parent.parent
ROOT = VIDEO / "templates"
DEFAULT = "aurora"
BUILTIN = ("aurora", "clean", "bold")
KINDS = ("theme", "full")
FORMATS = ("9:16", "1:1", "16:9")
LAYOUTS = ("hero", "chips", "stat", "list", "cta", "screen", "screens", "presenter")
BUILTIN_TRANSITIONS = ("lift", "fade", "slide", "zoom", "wipe", "blur", "cut")
CAPTION_STYLES = ("pill", "plain", "bar", "box")
PROGRESS_STYLES = ("segments", "bar", "dots", "none")
LOGO_PLACEMENTS = ("auto", "top-center", "top-left", "top-right", "none")
OPTION_TYPES = ("boolean", "select", "color", "number", "text")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
CSS_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%/]+\)|hsla?\([\d\s.,%/deg]+\)|transparent)$")
FILE_EXT = {".json", ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif",
            ".woff", ".woff2", ".ttf", ".otf", ".txt", ".md"}
FONT_EXT = {".woff", ".woff2", ".ttf", ".otf"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"}
MAX_ZIP_BYTES = 40 * 1024 * 1024       # uncompressed
MAX_FILES = 300
MAX_FILE = 15 * 1024 * 1024
SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "video-studio/template-manifest.schema.json",
    "title": "Video Studio template manifest",
    "type": "object",
    "required": ["id", "name", "kind"],
    "properties": {
        "id": {"type": "string", "pattern": ID_RE.pattern, "description": "folder name, lowercase, dashes"},
        "name": {"type": "string"}, "description": {"type": "string"}, "author": {"type": "string"},
        "version": {"type": "string"}, "licence": {"type": "string"},
        "kind": {"enum": list(KINDS)},
        "formats": {"type": "array", "items": {"enum": list(FORMATS)}},
        "layouts": {"type": "array", "items": {"enum": list(LAYOUTS)}},
        "css": {"type": "string", "default": "template.css"}, "js": {"type": "string", "default": "template.js"},
        "default_transition": {"type": "string"},
        "transitions": {"type": "array", "items": {"type": "object", "required": ["id", "name"],
                                                    "properties": {"id": {"type": "string"}, "name": {"type": "string"}}}},
        "previews": {"type": "array", "items": {"type": "string"}},
        "options": {"type": "array", "items": {"type": "object", "required": ["key", "type"]}},
        "tokens": {"type": "object"},
    },
}


class TemplateError(ValueError):
    def __init__(self, errors: list[str] | str, status: int = 422):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))
        self.status = status


# ------------------------------------------------------------------ discovery
def folder(tid: str) -> Path:
    if not ID_RE.match(tid or ""):
        raise TemplateError(f"bad template id '{tid}'", 404)
    d = ROOT / tid
    if not (d / "manifest.json").is_file():
        raise TemplateError(f"template '{tid}' not found", 404)
    return d


def version_of(d: Path) -> str:
    """Changes whenever any file in the template folder changes (hot reload of the live preview)."""
    h = hashlib.sha1()
    for f in sorted(d.rglob("*")):
        if f.is_file():
            st = f.stat()
            h.update(f"{f.relative_to(d).as_posix()}:{st.st_size}:{st.st_mtime_ns};".encode())
    return h.hexdigest()[:12]


def read_manifest(d: Path) -> dict:
    try:
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise TemplateError(f"manifest.json: {e}") from None
    if not isinstance(m, dict):
        raise TemplateError("manifest.json must be a JSON object")
    return m


def load(tid: str) -> dict:
    """Manifest + derived fields (dir, version, css/js present, builtin). Raises TemplateError."""
    d = folder(tid)
    m = read_manifest(d)
    errs, _ = check_manifest(m, d)
    if errs:
        raise TemplateError([f"{tid}: {e}" for e in errs])
    return _derive(m, d)


def _derive(m: dict, d: Path) -> dict:
    out = dict(m)
    out["dir"] = d
    out["builtin"] = m.get("id") in BUILTIN
    out["version_hash"] = version_of(d)
    kind = m.get("kind", "theme")
    css, js = m.get("css") or "template.css", m.get("js") or "template.js"
    out["css_file"] = css if kind == "full" and (d / css).is_file() else None
    out["js_file"] = js if kind == "full" and (d / js).is_file() else None
    out.setdefault("formats", list(FORMATS))
    out.setdefault("layouts", list(LAYOUTS))
    out.setdefault("tokens", {})
    out.setdefault("transitions", [])
    out.setdefault("options", [])
    out["previews"] = [p for p in (m.get("previews") or []) if (d / p).is_file()]
    return out


def list_all() -> list[dict]:
    """Every installed template (folders starting with '_' or '.' are skipped), builtins first.
    Broken ones are listed with `error` so the UI can show why."""
    out = []
    if not ROOT.exists():
        return out
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir() or d.name[0] in "_." or not (d / "manifest.json").is_file():
            continue
        try:
            out.append(public(load(d.name)))
        except TemplateError as e:
            out.append({"id": d.name, "name": d.name, "error": "; ".join(e.errors), "builtin": d.name in BUILTIN})
    order = {b: i for i, b in enumerate(BUILTIN)}
    return sorted(out, key=lambda m: (order.get(m["id"], 99), m.get("name", "").lower()))


def exists(tid: str) -> bool:
    try:
        folder(tid)
        return True
    except TemplateError:
        return False


def public(m: dict) -> dict:
    """What the API / UI sees (no paths)."""
    keep = ("id", "name", "description", "author", "version", "licence", "kind", "formats", "layouts", "tokens",
            "default_transition", "transitions", "options", "builtin")
    out = {k: m.get(k) for k in keep if k in m}
    base = f"/templates/{m['id']}/"
    v = m.get("version_hash")
    out["previews"] = [f"{base}{p}?v={v}" for p in m.get("previews", [])]
    out["has_css"], out["has_js"] = bool(m.get("css_file")), bool(m.get("js_file"))
    out["hash"] = v
    return out


def all_transition_ids() -> set[str]:
    ids = set(BUILTIN_TRANSITIONS)
    for m in list_all():
        for tr in m.get("transitions") or []:
            if isinstance(tr, dict) and tr.get("id"):
                ids.add(str(tr["id"]))
    return ids


# ------------------------------------------------------------------ validation
def _is_color(v, refs=True) -> bool:
    if not isinstance(v, str):
        return False
    if refs and (v == "auto" or v in ("brand.primary", "brand.accent", "brand.background")):
        return True
    return bool(HEX.match(v)) or bool(CSS_COLOR.match(v.replace(" ", "")))


def check_manifest(m: dict, d: Path | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for a manifest; `d` = its folder, to check that referenced files exist."""
    E, W = [], []
    if not isinstance(m, dict):
        return ["manifest must be a JSON object"], W
    tid = m.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        E.append("id must be 2-40 characters: lowercase letters, digits and dashes (e.g. \"my-brand\")")
    for k in ("name",):
        if not isinstance(m.get(k), str) or not m.get(k).strip():
            E.append(f"'{k}' is required")
    for k in ("description", "author", "version", "licence"):
        if k in m and not isinstance(m[k], str):
            E.append(f"'{k}' must be a string")
        elif k not in m:
            W.append(f"'{k}' is missing")
    kind = m.get("kind")
    if kind not in KINDS:
        E.append("kind must be \"theme\" or \"full\"")
    fm = m.get("formats", list(FORMATS))
    if not isinstance(fm, list) or not fm or any(f not in FORMATS for f in fm):
        E.append(f"formats must be a list of {', '.join(FORMATS)}")
    elif len(fm) < 3:
        W.append(f"supports only {', '.join(fm)}: projects in other formats use the template anyway, unchecked")
    ly = m.get("layouts", list(LAYOUTS))
    if not isinstance(ly, list) or any(x not in LAYOUTS for x in ly):
        E.append(f"layouts must be a list of {', '.join(LAYOUTS)}")
    css, js = m.get("css") or "template.css", m.get("js") or "template.js"
    for k, v in (("css", css), ("js", js)):
        if not isinstance(v, str) or not _safe_rel(v):
            E.append(f"'{k}' must be a relative file name inside the template folder")
    if d is not None and kind == "theme":
        if (d / "template.js").is_file() or m.get("js"):
            E.append("a \"theme\" template cannot contain code (template.js): set kind to \"full\"")
        if (d / "template.css").is_file() or m.get("css"):
            E.append("a \"theme\" template has no template.css (only tokens + background pictures): set kind to \"full\"")
    if d is not None and kind == "full" and not ((d / css).is_file() or (d / js).is_file()):
        E.append(f"a \"full\" template needs {css} and/or {js}")
    # tokens
    tk = m.get("tokens", {})
    if not isinstance(tk, dict):
        E.append("tokens must be an object")
        tk = {}
    if tk.get("tone", "dark") not in ("dark", "light"):
        E.append("tokens.tone must be \"dark\" or \"light\"")
    cols = tk.get("colors", {})
    if not isinstance(cols, dict):
        E.append("tokens.colors must be an object")
        cols = {}
    hex_only = ("primary", "accent", "background", "surface", "text", "muted", "primary_light", "line_mix")
    for k, v in cols.items():
        if k in hex_only and not (_is_color(v) and (v.startswith("brand.") or v == "auto" or HEX.match(v))):
            E.append(f"tokens.colors.{k}: '{v}' — use a hex colour like #1D1D1F or brand.primary / brand.accent / brand.background")
        elif k not in hex_only and not _is_color(v, refs=False):
            E.append(f"tokens.colors.{k}: '{v}' is not a CSS colour (hex, rgb(), rgba())")
    fonts = tk.get("fonts", {})
    if not isinstance(fonts, dict):
        E.append("tokens.fonts must be an object")
        fonts = {}
    for k in ("display", "body"):
        if k in fonts and (not isinstance(fonts[k], str) or not re.match(r"^[\w .-]{1,60}$", fonts[k])):
            E.append(f"tokens.fonts.{k} must be a font family name")
    for k in ("display_weight", "body_weight"):
        if k in fonts and not (isinstance(fonts[k], int) and 100 <= fonts[k] <= 900):
            E.append(f"tokens.fonts.{k} must be 100..900")
    families = {"Inter", "Noto Sans Arabic"}
    for i, f in enumerate(fonts.get("files") or []):
        if not isinstance(f, dict) or not f.get("family") or not f.get("src"):
            E.append(f"tokens.fonts.files[{i}] needs family and src")
            continue
        families.add(f["family"])
        src = f["src"]
        if not _safe_rel(src) or Path(src).suffix.lower() not in FONT_EXT:
            E.append(f"tokens.fonts.files[{i}].src '{src}' must be a .woff2/.woff/.ttf/.otf file inside the folder")
        elif d is not None and not (d / src).is_file():
            E.append(f"font file missing: {src}")
        if not f.get("licence"):
            W.append(f"font {f['family']}: no 'licence' given (fonts must allow embedding in videos)")
    for k in ("display", "body"):
        if fonts.get(k) and fonts[k] not in families:
            W.append(f"tokens.fonts.{k} '{fonts[k]}' is neither bundled (fonts.files) nor built in (Inter) — "
                     "it is used only if it is installed on this PC")
    rad = tk.get("radii", {})
    if not isinstance(rad, dict) or any(not isinstance(v, (int, float)) or v < 0 for v in rad.values()):
        E.append("tokens.radii values must be numbers (px)")
    sh = tk.get("shadows", {})
    if not isinstance(sh, dict) or any(not isinstance(v, str) or len(v) > 200 or "url(" in v or ";" in v for v in sh.values()):
        E.append("tokens.shadows values must be CSS box-shadow strings")
    cap = tk.get("caption", {})
    if not isinstance(cap, dict):
        E.append("tokens.caption must be an object")
        cap = {}
    if cap.get("style", "pill") not in CAPTION_STYLES:
        E.append(f"tokens.caption.style must be one of {', '.join(CAPTION_STYLES)}")
    for k in ("background", "border", "text", "highlight", "highlight_text"):
        if k in cap and not _is_color(cap[k]):
            E.append(f"tokens.caption.{k}: '{cap[k]}' is not a colour")
    pg = tk.get("progress", {})
    if not isinstance(pg, dict) or pg.get("style", "segments") not in PROGRESS_STYLES:
        E.append(f"tokens.progress.style must be one of {', '.join(PROGRESS_STYLES)}")
    lg = tk.get("logo", {})
    if not isinstance(lg, dict) or lg.get("placement", "auto") not in LOGO_PLACEMENTS:
        E.append(f"tokens.logo.placement must be one of {', '.join(LOGO_PLACEMENTS)}")
    bg = tk.get("background", {})
    if not isinstance(bg, dict):
        E.append("tokens.background must be an object")
        bg = {}
    if bg.get("image"):
        if not _safe_rel(bg["image"]) or Path(bg["image"]).suffix.lower() not in IMAGE_EXT:
            E.append("tokens.background.image must be an image file inside the folder")
        elif d is not None and not (d / bg["image"]).is_file():
            E.append(f"background image missing: {bg['image']}")
    for k in ("blobs", "grid", "particles", "vignette"):
        if k in bg and not isinstance(bg[k], bool):
            E.append(f"tokens.background.{k} must be true or false")
    # transitions + default
    trs = m.get("transitions", [])
    ids = set(BUILTIN_TRANSITIONS)
    if not isinstance(trs, list):
        E.append("transitions must be a list of {id, name}")
        trs = []
    for t in trs:
        if not isinstance(t, dict) or not re.match(r"^[a-z][a-z0-9-]{1,30}$", str(t.get("id") or "")) or not t.get("name"):
            E.append("each transition needs id (lowercase) and name")
            continue
        if kind != "full":
            E.append(f"transition '{t['id']}': only \"full\" templates can add transitions (template.js)")
        ids.add(t["id"])
    dt = m.get("default_transition", "lift")
    if dt not in ids:
        E.append(f"default_transition '{dt}' is not a built-in ({', '.join(BUILTIN_TRANSITIONS)}) or declared transition")
    # previews
    pv = m.get("previews", [])
    if not isinstance(pv, list) or any(not isinstance(p, str) or not _safe_rel(p) for p in pv):
        E.append("previews must be a list of image file names")
    elif d is not None:
        miss = [p for p in pv if not (d / p).is_file()]
        if miss:
            W.append(f"preview image(s) missing: {', '.join(miss)} (run validate.py --previews)")
        if not pv:
            W.append("no preview image (run validate.py --previews to make one)")
    # options
    opts = m.get("options", [])
    if not isinstance(opts, list):
        E.append("options must be a list")
        opts = []
    seen = set()
    for i, o in enumerate(opts):
        if not isinstance(o, dict) or not re.match(r"^[a-z][a-z0-9_-]{0,30}$", str(o.get("key") or "")):
            E.append(f"options[{i}]: key must be lowercase letters/digits/_/-")
            continue
        if o["key"] in seen:
            E.append(f"options: duplicate key '{o['key']}'")
        seen.add(o["key"])
        if o.get("type") not in OPTION_TYPES:
            E.append(f"options.{o['key']}: type must be one of {', '.join(OPTION_TYPES)}")
        if o.get("type") == "select":
            ch = o.get("choices")
            if not isinstance(ch, list) or not ch or any(not isinstance(c, dict) or "value" not in c for c in ch):
                E.append(f"options.{o['key']}: select needs choices [{{value, label}}]")
        if "default" not in o:
            W.append(f"options.{o['key']}: no default")
    return E, W


def _safe_rel(p: str) -> bool:
    if not isinstance(p, str) or not p or len(p) > 160:
        return False
    if p.startswith(("/", "\\")) or ":" in p or "\\" in p:
        return False
    parts = p.split("/")
    return all(x not in ("", ".", "..") for x in parts)


# ------------------------------------------------------------------ per-project settings
def option_values(m: dict, raw) -> tuple[dict, list[str]]:
    """Merge a brief's template_options with the manifest's declared options (defaults, types)."""
    warn = []
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    decl = {o["key"]: o for o in m.get("options") or [] if isinstance(o, dict) and o.get("key")}
    for k, o in decl.items():
        v = raw.get(k, o.get("default"))
        t = o.get("type")
        if v is None:
            continue
        if t == "boolean":
            v = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")
        elif t == "number":
            try:
                v = float(v)
                if "min" in o:
                    v = max(float(o["min"]), v)
                if "max" in o:
                    v = min(float(o["max"]), v)
            except (TypeError, ValueError):
                warn.append(f"template_options.{k}: not a number — using the default")
                v = o.get("default")
        elif t == "color":
            if not (isinstance(v, str) and HEX.match(v)):
                warn.append(f"template_options.{k}: '{v}' is not a hex colour — using the default")
                v = o.get("default")
        elif t == "select":
            vals = [c.get("value") for c in o.get("choices") or []]
            if v not in vals:
                warn.append(f"template_options.{k}: '{v}' is not one of {', '.join(map(str, vals))} — using the default")
                v = o.get("default")
        else:
            v = str(v)[:120]
        out[k] = v
    for k in raw:
        if k not in decl:
            warn.append(f"template_options.{k}: the template has no such option — ignored")
    return out, warn


def timeline_info(brief: dict, override: str | None = None) -> dict:
    """`timeline.template` for the renderer (without URLs; see fill_urls). Falls back to aurora."""
    tid = override or brief.get("template") or DEFAULT
    try:
        m = load(tid)
    except TemplateError:
        m = load(DEFAULT)
    opts, _ = option_values(m, brief.get("template_options"))
    info = {"id": m["id"], "name": m.get("name"), "kind": m.get("kind"), "version": m["version_hash"],
            "tokens": m.get("tokens") or {}, "options": opts,
            "default_transition": m.get("default_transition") or "lift",
            "transitions": [t["id"] for t in m.get("transitions") or [] if isinstance(t, dict)],
            "_dir": str(m["dir"])}
    if m.get("css_file"):
        info["css"] = m["css_file"]
    if m.get("js_file"):
        info["js"] = m["js_file"]
    return info


def fill_urls(info: dict | None, mode: str) -> dict | None:
    """'http' (studio preview: /templates/<id>/) or 'file' (render.py: file:// URI of the folder)."""
    if not info:
        return info
    d = Path(info.pop("_dir", "") or ROOT / info["id"])
    info["base"] = f"/templates/{info['id']}/" if mode == "http" else d.resolve().as_uri() + "/"
    return info


# ------------------------------------------------------------------ import / delete
def import_zip(src: Path, replace: bool = False) -> tuple[dict, list[str]]:
    """Validate and install a template zip. Returns (public manifest, warnings). Raises TemplateError
    with every problem found (so the UI can list them all at once)."""
    try:
        zf = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError):
        raise TemplateError("not a zip file") from None
    with zf, tempfile.TemporaryDirectory(prefix="studio-tpl-") as tmp:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        errs = []
        if not infos:
            raise TemplateError("the zip is empty")
        if len(infos) > MAX_FILES:
            raise TemplateError(f"too many files ({len(infos)}; limit {MAX_FILES})")
        total = sum(i.file_size for i in infos)
        if total > MAX_ZIP_BYTES:
            raise TemplateError(f"too large when unpacked ({total / 1e6:.0f} MB; limit {MAX_ZIP_BYTES // 2**20} MB)")
        names = [i.filename.replace("\\", "/") for i in infos]
        # manifest.json at the root, or inside one top folder (zip of the folder itself)
        prefix = ""
        if "manifest.json" not in names:
            cands = [n for n in names if n.endswith("/manifest.json") and n.count("/") == 1]
            if len(cands) != 1:
                raise TemplateError("manifest.json not found (put it at the top of the zip, or in one folder)")
            prefix = cands[0][: -len("manifest.json")]
        root = Path(tmp) / "t"
        root.mkdir()
        for i, n in zip(infos, names):
            if "__MACOSX/" in n or n.split("/")[-1] in (".DS_Store", "Thumbs.db"):
                continue
            if not n.startswith(prefix):
                errs.append(f"{n}: outside the template folder")
                continue
            rel = n[len(prefix):]
            if not _safe_rel(rel):
                errs.append(f"{n}: unsafe path")
                continue
            if Path(rel).suffix.lower() not in FILE_EXT:
                errs.append(f"{rel}: file type not allowed (allowed: {', '.join(sorted(FILE_EXT))})")
                continue
            if i.file_size > MAX_FILE:
                errs.append(f"{rel}: too large ({i.file_size / 1e6:.0f} MB)")
                continue
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as fsrc, open(dest, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst, 1 << 20)
        if errs:
            raise TemplateError(errs)
        m = read_manifest(root)
        e2, warns = check_manifest(m, root)
        tid = m.get("id") if isinstance(m.get("id"), str) else ""
        if tid in BUILTIN:
            e2.append(f"'{tid}' is a built-in template — give yours another id")
        if tid.startswith("_"):
            e2.append("ids starting with _ are reserved")
        js = root / (m.get("js") or "template.js")
        if m.get("kind") == "full" and js.is_file():
            e2 += js_syntax(js)
        if e2:
            raise TemplateError(e2)
        dest = ROOT / tid
        if dest.exists() and not replace:
            raise TemplateError(f"a template with id '{tid}' is already installed — tick “replace” to update it", 409)
        ROOT.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root, dest)
    return public(load(tid)), warns


def js_syntax(js: Path) -> list[str]:
    """Syntax check of template.js with Node (when it is installed; otherwise the render check finds it)."""
    node = shutil.which("node")
    if not node:
        return []
    with tempfile.TemporaryDirectory(prefix="studio-js-") as tmp:
        f = Path(tmp) / "template.mjs"
        shutil.copy2(js, f)
        try:
            r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            return []
    if r.returncode == 0:
        return []
    msg = "\n".join(x for x in r.stderr.splitlines() if x.strip() and not x.startswith("Node.js"))[-600:]
    return [f"template.js has a syntax error: {msg.replace(str(f), 'template.js')}"]


def delete(tid: str) -> None:
    if tid in BUILTIN:
        raise TemplateError("built-in templates can't be deleted", 409)
    d = folder(tid)
    shutil.rmtree(d)
