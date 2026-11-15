"""Video Studio — local web app for promo videos from screenshots + an AI-written brief.

    video/.venv/Scripts/python -m uvicorn server:app --app-dir video/studio --host 127.0.0.1 --port 8001
    (or video/studio/start.bat)

UI at http://127.0.0.1:8001/ ; API under /api (see README.md).
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import align
import brief as B
import build
import comfy as CF
import comfyflows as CFW
import followup
import history as HI
import i18n as I
import inbox
import library as L
import music as MU
import presenter as PR
import providers as PV
import security
import sysmon
import templates as TP
from aijobs import AI_PY, DISCLOSURE, LICENCES, AIJobs
from jobs import Jobs
from pipeline import Pipelines, image_ref
from store import MAX_IMAGE, MAX_VIDEO, Project, StoreError, import_project, kind_of

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
EXAMPLE = STUDIO / "example"
MAX_REQUEST = 1536 * 1024 * 1024

app = FastAPI(title="Video Studio", version="1.0",
              description="Promo videos from screenshots, recordings and an AI-written brief.")
app.add_middleware(security.Guard)     # loopback Host, same-origin writes, body limits, security headers
jobs = Jobs()
try:
    L.import_existing()   # first start: the 3 existing Wan2.2 clips + the generated presenter portrait
except Exception as e:  # never block the studio on the AI library
    print("library import failed:", e)
ai = None
make = None


def _activity() -> list[tuple]:
    """What runs right now, for the monitor's timeline markers: pipeline stage, AI job, render / voice job."""
    out = []
    if make is not None:
        out += make.activity()
    if ai is not None and ai.current:
        j = ai.jobs.get(ai.current) or {}
        if j.get("status") == "running":
            what = j.get("provider") or j.get("kind")
            out.append((f"ai:{j['id']}", f"AI · {j.get('kind')}{' · ' + what if what != j.get('kind') else ''}"
                        f"{' · ' + j['preset'] if j.get('preset') and j.get('kind') == 'broll' else ''}"
                        f"{' (warm)' if j.get('warm') else ''}", f"ai-{j.get('kind')}"))
    for j in list(jobs.jobs.values()):
        if j["status"] == "running":
            out.append((f"job:{j['id']}", f"{'Render' if j['kind'] == 'render' else 'Voice-over'} · {j['project']}",
                        j["kind"]))
    return out


def _warm_ram() -> dict:
    if ai is None:
        return {}
    return {w.family: w.ram_gb() for w in list(ai.pool.workers.values()) if w.alive()}


sampler = sysmon.Sampler(ai_pids=lambda: ai._ai_pids() if ai is not None else set(), activity=_activity,
                         warm_ram=_warm_ram, comfy_pids=lambda: ai.comfy.pids() if ai is not None else set(),
                         persist=L.LIB / "monitor-history.jsonl")   # the last 30 min survive a restart
ai = AIJobs(sampler=sampler)
make = Pipelines(jobs, ai)


@app.on_event("startup")
def _startup() -> None:
    inbox.start()

    def _bg():
        try:
            MU.ensure_builtin()          # 3 small CC0 tracks, synthesised once
        except Exception as e:  # never block the studio on the music library
            print("builtin music failed:", e)
        align.load_model()               # warm up faster-whisper (CPU) for word timing
    import threading
    threading.Thread(target=_bg, name="warmup", daemon=True).start()


@app.on_event("shutdown")
def _shutdown() -> None:
    jobs.shutdown()             # running renders: render.py + its Chromium + ffmpeg (process tree)
    sampler.flush(force=True)   # monitor history: the last seconds too
    ai.pool.shutdown()          # warm workers exit with the studio (they also exit when their socket closes)
    # a managed ComfyUI keeps running (it holds no studio state); the next studio start adopts it


@app.exception_handler(StoreError)
def _store_error(req, e: StoreError):
    return JSONResponse({"detail": I.tr(str(e), I.ui_lang(req))}, status_code=e.status)


@app.exception_handler(StarletteHTTPException)
def _http_error(req, e: StarletteHTTPException):
    detail = I.tr(e.detail, I.ui_lang(req)) if isinstance(e.detail, str) else e.detail
    return JSONResponse({"detail": detail}, status_code=e.status_code, headers=getattr(e, "headers", None))


@app.exception_handler(TP.TemplateError)
def _template_error(_req, e: TP.TemplateError):
    return JSONResponse({"detail": "; ".join(e.errors), "errors": e.errors}, status_code=e.status)


@app.exception_handler(CFW.FlowError)
def _flow_error(_req, e: CFW.FlowError):
    return JSONResponse({"detail": str(e), "errors": e.errors}, status_code=e.status)


@app.exception_handler(CF.ComfyError)
def _comfy_error(_req, e: CF.ComfyError):
    return JSONResponse({"detail": str(e)}, status_code=503)


@app.exception_handler(B.BriefError)
def _brief_error(req, e: B.BriefError):
    lang = I.ui_lang(req)
    return JSONResponse({"detail": I.tr("invalid brief", lang), "errors": I.tr_list(e.errors, lang)}, status_code=422)


NOCACHE = {"Cache-Control": "no-store"}


# ---------------------------------------------------------------- meta
@app.get("/api/health")
def health():
    try:
        models = build.V.models(build.OVOZ, timeout=3)
        return {"studio": True, "ovoz": True, "ovoz_url": build.OVOZ, "ovoz_models": len(models), "preview_sandbox": True}
    except build.V.TTSError as e:
        return {"studio": True, "ovoz": False, "ovoz_url": build.OVOZ, "ovoz_error": str(e), "preview_sandbox": True}


@app.get("/api/voices")
def voices():
    """Proxy of Ovoz GET /api/models (503 when Ovoz is down)."""
    try:
        return build.V.models(build.OVOZ, timeout=8)
    except build.V.TTSError as e:
        raise HTTPException(503, str(e)) from None


@app.get("/api/brief-spec")
def brief_spec():
    return B.SCHEMA


def _ai_request_text() -> str:
    md = (STUDIO / "BRIEF.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- AI-REQUEST:START -->\s*```(?:text)?\n(.*?)```\s*<!-- AI-REQUEST:END -->", md, re.S)
    return m.group(1).strip() + "\n" if m else md


@app.get("/api/ai-request", response_class=PlainTextResponse)
def ai_request():
    """Copy-paste prompt for a chat AI (from BRIEF.md)."""
    return _ai_request_text()


@app.get("/api/inbox")
def inbox_status():
    return {"path": str(inbox.INBOX), "poll_seconds": inbox.POLL_SECONDS, "events": inbox.log}


# ---------------------------------------------------------------- upload helpers
async def _spool(upload, tmpdir: Path, idx: int) -> tuple[Path, str]:
    """Copy one UploadFile to disk, enforcing the size limit while streaming."""
    name = upload.filename or f"file{idx}"
    k = kind_of(name)
    limit = MAX_VIDEO if k == "video" else MAX_IMAGE if k == "image" else 2 * 1024 * 1024
    dest = tmpdir / f"{idx}.upload"
    size = 0
    with open(dest, "wb") as f:
        while chunk := await upload.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise StoreError(f"{name}: too large (limit {limit // 2**20} MB)", 413)
            f.write(chunk)
    return dest, name


def _parse_brief(text: str | bytes):
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8-sig")
        return json.loads(text)
    except (ValueError, UnicodeDecodeError) as e:
        raise B.BriefError([f"brief is not valid JSON: {e}"]) from None


async def _read_form(request: Request, tmpdir: Path):
    """multipart: field/file 'brief' (JSON) + any number of files (any field name).

    A *.json file among the files is taken as the brief when no 'brief' part exists.
    """
    if int(request.headers.get("content-length") or 0) > MAX_REQUEST:
        raise StoreError("request too large", 413)
    form = await request.form(max_files=200, max_fields=50, max_part_size=4 * 1024 * 1024)
    raw_brief, files = None, []
    for i, (key, val) in enumerate(form.multi_items()):
        if isinstance(val, str):
            if key == "brief" and val.strip():
                raw_brief = _parse_brief(val)
            continue
        if key == "brief" or (val.filename or "").lower().endswith(".json"):
            if raw_brief is None:
                raw_brief = _parse_brief(await val.read(2 * 1024 * 1024))
            continue
        if not val.filename:
            continue
        files.append(await _spool(val, tmpdir, i))
    return raw_brief, files


# ---------------------------------------------------------------- projects
@app.get("/api/projects")
def list_projects():
    ps = [p.summary() for p in Project.all()]
    return sorted(ps, key=lambda s: s.get("updated") or 0, reverse=True)


@app.post("/api/projects", status_code=201)
async def create_project(request: Request):
    """Create a project.

    - multipart/form-data: `brief` (JSON text or file) + files (screenshots, recordings, logo)
    - application/json: the brief itself, or {"brief": {...}}; no assets
    - empty body / no brief: blank project
    """
    ctype = request.headers.get("content-type", "")
    with tempfile.TemporaryDirectory(prefix="studio-up-") as tmp:
        if ctype.startswith("multipart/form-data"):
            raw, files = await _read_form(request, Path(tmp))
            source = request.query_params.get("source", "api")
        else:
            body = await request.body()
            raw = _parse_brief(body) if body.strip() else None
            if isinstance(raw, dict) and isinstance(raw.get("brief"), dict):
                raw = raw["brief"]
            if isinstance(raw, dict) and raw.get("blank"):
                langs = raw.get("languages") if isinstance(raw.get("languages"), list) else None
                fmts = raw.get("formats") if isinstance(raw.get("formats"), list) else None
                lang = raw.get("language") or (langs[0] if langs else "en")
                fmt = raw.get("format") or (fmts[0] if fmts else "9:16")
                raw = B.blank(str(raw.get("product") or "My product")[:60], lang, fmt, langs, fmts)
            files, source = [], request.query_params.get("source", "api")
        p, warnings = import_project(raw, files, source if source in ("api", "ui", "inbox") else "api")
    return {**p.summary(), "warnings": I.tr_list(warnings, I.ui_lang(request)), "url": f"/#/p/{p.id}"}


@app.post("/api/projects/example", status_code=201)
def create_example(request: Request, format: str = "9:16"):
    """New project from video/studio/example (fictional app)."""
    raw = json.loads((EXAMPLE / "brief.json").read_text(encoding="utf-8"))
    if format in B.FORMATS:
        raw["format"] = format
        raw.pop("formats", None)
    with tempfile.TemporaryDirectory(prefix="studio-ex-") as tmp:
        files = []
        for f in sorted(EXAMPLE.iterdir()):
            if kind_of(f.name):
                t = Path(tmp) / f.name
                shutil.copy2(f, t)
                files.append((t, f.name))
        p, warnings = import_project(raw, files, "ui")
    return {**p.summary(), "warnings": I.tr_list(warnings, I.ui_lang(request))}


def _detail(p: Project, lang: str = "en") -> dict:
    d = p.read()
    brief = d["brief"]
    by_lang = build.voiced_map(p, brief)
    voiced = by_lang.get(brief.get("language")) or {}
    _, warnings = B.normalize(brief, p.assets())
    return {**d, "assets": p.assets(), "renders": p.renders(), "voiced": voiced, "voiced_by_lang": by_lang,
            "missing": B.missing_translations(brief), "warnings": I.tr_list(warnings, lang),
            "jobs": [I.tr_job(j, lang) for j in jobs.for_project(p.id)],
            "ai_jobs": [I.tr_job(j, lang) for j in ai.active(project=p.id)],
            "make": I.tr_job(make.latest(p.id), lang), "presenter_state": _presenter_state(p)}


@app.get("/api/projects/{pid}")
def get_project(pid: str, request: Request):
    return _detail(Project.get(pid), I.ui_lang(request))


@app.put("/api/projects/{pid}")
async def update_project(pid: str, request: Request):
    """Replace the brief. Body: {"brief": {...}} or the brief itself."""
    p = Project.get(pid)
    body = _parse_brief(await request.body())
    raw = body.get("brief") if isinstance(body, dict) and isinstance(body.get("brief"), dict) else body
    clean, warnings = B.normalize(raw, p.assets())
    snap = p.save_brief(clean)
    lang = I.ui_lang(request)
    if snap:
        snap = I.tr_history([snap], lang)[0]
    return {"brief": clean, "warnings": I.tr_list(warnings, lang), "updated": p.read()["updated"], "snapshot": snap}


# ---------------------------------------------------------------- version history
@app.get("/api/projects/{pid}/history")
def project_history(pid: str, request: Request):
    """Snapshots of the brief, newest first: [{id, created, updated, kind: base|import|edit|restore, summary, scenes}]."""
    return JSONResponse(I.tr_history(HI.listing(Project.get(pid)), I.ui_lang(request)), headers=NOCACHE)


@app.get("/api/projects/{pid}/history/{sid}")
def project_history_get(pid: str, sid: str):
    snap = HI.get(Project.get(pid), sid)
    if not snap:
        raise HTTPException(404, "snapshot not found")
    return snap


@app.post("/api/projects/{pid}/history/{sid}/restore")
def project_history_restore(pid: str, sid: str, request: Request):
    """Make snapshot `sid` the current brief. This is saved as a new snapshot, so it can be undone."""
    p = Project.get(pid)
    snap = HI.get(p, sid)
    if not snap:
        raise HTTPException(404, "snapshot not found")
    from datetime import datetime
    when = datetime.fromtimestamp(snap.get("updated") or snap["created"]).strftime("%d %b %H:%M:%S")
    clean, warnings = B.normalize(snap["brief"], p.assets())
    meta = p.save_brief(clean, kind="restore", label=f"Restored the version from {when}")
    lang = I.ui_lang(request)
    return {"brief": clean, "warnings": I.tr_list(warnings, lang), "snapshot": I.tr_history([meta], lang)[0] if meta else None,
            "updated": p.read()["updated"]}


# ---------------------------------------------------------------- follow-up for the source AI
@app.get("/api/projects/{pid}/followup")
def project_followup(pid: str, format: str = "json", lang: str = "en"):
    """A ready-to-send message for the source AI listing missing/invalid files, missing translations,
    over-long or empty text, badly sized screenshots and suggestions. ?format=text returns just the message.
    ?lang=uz writes it in Uzbek (default English: the source AI usually works in English)."""
    p = Project.get(pid)
    r = followup.build(p.brief, p.assets(), lang=lang if lang in followup.LANGS else "en")
    if format == "text":
        return PlainTextResponse(r["message"])
    return JSONResponse(r, headers=NOCACHE)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    p = Project.get(pid)
    if jobs.busy(pid, "render"):
        raise HTTPException(409, "a render is running for this project")
    if any(j["status"] == "running" for j in ai.active(project=pid)):
        raise HTTPException(409, "an AI job is running for this project")
    if make.active(pid):
        raise HTTPException(409, "a “Make video” pipeline is running for this project")
    p.delete()
    return {"deleted": pid}


# ---------------------------------------------------------------- assets
@app.post("/api/projects/{pid}/assets", status_code=201)
async def upload_assets(pid: str, request: Request):
    lang = I.ui_lang(request)
    p = Project.get(pid)
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise HTTPException(415, "send multipart/form-data with one or more files")
    added, errors = [], []
    with tempfile.TemporaryDirectory(prefix="studio-up-") as tmp:
        raw, files = await _read_form(request, Path(tmp))
        for src, name in files:
            try:
                stored, meta = p.add_asset(src, name)
                added.append({"name": stored, "original": name, **meta})
            except StoreError as e:
                errors.append(str(e))
    if raw is not None:
        errors.append("a brief was included but ignored here — use PUT /api/projects/{id}")
    if added:
        mapping = {a["original"]: a["name"] for a in added if a["original"] != a["name"]}
        if mapping:
            p.save_brief(B.normalize(B.rename_assets(p.brief, mapping), p.assets())[0])
        p.touch()
    errors = I.tr_list(errors, lang)
    if not added and errors:
        raise HTTPException(400, "; ".join(errors))
    return {"added": added, "errors": errors, "assets": p.assets()}


@app.delete("/api/projects/{pid}/assets/{name}")
def delete_asset(pid: str, name: str, request: Request):
    p = Project.get(pid)
    p.delete_asset(name)
    _, warnings = B.normalize(p.brief, p.assets())
    return {"deleted": name, "warnings": I.tr_list(warnings, I.ui_lang(request))}


@app.get("/api/projects/{pid}/files/{name}")
def asset_file(pid: str, name: str):
    return FileResponse(Project.get(pid).asset_path(name))


@app.get("/api/projects/{pid}/thumbs/{name}")
def asset_thumb(pid: str, name: str):
    p = Project.get(pid)
    src = p.asset_path(name)
    t = p.thumbs_dir / (name + ".jpg")
    if not t.exists():
        p._thumb(src, kind_of(name))
    return FileResponse(t if t.exists() else src)


# ---------------------------------------------------------------- preview / voice
@app.get("/api/projects/{pid}/timeline")
def preview_timeline(pid: str, lang: str | None = None, format: str | None = None, template: str | None = None):
    """Timeline for the live preview of one language + format (estimated lengths for scenes without
    voice; word timing that is not aligned yet is queued in the background). `template` previews another
    template without saving it (the template picker's live preview). Template files are re-read on every
    call, so an edited template.css / template.js shows up on the next preview refresh."""
    p = Project.get(pid)
    if template and not TP.exists(template):
        raise HTTPException(404, f"template '{template}' not found")
    tl = build.timeline(p, lang=lang, fmt=format, align_mode="queue", template=template)
    TP.fill_urls(tl.get("template"), "http")
    if tl.get("broll") or tl.get("presenter"):
        build.ai_urls(p, tl, "http")
    tl["music"] = build.preview_music(p, tl)
    for k in [k for k in tl if k.startswith("_")]:
        tl.pop(k)
    tl["asset_base"] = f"/api/projects/{pid}/files/"
    tl["audio_url"] = (f"/api/projects/{pid}/voiceover.wav?lang={tl['language']}&k={tl['audio_key']}"
                       if tl["audio_key"] else None)
    return JSONResponse(tl, headers=NOCACHE)


@app.get("/api/projects/{pid}/voiceover.wav")
def voiceover(pid: str, lang: str | None = None):
    p = Project.get(pid)
    wav = build.voiceover(p, build.timeline(p, lang=lang))
    if not wav:
        raise HTTPException(404, "no voice-over yet")
    return FileResponse(wav, media_type="audio/wav", headers=NOCACHE)


@app.get("/api/projects/{pid}/voice/{scene_id}.wav")
def scene_clip(pid: str, scene_id: str, lang: str | None = None):
    p = Project.get(pid)
    brief = p.brief
    s = next((s for s in brief["scenes"] if s["id"] == scene_id), None)
    if not s:
        raise HTTPException(404, "scene not found")
    f = build.scene_voice(p, brief, s, lang)
    if not f:
        raise HTTPException(404, "no voice for this scene yet")
    return FileResponse(f, media_type="audio/wav", headers=NOCACHE)


@app.post("/api/projects/{pid}/voice", status_code=202)
async def make_voice(pid: str, request: Request):
    """Generate voice-over (+ word timing). Body (optional): {"scene": "<id>", "force": false,
    "lang": "ru" | "langs": ["uz", "ru"]} — default: every language of the project."""
    Project.get(pid)
    prm = _body(await request.body())
    return jobs.submit("voice", pid, scene=prm.get("scene"), force=bool(prm.get("force")), lang=prm.get("lang"),
                       langs=prm.get("langs") if isinstance(prm.get("langs"), list) else None)


@app.post("/api/projects/{pid}/render", status_code=202)
async def make_render(pid: str, request: Request):
    """Queue a render: one MP4 (+ .srt, .vtt, cover .png) per language x format.
    Body (optional): {"allow_silent": false, "skip_voice": false, "workers": 8,
    "langs": [...] (default all), "formats": [...] (default all)}."""
    Project.get(pid)
    prm = _body(await request.body())
    if (j := jobs.busy(pid, "render")):
        return JSONResponse(j, status_code=409)
    try:
        workers = max(1, min(16, int(prm.get("workers") or 8)))   # parallel Chromium pages
    except (TypeError, ValueError):
        raise HTTPException(422, "workers must be a number (1-16)") from None
    return jobs.submit("render", pid, allow_silent=bool(prm.get("allow_silent")),
                       skip_voice=bool(prm.get("skip_voice")), workers=workers,
                       langs=prm.get("langs") if isinstance(prm.get("langs"), list) else None,
                       formats=prm.get("formats") if isinstance(prm.get("formats"), list) else None)


@app.get("/api/jobs/{jid}")
def job(jid: str, request: Request):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return I.tr_job(j, I.ui_lang(request))


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str, request: Request):
    j = jobs.cancel(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return I.tr_job(j, I.ui_lang(request))


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str, request: Request):
    return [I.tr_job(j, I.ui_lang(request)) for j in jobs.for_project(pid)]


# ---------------------------------------------------------------- renders
@app.get("/api/projects/{pid}/renders")
def renders(pid: str):
    return Project.get(pid).renders()


RENDER_TYPES = {".mp4": "video/mp4", ".srt": "application/x-subrip; charset=utf-8", ".vtt": "text/vtt; charset=utf-8",
                ".png": "image/png"}


@app.get("/api/projects/{pid}/renders/{name}")
def render_file(pid: str, name: str, download: int = 0):
    """A render (.mp4) or its subtitles (.srt / .vtt) or cover image (.png)."""
    p = Project.get(pid)
    f = p.render_path(name)
    mt = RENDER_TYPES[f.suffix.lower()]
    if download:
        return FileResponse(f, media_type=mt, filename=f"{pid}-{name}")
    return FileResponse(f, media_type=mt)


@app.delete("/api/projects/{pid}/renders/{name}")
def delete_render(pid: str, name: str):
    """Delete a render with its .json, .srt, .vtt and cover."""
    p = Project.get(pid)
    f = p.render_path(name)
    if f.suffix != ".mp4":
        raise HTTPException(400, "delete the .mp4; its subtitles and cover go with it")
    f.unlink()
    for ext in (".json", ".srt", ".vtt", ".png"):
        f.with_suffix(ext).unlink(missing_ok=True)
    return {"deleted": name}


# ---------------------------------------------------------------- music library
@app.get("/api/library/music")
def library_music():
    used: dict[str, list] = {}
    for p in Project.all():
        try:
            b = p.brief
        except (OSError, ValueError):
            continue
        tr = (b.get("music") or {}).get("track") if isinstance(b.get("music"), dict) else None
        if tr:
            used.setdefault(tr, []).append({"project": p.id, "title": b.get("title")})
    return JSONResponse([{**m, "url": f"/api/library/music/{m['id']}/track{m['ext']}", "used_in": used.get(m["id"], [])}
                         for m in MU.list_tracks()], headers=NOCACHE)


@app.get("/api/library/music/{tid}/{name}")
def library_music_file(tid: str, name: str):
    f = MU.track_file(tid)
    if name != f.name:
        raise HTTPException(404, "not found")
    mt = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}[f.suffix]
    return FileResponse(f, media_type=mt, headers={"Cache-Control": "max-age=3600"})


@app.post("/api/library/music", status_code=201)
async def library_music_upload(request: Request):
    """multipart: one audio file (mp3/wav/ogg/m4a, <= 60 MB) + licence (required) + title?, artist?, mood?, bpm?,
    project? (use it in that project)."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise HTTPException(415, "send multipart/form-data")
    form = await request.form(max_files=2, max_fields=12, max_part_size=1024 * 1024)
    up = next((v for _, v in form.multi_items() if not isinstance(v, str)), None)
    if up is None or not up.filename:
        raise HTTPException(422, "no audio file")
    licence = str(form.get("licence") or "").strip()
    if not licence:
        raise HTTPException(422, "licence is required (e.g. 'CC BY 4.0 — Artist', 'Pixabay licence', 'own recording')")
    ext = Path(up.filename).suffix.lower()
    if ext not in MU.EXTS:
        raise HTTPException(422, "unsupported audio type (mp3, wav, ogg, m4a)")
    with tempfile.TemporaryDirectory(prefix="studio-mu-") as tmp:
        dest = Path(tmp) / f"upload{ext}"
        size = 0
        with open(dest, "wb") as f:
            while chunk := await up.read(1 << 20):
                size += len(chunk)
                if size > MU.MAX_BYTES:
                    raise StoreError(f"audio file too large (limit {MU.MAX_BYTES // 2**20} MB)", 413)
                f.write(chunk)
        bpm = str(form.get("bpm") or "").strip()
        m = MU.add_track(dest, {"title": str(form.get("title") or Path(up.filename).stem)[:80],
                                "artist": str(form.get("artist") or "")[:80], "licence": licence[:160],
                                "source": "upload", "mood": str(form.get("mood") or "")[:80] or None,
                                "bpm": int(bpm) if bpm.isdigit() else None, "original_name": up.filename[:120]}, move=True)
    if form.get("project"):
        p = Project.get(str(form.get("project")))
        b = p.brief
        b["music"] = {**(b.get("music") if isinstance(b.get("music"), dict) else {}), "track": m["id"]}
        p.save_brief(B.normalize(b, p.assets())[0])
    return {**m, "url": f"/api/library/music/{m['id']}/track{m['ext']}"}


@app.delete("/api/library/music/{tid}")
def library_music_delete(tid: str):
    """Delete a track. Projects that used it render without music (with a warning in the editor)."""
    MU.delete(tid)
    return {"deleted": tid}


@app.get("/api/align/status")
def align_status():
    """faster-whisper word timing: model, loaded, error."""
    return align.status()


# ---------------------------------------------------------------- AI: GPU queue, library, presenter
def _body(raw: bytes) -> dict:
    if not raw.strip():
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(d, dict):
        raise HTTPException(400, "body must be a JSON object")
    return d


@app.get("/api/ai/status")
def ai_status(fresh: int = 0):
    """GPU memory (nvidia-smi + per-process attribution), queue summary, presets, licences."""
    g = ai.gpu_status(fresh=bool(fresh))
    tm = ai.timings
    return JSONResponse({
        "gpu": g, "venv": AI_PY.exists(), "current_job": ai.current,
        "queued": len(ai.queued()), "active": ai.active(),
        "providers": PV.public(ai), "default_provider": PV.DEFAULT, "workers": ai.pool.view(),
        "presets": {
            "broll": {k: {"steps": v["steps"], "frames": v["frames"], "portrait": v["portrait"], "landscape": v["landscape"],
                          "seconds": round(v["frames"] / 24, 2), "estimate": ai.estimate_broll(PV.DEFAULT, k),
                          "need_gb": ai.need_gb("broll", k), "measured": bool(tm.get(f"broll/{k}", {}).get("runs"))}
                      for k, v in L.BROLL_PRESETS.items()},
            "music": {"estimate_per_30s": ai.estimate("music", "final", 36, warm=ai.is_warm("acestep", ["acestep"])),
                      "need_gb": ai.need_gb("music", "final"), "measured": bool(tm.get("music/final", {}).get("runs"))},
            "presenter": {k: {**v, "need_gb": ai.need_gb("presenter", k),
                              "per_second": tm.get(f"presenter/{k}", {}).get("per_unit"),
                              "load": tm.get(f"presenter/{k}", {}).get("load"),
                              "measured": bool(tm.get(f"presenter/{k}", {}).get("runs"))}
                          for k, v in PR.PRESETS.items()},
            "portrait": {"estimate": ai.estimate("portrait", "final", 40), "need_gb": ai.need_gb("portrait", "final")}},
        "licences": LICENCES, "disclosure": DISCLOSURE, "consent_text": L.CONSENT_TEXT,
    }, headers=NOCACHE)


@app.get("/api/ai/jobs")
def ai_jobs(request: Request, project: str | None = None):
    lang = I.ui_lang(request)
    return JSONResponse([I.tr_job(j, lang) for j in ai.list(project=project)], headers=NOCACHE)


@app.get("/api/ai/jobs/{jid}")
def ai_job(jid: str, request: Request):
    j = ai.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return JSONResponse(I.tr_job(j, I.ui_lang(request)), headers=NOCACHE)


@app.post("/api/ai/jobs/{jid}/cancel")
def ai_cancel(jid: str):
    j = ai.cancel(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.post("/api/ai/jobs/{jid}/start-anyway")
def ai_start_anyway(jid: str):
    """Skip the free-VRAM check (the job may spill into shared memory and run much slower)."""
    j = ai.start_anyway(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.post("/api/ai/jobs/{jid}/retry", status_code=202)
def ai_retry(jid: str):
    j = ai.retry(jid)
    if not j:
        raise HTTPException(409, "only failed, cancelled or interrupted jobs can be retried")
    return j


@app.delete("/api/ai/jobs/{jid}")
def ai_remove(jid: str):
    if not ai.remove(jid):
        raise HTTPException(409, "job not found or still active")
    return {"deleted": jid}


@app.post("/api/ai/broll", status_code=202)
async def ai_broll(request: Request):
    """Queue a b-roll clip. Body: {prompt, negative?, seed?, preset: draft|final, orientation: portrait|landscape,
    assign?: {project, scene, dim?, blur?}}. An identical request reuses the library clip (no GPU work)."""
    d = _body(await request.body())
    prompt = str(d.get("prompt") or "").strip()
    if len(prompt) < 8:
        raise HTTPException(422, "prompt is too short (describe what the footage shows)")
    provider = d.get("provider") or PV.DEFAULT
    if not PV.exists(provider):
        raise HTTPException(422, f"unknown provider '{provider}' ({', '.join(PV.all_providers())})")
    pv = PV.get(provider)
    preset = d.get("preset") if d.get("preset") in pv["presets"] else "draft"
    orient = d.get("orientation") if d.get("orientation") in ("portrait", "landscape") else "portrait"
    try:
        seed = int(d.get("seed") if d.get("seed") not in (None, "") else 42)
    except (TypeError, ValueError):
        raise HTTPException(422, "seed must be an integer") from None
    params = {"prompt": prompt[:800], "negative": str(d.get("negative") or "").strip()[:800] or None, "seed": seed,
              "orientation": orient, "provider": provider}
    im = d.get("image") if isinstance(d.get("image"), dict) else None
    if im and "i2v" not in pv["modes"]:
        raise HTTPException(422, f"{pv['name']} cannot animate an image")
    if not im and "t2v" not in pv["modes"]:
        raise HTTPException(422, f"{pv['name']} needs an image to animate (image-to-video only)")
    if im:                       # "animate this image": {project, asset} or {portrait}
        ref = None
        if im.get("portrait"):
            ref = _portrait_ref(str(im["portrait"]))
        elif im.get("project") and im.get("asset"):
            ref = image_ref(Project.get(im["project"]), str(im["asset"]))
        if not ref:
            raise HTTPException(422, "image not found (an image asset of the project or a library portrait)")
        params["image"] = ref
    a = d.get("assign") if isinstance(d.get("assign"), dict) else None
    if a and a.get("project") and a.get("scene"):
        Project.get(a["project"])
        params["assign"] = {"project": a["project"], "scene": a["scene"],
                            **{k: float(a[k]) for k in ("dim", "blur") if isinstance(a.get(k), (int, float))}}
    project = params.get("assign", {}).get("project")
    img_sha = (params.get("image") or {}).get("sha")
    cid = PV.clip_key(provider, params["prompt"], seed, preset, orient, img_sha)
    label = f"{pv['name']} · {prompt[:56]}"
    if L.broll_get(cid):
        ai._assign_broll({"params": params}, cid)
        return ai.done_job("broll", params, {"clip": cid}, project=project, label=label, preset=preset)
    for j in ai.active(kind="broll"):
        jp = j["params"]
        if j["preset"] == preset and jp["prompt"] == params["prompt"] and jp["seed"] == seed and jp["orientation"] == orient \
                and (jp.get("provider") or PV.ORIGINAL) == provider and (jp.get("image") or {}).get("sha") == img_sha:
            return JSONResponse(j, status_code=409)
    return ai.submit("broll", params, project=project, label=label, units=pv["presets"][preset]["steps"], preset=preset)


def _portrait_ref(pid: str) -> dict | None:
    m = L.portrait_get(pid)
    if not m:
        return None
    f = L.PORTRAITS / pid / "portrait.png"
    return {"path": str(f), "sha": L.file_sha(f), "label": f"portrait {m.get('name') or pid}"}


@app.get("/api/ai/providers")
def ai_providers():
    """Video generators (b-roll): id, name, licence, capabilities, presets with estimates and VRAM needs."""
    return JSONResponse({"providers": PV.public(ai), "default": PV.DEFAULT,
                         "cloud": {"enabled": False, "note": "No paid cloud API is implemented — see README (Cloud providers)."}},
                        headers=NOCACHE)


# ---------------------------------------------------------------- AI music
@app.post("/api/ai/music", status_code=202)
async def ai_music(request: Request):
    """Generate an instrumental track (ACE-Step v1.5) into the music library.
    Body: {prompt, genre?, mood?, bpm?, duration? (s, 10-240, default 30), seed?, title?, project? (use it there)}."""
    d = _body(await request.body())
    prompt = str(d.get("prompt") or "").strip()[:400]
    genre, mood = str(d.get("genre") or "").strip()[:60], str(d.get("mood") or "").strip()[:80]
    if len(prompt) + len(genre) + len(mood) < 4:
        raise HTTPException(422, "describe the music (prompt, genre or mood)")
    try:
        dur = float(d.get("duration") or 30)
        bpm = int(d["bpm"]) if d.get("bpm") not in (None, "") else None
        seed = int(d.get("seed") if d.get("seed") not in (None, "") else 7)
    except (TypeError, ValueError):
        raise HTTPException(422, "duration, bpm and seed must be numbers") from None
    if not 10 <= dur <= 240:
        raise HTTPException(422, "duration must be 10–240 s")
    if bpm is not None and not 50 <= bpm <= 200:
        raise HTTPException(422, "bpm must be 50–200")
    spec = {"prompt": prompt or None, "genre": genre or None, "mood": mood or None, "bpm": bpm, "duration": round(dur, 1),
            "seed": seed}
    params = {**spec, "title": str(d.get("title") or "").strip()[:60] or None, "cache_key": MU.generate_key(spec)}
    if d.get("project"):
        Project.get(d["project"])
        params["assign_project"] = d["project"]
    label = (prompt or ", ".join(x for x in (genre, mood) if x))[:70]
    have = MU.generated_for(spec)
    if have:
        if params.get("assign_project"):
            p = Project.get(params["assign_project"])
            b = p.brief
            b["music"] = {**(b.get("music") if isinstance(b.get("music"), dict) else {}), "track": have["id"]}
            p.save_brief(B.normalize(b, p.assets())[0])
        return ai.done_job("music", params, {"track": have["id"], "title": have["title"]}, project=d.get("project"),
                           label=label, preset="final")
    return ai.submit("music", params, project=d.get("project"), label=label, units=dur, preset="final")


# ---------------------------------------------------------------- warm AI workers
@app.get("/api/ai/workers")
def ai_workers():
    """Warm model workers: settings, per worker {family, pid, busy, models [{key, gb}], ram_gb, vram_gb, idle_s}."""
    return JSONResponse(ai.pool.view(), headers=NOCACHE)


@app.put("/api/ai/workers/settings")
async def ai_workers_settings(request: Request):
    """{warm?: bool, idle_minutes?: 1-1440, ram_budget_gb?: 8-120}"""
    d = _body(await request.body())
    try:
        return ai.pool.update(d)
    except (TypeError, ValueError):
        raise HTTPException(422, "idle_minutes and ram_budget_gb must be numbers") from None


@app.post("/api/ai/workers/release-ram")
async def ai_workers_release(request: Request):
    """Stop the idle warm workers (all, or {family}) so their RAM is free again. Busy workers are left alone."""
    d = _body(await request.body())
    return {"stopped": ai.pool.release_ram(d.get("family")), **ai.pool.view()}


@app.post("/api/ai/workers/release-gpu")
def ai_workers_release_gpu():
    """Every idle warm worker gives back its GPU memory; the weights stay in RAM."""
    return {"released": ai.pool.release_gpu(), **ai.pool.view()}


# ---------------------------------------------------------------- ComfyUI
@app.get("/api/comfy/status")
def comfy_status(fresh: int = 0):
    """Managed/external ComfyUI: state (running / starting / stopped / error / external), reachable, busy, queue
    length, version, devices, VRAM of the ComfyUI process (monitor), last /free, settings, log tail."""
    return JSONResponse(ai.comfy.status(fresh=bool(fresh)), headers=NOCACHE)


@app.post("/api/comfy/start")
def comfy_start():
    """Start the managed ComfyUI (video/comfyui) on 127.0.0.1:<port>; returns at once (state 'starting')."""
    return ai.comfy.start()


@app.post("/api/comfy/stop")
def comfy_stop():
    """Stop the managed ComfyUI (refused while a ComfyUI job runs)."""
    cur = ai.jobs.get(ai.current) if ai.current else None
    if cur and ai._is_comfy(cur):
        raise HTTPException(409, "a ComfyUI job is running — cancel it first")
    return ai.comfy.stop()


@app.post("/api/comfy/free")
def comfy_free():
    """POST /free {unload_models, free_memory} to ComfyUI and report its VRAM before/after."""
    if not ai.comfy.reachable(2.0):
        raise HTTPException(409, "ComfyUI is not running")
    return ai.comfy.free(why="manual")


@app.put("/api/comfy/settings")
async def comfy_settings(request: Request):
    """{mode: managed|external, url, port, autostart, vram_mode: auto|lowvram|novram|highvram, reserve_vram_gb,
    custom_nodes, extra_args}. A running managed server keeps its flags until it is restarted."""
    try:
        return ai.comfy.update(_body(await request.body()))
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e)) from None


def _wf_view(m: dict, oi: dict | None) -> dict:
    _, g = CFW.load(m["id"])
    v = CFW.validate(g, m, oi)
    return {**m, "provider": PV.COMFY + m["id"], "validation": v,
            "preview_url": f"/api/comfy/workflows/{m['id']}/preview" if CFW.preview_file(m["id"]) else None}


@app.get("/api/comfy/workflows")
def comfy_workflows():
    """Workflow library (video/comfy-workflows) with validation against the running ComfyUI (when it answers)."""
    oi = ai.comfy.object_info_cached()
    return JSONResponse({"workflows": [_wf_view(m, oi) for m in CFW.list_all()], "checked": oi is not None,
                         "keys": list(CFW.KEYS), "capabilities": list(CFW.CAPS)}, headers=NOCACHE)


@app.get("/api/comfy/workflows/{wid}")
def comfy_workflow(wid: str):
    """Manifest + validation + the node list (for the mapping editor) + the API graph."""
    m, g = CFW.load(wid)
    oi = ai.comfy.object_info_cached()
    return JSONResponse({**_wf_view(m, oi), "nodes_list": CFW.nodes_view(g), "graph": g, "detected": CFW.detect(g)},
                        headers=NOCACHE)


@app.get("/api/comfy/workflows/{wid}/workflow_api.json")
def comfy_workflow_file(wid: str):
    return FileResponse(CFW.folder(wid) / "workflow_api.json", media_type="application/json",
                        filename=f"{wid}-workflow_api.json")


@app.get("/api/comfy/workflows/{wid}/preview")
def comfy_workflow_preview(wid: str):
    f = CFW.preview_file(wid)
    if not f:
        raise HTTPException(404, "no preview")
    return FileResponse(f, headers=NOCACHE)


MAX_WORKFLOW = 8 * 1024 * 1024
MAX_PREVIEW = 5 * 1024 * 1024


def _check_preview(data: bytes, fn: str) -> tuple[bytes, str]:
    """A workflow preview must really be a PNG / JPEG / WebP (it is served back with an image type)."""
    if len(data) > MAX_PREVIEW:
        raise CFW.FlowError(f"preview image too large (limit {MAX_PREVIEW // 2**20} MB)", 413)
    import io

    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
            fmt = (im.format or "").lower()
    except Exception:  # noqa: BLE001 - any decoder error = not an image
        raise CFW.FlowError(f"{fn}: not a valid image") from None
    ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}.get(fmt)
    if not ext:
        raise CFW.FlowError(f"{fn}: preview must be PNG, JPEG or WebP")
    return data, ext


async def _read_workflow_upload(request: Request) -> tuple[dict, dict, bytes | None, str]:
    ctype = request.headers.get("content-type", "")
    meta, preview, pext = {}, None, ".jpg"
    if ctype.startswith("multipart/form-data"):
        form = await request.form(max_files=3, max_fields=12, max_part_size=1024 * 1024)
        raw = None
        for key, val in form.multi_items():
            if isinstance(val, str):
                if key in ("name", "licence", "notes", "id", "commercial"):
                    meta[key] = val
                elif key == "workflow" and val.strip():
                    raw = val
                continue
            fn = (val.filename or "").lower()
            if fn.endswith(".json") or key == "workflow":
                raw = await val.read(MAX_WORKFLOW + 1)
                if len(raw) > MAX_WORKFLOW:
                    raise CFW.FlowError(f"workflow file too large (limit {MAX_WORKFLOW // 2**20} MB)", 413)
            elif fn.endswith((".png", ".jpg", ".jpeg", ".webp")):
                preview, pext = _check_preview(await val.read(MAX_PREVIEW + 1), fn)
        if raw is None:
            raise CFW.FlowError("no workflow file — upload the JSON from ComfyUI's Workflow → Export (API)")
        meta["commercial"] = str(meta.get("commercial") or "").lower() in ("1", "true", "on", "yes")
        return CFW.parse(raw), meta, preview, pext
    body = await request.body()
    try:
        d = json.loads(body or b"{}")
    except ValueError as e:
        raise CFW.FlowError(f"not valid JSON: {e}") from None
    if isinstance(d, dict) and "workflow" in d:
        meta = {k: d.get(k) if k == "commercial" else str(d.get(k))
                for k in ("name", "licence", "notes", "id", "commercial") if d.get(k) is not None}
        return CFW.parse(d["workflow"]), meta, None, pext
    return CFW.parse(d), meta, None, pext


@app.post("/api/comfy/workflows/detect")
async def comfy_workflow_detect(request: Request):
    """Dry run of an upload: detected mapping + validation (nothing is saved)."""
    g, meta, _, _ = await _read_workflow_upload(request)
    det = CFW.detect(g)
    m = CFW.new_manifest(g, meta, det)
    return {"detected": det, "manifest": m, "validation": CFW.validate(g, m, ai.comfy.object_info_cached()),
            "nodes_list": CFW.nodes_view(g)}


@app.post("/api/comfy/workflows", status_code=201)
async def comfy_workflow_upload(request: Request):
    """Upload a workflow (multipart: the API-format JSON + name?, licence?, notes?, commercial?, id?, preview image?).
    The input mapping is auto-detected; correct it with PUT. Custom nodes are never installed."""
    g, meta, preview, pext = await _read_workflow_upload(request)
    det = CFW.detect(g)
    m = CFW.new_manifest(g, meta, det)
    wid = str(meta.get("id") or "").strip().lower() or None
    m = CFW.save(g, m, wid, preview=preview, preview_ext=pext)
    return {**_wf_view(m, ai.comfy.object_info_cached()), "detected": det, "nodes_list": CFW.nodes_view(g)}


@app.put("/api/comfy/workflows/{wid}")
async def comfy_workflow_update(wid: str, request: Request):
    """Edit the manifest: name, licence, notes, commercial, capabilities, inputs {key: [{node, input}]}, outputs,
    presets {draft|final: {width, height, length, fps, steps?}}, min_vram_gb, ram_gb."""
    m = CFW.update(wid, _body(await request.body()))
    return _wf_view(m, ai.comfy.object_info_cached())


@app.post("/api/comfy/workflows/{wid}/validate")
def comfy_workflow_validate(wid: str):
    m, g = CFW.load(wid)
    oi = ai.comfy.object_info(force=True) if ai.comfy.reachable(2.0) else None
    return {"validation": CFW.validate(g, m, oi), "checked": oi is not None}


@app.delete("/api/comfy/workflows/{wid}")
def comfy_workflow_delete(wid: str):
    if any(PV.COMFY + wid == (j.get("provider") or "") for j in ai.active(kind="broll")):
        raise HTTPException(409, "a job of this workflow is queued or running")
    CFW.delete(wid)
    return {"deleted": wid}


# ---------------------------------------------------------------- system monitor
@app.get("/api/monitor")
def monitor(since: float = 0, procs: int = 1):
    """Samples of the last 30 minutes (1 Hz, or newer than `since`), stage markers, the latest sample, the
    process table, the warm workers and the sampler's own overhead. Sample keys: t, gu (GPU %), vt/vu (VRAM
    total/used MB), vo/va/vs (VRAM of Ovoz / AI / studio MB), tc (°C), pw (W), cpu (%), rt/ru (RAM total/used
    GB), ra/ro/rs (RAM of AI / Ovoz / studio GB), rw (RAM of warm model workers GB), act (running activities)."""
    out = {"samples": sampler.history(since), "markers": sampler.marker_list(), "now": sampler.latest,
           "info": sampler.info, "overhead": sampler.overhead(), "errors": sampler.err, "server_time": time.time()}
    if procs:
        out["procs"] = sampler.procs
        out["workers"] = ai.pool.view()
        out["current"] = {"pipeline": make.active(), "ai_job": ai.get(ai.current) if ai.current else None}
    return JSONResponse(out, headers=NOCACHE)


@app.get("/api/monitor/stream")
async def monitor_stream(request: Request, procs: int = 0):
    """Server-Sent Events: one `data: {sample}` per second (+ `procs` and open markers when ?procs=1)."""
    async def gen():
        last = 0.0
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                break
            s = await asyncio.to_thread(sampler.wait_next, last, 3.0)
            if not s or s.get("t", 0) <= last:
                continue
            last = s["t"]
            msg = {"s": s}
            if procs:
                msg["procs"] = sampler.procs
                msg["open"] = [m for m in sampler.marker_list() if not m["t1"]]
            yield "data: " + json.dumps(msg, separators=(",", ":")) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/api/library/broll")
def library_broll():
    used: dict[str, list] = {}
    for p in Project.all():
        try:
            b = p.brief
        except (OSError, ValueError):
            continue
        for s in b.get("scenes", []):
            bg = s.get("background") or {}
            if bg.get("clip"):
                used.setdefault(bg["clip"], []).append({"project": p.id, "title": b.get("title"), "scene": s["id"]})
    return JSONResponse([{**m, "used_in": used.get(m["id"], [])} for m in L.broll_list()], headers=NOCACHE)


@app.get("/api/library/broll/{cid}/{name}")
def library_broll_file(cid: str, name: str):
    return FileResponse(L.broll_file(cid, name), headers={"Cache-Control": "max-age=86400"})


@app.delete("/api/library/broll/{cid}")
def library_broll_delete(cid: str):
    """Delete a clip. Scenes that used it fall back to the brand gradient."""
    if not L.broll_get(cid):
        raise HTTPException(404, "clip not found")
    L.broll_delete(cid)
    return {"deleted": cid}


@app.get("/api/library/portraits")
def library_portraits():
    return JSONResponse(L.portrait_list(), headers=NOCACHE)


@app.get("/api/library/portraits/{pid}/{name}")
def library_portrait_file(pid: str, name: str):
    return FileResponse(L.portrait_file(pid, name))


@app.post("/api/library/portraits", status_code=201)
async def library_portrait_upload(request: Request):
    """multipart: one image + consent=true ("This is a fictional person or I have the person's permission"),
    optional name, optional project (use it as that project's presenter)."""
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise HTTPException(415, "send multipart/form-data")
    form = await request.form(max_files=2, max_fields=10, max_part_size=1024 * 1024)
    consent = str(form.get("consent") or "").lower() in ("1", "true", "yes", "on")
    if not consent:
        raise HTTPException(422, f"please confirm: {L.CONSENT_TEXT}")
    up = next((v for _, v in form.multi_items() if not isinstance(v, str)), None)
    if up is None:
        raise HTTPException(422, "no image file")
    with tempfile.TemporaryDirectory(prefix="studio-pt-") as tmp:
        src, name = await _spool(up, Path(tmp), 0)
        m = L.portrait_add(src, {"source": "upload", "consent": True, "original_name": name,
                                 "name": str(form.get("name") or Path(name).stem)[:60]})
    if form.get("project"):
        _set_portrait(str(form.get("project")), m["id"])
    return m


def _set_portrait(pid: str, portrait_id: str) -> None:
    p = Project.get(pid)
    b = p.brief
    pres = b.get("presenter") or {"enabled": True, "mode": "corner", "scenes": "all"}
    b["presenter"] = {**pres, "portrait": portrait_id}
    p.save_brief(B.normalize(b, p.assets())[0])


@app.delete("/api/library/portraits/{pid}")
def library_portrait_delete(pid: str):
    L.portrait_delete(pid)
    return {"deleted": pid}


@app.post("/api/ai/portrait", status_code=202)
async def ai_portrait(request: Request):
    """Generate a fictional presenter portrait. Body: {prompt, seed?, project? (use it there)}."""
    d = _body(await request.body())
    prompt = str(d.get("prompt") or "").strip()
    if len(prompt) < 8:
        raise HTTPException(422, "describe the (fictional) presenter")
    try:
        seed = int(d.get("seed") if d.get("seed") not in (None, "") else 1)
    except (TypeError, ValueError):
        raise HTTPException(422, "seed must be an integer") from None
    params = {"prompt": prompt[:600], "seed": seed, "steps": 40}
    if d.get("project"):
        Project.get(d["project"])
        params["assign_project"] = d["project"]
    return ai.submit("portrait", params, project=d.get("project"), label=prompt[:70], units=40, preset="final")


def _presenter_state(p: Project) -> dict | None:
    brief = B.localize(p.brief)             # the presenter speaks the main language
    tl = build.timeline(p, p.brief)
    pl = tl.get("_presenter")
    if not pl:
        return None
    est = {}
    for q in PR.PRESETS:
        miss = 0 if pl.get("error") else PR.plan(p, brief, tl, q)["missing_seconds"]
        est[q] = {"seconds": ai.estimate("presenter", q, miss) if miss else 0, "missing_seconds": miss}
    act = ai.active(project=p.id, kind="presenter")
    st = {k: pl.get(k) for k in ("mode", "enabled", "bubble", "full", "quality", "ready", "stale", "video", "error",
                                 "missing_seconds", "generate_seconds", "w", "h")}
    st.update(portrait=pl.get("portrait") or {}, estimate=est, job=act[0] if act else None,
              voiced=tl["voiced"], voice_needed=tl["voice_needed"],
              need_gb={q: ai.need_gb("presenter", q) for q in PR.PRESETS})
    return st


@app.get("/api/projects/{pid}/presenter")
def presenter_state(pid: str):
    return JSONResponse(_presenter_state(Project.get(pid)) or {"enabled": False}, headers=NOCACHE)


@app.post("/api/projects/{pid}/presenter", status_code=202)
async def presenter_generate(pid: str, request: Request):
    """Queue the talking head for this project's voice-over. Body: {quality?: draft|final}."""
    p = Project.get(pid)
    d = _body(await request.body())
    st = _presenter_state(p)
    if not st:
        raise HTTPException(409, "enable the presenter or add a 'presenter' scene first")
    if st.get("error"):
        raise HTTPException(409, st["error"])
    if st.get("job"):
        return JSONResponse(st["job"], status_code=409)
    q = d.get("quality") if d.get("quality") in PR.PRESETS else st["quality"]
    units = st["estimate"][q]["missing_seconds"]
    return ai.submit("presenter", {"quality": q}, project=pid, label=f"{p.brief.get('title')} · {q}",
                     units=units, preset=q)


@app.get("/api/projects/{pid}/presenter/{name}")
def presenter_file(pid: str, name: str):
    p = Project.get(pid)
    if not re.match(r"^[0-9a-f]{16}\.(webm|jpg)$", name or ""):
        raise HTTPException(404, "not found")
    f = PR.pdir(p) / name
    if not f.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(f)


# ---------------------------------------------------------------- "Make video" pipeline
@app.post("/api/projects/{pid}/make", status_code=202)
async def make_video(pid: str, request: Request):
    """Brief -> finished MP4 in one call: voice-over -> free GPU (Ovoz /api/unload) -> AI jobs -> render.
    Body (optional): {"quality": "final"|"draft" (AI quality, default final), "allow_silent": false}.
    409 + the running pipeline if one is already queued/running for this project."""
    Project.get(pid)
    d = _body(await request.body())
    act = make.active(pid)
    if act:
        return JSONResponse(act, status_code=409)
    q = d.get("quality") if d.get("quality") in ("draft", "final") else "final"
    return make.submit(pid, q, bool(d.get("allow_silent")))


@app.get("/api/projects/{pid}/make")
def make_status(pid: str, request: Request):
    Project.get(pid)
    return JSONResponse(I.tr_job(make.latest(pid), I.ui_lang(request)) or {}, headers=NOCACHE)


@app.get("/api/make")
def make_list(request: Request):
    lang = I.ui_lang(request)
    return JSONResponse([I.tr_job(it, lang) for it in make.list()], headers=NOCACHE)


@app.get("/api/make/{mid}")
def make_get(mid: str, request: Request):
    it = make.get(mid)
    if not it:
        raise HTTPException(404, "pipeline not found")
    return JSONResponse(I.tr_job(it, I.ui_lang(request)), headers=NOCACHE)


@app.post("/api/make/{mid}/cancel")
def make_cancel(mid: str):
    it = make.cancel(mid)
    if not it:
        raise HTTPException(404, "pipeline not found")
    return it


@app.post("/api/make/{mid}/retry", status_code=202)
def make_retry(mid: str):
    it = make.retry(mid)
    if not it:
        raise HTTPException(409, "only a failed, cancelled or interrupted pipeline can be retried (and none may be active)")
    return it


# ---------------------------------------------------------------- templates
@app.get("/api/templates")
def templates_list():
    """Installed templates (video/templates/<id>/), built-ins first; broken ones carry `error`."""
    return JSONResponse(TP.list_all(), headers=NOCACHE)


@app.get("/api/templates/schema")
def templates_schema():
    return TP.SCHEMA


@app.get("/api/templates/{tid}")
def templates_get(tid: str):
    return JSONResponse(TP.public(TP.load(tid)), headers=NOCACHE)


@app.post("/api/templates", status_code=201)
async def templates_import(request: Request, replace: int = 0):
    """Import a template zip (multipart field `file`, or the raw zip as the body). ?replace=1 updates an
    installed one. 422 {errors: [...]} lists every problem found."""
    ctype = request.headers.get("content-type", "")
    with tempfile.TemporaryDirectory(prefix="studio-tz-") as tmp:
        dest = Path(tmp) / "template.zip"
        size, limit = 0, 50 * 1024 * 1024
        if ctype.startswith("multipart/form-data"):
            form = await request.form(max_files=2, max_fields=5, max_part_size=1024 * 1024)
            up = next((v for _, v in form.multi_items() if not isinstance(v, str)), None)
            if up is None:
                raise TP.TemplateError("no file — send the template as a .zip")
            if not (up.filename or "").lower().endswith(".zip"):
                raise TP.TemplateError(f"{up.filename}: not a .zip file")
            replace = replace or str(form.get("replace") or "").lower() in ("1", "true", "on", "yes")
            with open(dest, "wb") as f:
                while chunk := await up.read(1 << 20):
                    size += len(chunk)
                    if size > limit:
                        raise TP.TemplateError("zip too large (limit 50 MB)", 413)
                    f.write(chunk)
        else:
            body = await request.body()
            if len(body) > limit:
                raise TP.TemplateError("zip too large (limit 50 MB)", 413)
            dest.write_bytes(body)
        m, warnings = TP.import_zip(dest, replace=bool(replace))
    return {**m, "warnings": warnings}


@app.delete("/api/templates/{tid}")
def templates_delete(tid: str):
    used = [p.id for p in Project.all() if (p.brief.get("template") == tid)]
    TP.delete(tid)
    return {"deleted": tid, "projects_now_default": used}


@app.get("/templates/{tid}/{path:path}", include_in_schema=False)
def template_file(tid: str, path: str):
    """Template files (CSS, JS, fonts, pictures) for the live preview; never cached (hot reload)."""
    d = TP.folder(tid).resolve()
    f = (d / path).resolve()
    if d not in f.parents or not f.is_file() or path.split("/")[0].startswith("."):
        raise HTTPException(404, "not found")
    mt = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
          ".svg": "image/svg+xml", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
    return FileResponse(f, media_type=mt.get(f.suffix.lower()), headers=NOCACHE)


@app.get("/api/ai-licences")
def ai_licences():
    return {"licences": LICENCES, "disclosure": DISCLOSURE}


# ---------------------------------------------------------------- static
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STUDIO / "static" / "index.html", headers=NOCACHE)


@app.get("/BRIEF.md", include_in_schema=False)
def brief_md():
    return PlainTextResponse((STUDIO / "BRIEF.md").read_text(encoding="utf-8"))


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=STUDIO / "static"), name="static")
app.mount("/renderer", StaticFiles(directory=VIDEO / "html"), name="renderer")
app.mount("/shared/fonts", StaticFiles(directory=VIDEO / "shared" / "fonts"), name="fonts")
