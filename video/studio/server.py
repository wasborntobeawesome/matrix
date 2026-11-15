"""Video Studio — local web app for promo videos from screenshots + an AI-written brief.

    video/.venv/Scripts/python -m uvicorn server:app --app-dir video/studio --host 127.0.0.1 --port 8001
    (or video/studio/start.bat)

UI at http://127.0.0.1:8001/ ; API under /api (see README.md).
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

import brief as B
import build
import inbox
import library as L
import presenter as PR
from aijobs import AI_PY, DISCLOSURE, LICENCES, AIJobs
from jobs import Jobs
from pipeline import Pipelines
from store import MAX_IMAGE, MAX_VIDEO, Project, StoreError, import_project, kind_of

STUDIO = Path(__file__).resolve().parent
VIDEO = STUDIO.parent
EXAMPLE = STUDIO / "example"
MAX_REQUEST = 1536 * 1024 * 1024

app = FastAPI(title="Video Studio", version="1.0",
              description="Promo videos from screenshots, recordings and an AI-written brief.")
jobs = Jobs()
try:
    L.import_existing()   # first start: the 3 existing Wan2.2 clips + the generated presenter portrait
except Exception as e:  # never block the studio on the AI library
    print("library import failed:", e)
ai = AIJobs()
make = Pipelines(jobs, ai)


@app.on_event("startup")
def _startup() -> None:
    inbox.start()


@app.exception_handler(StoreError)
def _store_error(_req, e: StoreError):
    return JSONResponse({"detail": str(e)}, status_code=e.status)


@app.exception_handler(B.BriefError)
def _brief_error(_req, e: B.BriefError):
    return JSONResponse({"detail": "invalid brief", "errors": e.errors}, status_code=422)


NOCACHE = {"Cache-Control": "no-store"}


# ---------------------------------------------------------------- meta
@app.get("/api/health")
def health():
    try:
        models = build.V.models(build.OVOZ, timeout=3)
        return {"studio": True, "ovoz": True, "ovoz_url": build.OVOZ, "ovoz_models": len(models)}
    except build.V.TTSError as e:
        return {"studio": True, "ovoz": False, "ovoz_url": build.OVOZ, "ovoz_error": str(e)}


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
                raw = B.blank(str(raw.get("product") or "My product")[:60], raw.get("language") or "en",
                              raw.get("format") or "9:16")
            files, source = [], request.query_params.get("source", "api")
        p, warnings = import_project(raw, files, source if source in ("api", "ui", "inbox") else "api")
    return {**p.summary(), "warnings": warnings, "url": f"/#/p/{p.id}"}


@app.post("/api/projects/example", status_code=201)
def create_example(format: str = "9:16"):
    """New project from video/studio/example (fictional app)."""
    raw = json.loads((EXAMPLE / "brief.json").read_text(encoding="utf-8"))
    if format in B.FORMATS:
        raw["format"] = format
    with tempfile.TemporaryDirectory(prefix="studio-ex-") as tmp:
        files = []
        for f in sorted(EXAMPLE.iterdir()):
            if kind_of(f.name):
                t = Path(tmp) / f.name
                shutil.copy2(f, t)
                files.append((t, f.name))
        p, warnings = import_project(raw, files, "ui")
    return {**p.summary(), "warnings": warnings}


def _detail(p: Project) -> dict:
    d = p.read()
    brief = d["brief"]
    voiced = {s["id"]: bool(build.scene_voice(p, brief, s)) for s in brief["scenes"]}
    _, warnings = B.normalize(brief, p.assets())
    return {**d, "assets": p.assets(), "renders": p.renders(), "voiced": voiced, "warnings": warnings,
            "jobs": jobs.for_project(p.id), "ai_jobs": ai.active(project=p.id), "make": make.latest(p.id),
            "presenter_state": _presenter_state(p)}


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _detail(Project.get(pid))


@app.put("/api/projects/{pid}")
async def update_project(pid: str, request: Request):
    """Replace the brief. Body: {"brief": {...}} or the brief itself."""
    p = Project.get(pid)
    body = _parse_brief(await request.body())
    raw = body.get("brief") if isinstance(body, dict) and isinstance(body.get("brief"), dict) else body
    clean, warnings = B.normalize(raw, p.assets())
    p.save_brief(clean)
    return {"brief": clean, "warnings": warnings, "updated": p.read()["updated"]}


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
    if not added and errors:
        raise HTTPException(400, "; ".join(errors))
    return {"added": added, "errors": errors, "assets": p.assets()}


@app.delete("/api/projects/{pid}/assets/{name}")
def delete_asset(pid: str, name: str):
    p = Project.get(pid)
    p.delete_asset(name)
    _, warnings = B.normalize(p.brief, p.assets())
    return {"deleted": name, "warnings": warnings}


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
def preview_timeline(pid: str):
    """Timeline for the live preview (estimated lengths for scenes without voice)."""
    p = Project.get(pid)
    tl = build.timeline(p)
    tl.pop("_parts", None)
    if tl.get("broll") or tl.get("presenter"):
        build.ai_urls(p, tl, "http")
    tl.pop("_presenter", None)
    tl["asset_base"] = f"/api/projects/{pid}/files/"
    tl["audio_url"] = f"/api/projects/{pid}/voiceover.wav?k={tl['audio_key']}" if tl["audio_key"] else None
    return JSONResponse(tl, headers=NOCACHE)


@app.get("/api/projects/{pid}/voiceover.wav")
def voiceover(pid: str):
    p = Project.get(pid)
    wav = build.voiceover(p, build.timeline(p))
    if not wav:
        raise HTTPException(404, "no voice-over yet")
    return FileResponse(wav, media_type="audio/wav", headers=NOCACHE)


@app.get("/api/projects/{pid}/voice/{scene_id}.wav")
def scene_clip(pid: str, scene_id: str):
    p = Project.get(pid)
    brief = p.brief
    s = next((s for s in brief["scenes"] if s["id"] == scene_id), None)
    if not s:
        raise HTTPException(404, "scene not found")
    f = build.scene_voice(p, brief, s)
    if not f:
        raise HTTPException(404, "no voice for this scene yet")
    return FileResponse(f, media_type="audio/wav", headers=NOCACHE)


@app.post("/api/projects/{pid}/voice", status_code=202)
async def make_voice(pid: str, request: Request):
    """Generate voice-over. Body (optional): {"scene": "<id>", "force": false}."""
    Project.get(pid)
    body = await request.body()
    prm = json.loads(body) if body.strip() else {}
    return jobs.submit("voice", pid, scene=prm.get("scene"), force=bool(prm.get("force")))


@app.post("/api/projects/{pid}/render", status_code=202)
async def make_render(pid: str, request: Request):
    """Queue a render. Body (optional): {"allow_silent": false, "skip_voice": false, "workers": 8}."""
    Project.get(pid)
    body = await request.body()
    prm = json.loads(body) if body.strip() else {}
    if (j := jobs.busy(pid, "render")):
        return JSONResponse(j, status_code=409)
    return jobs.submit("render", pid, allow_silent=bool(prm.get("allow_silent")),
                       skip_voice=bool(prm.get("skip_voice")), workers=int(prm.get("workers") or 8))


@app.get("/api/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    j = jobs.cancel(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str):
    return jobs.for_project(pid)


# ---------------------------------------------------------------- renders
@app.get("/api/projects/{pid}/renders")
def renders(pid: str):
    return Project.get(pid).renders()


@app.get("/api/projects/{pid}/renders/{name}")
def render_file(pid: str, name: str, download: int = 0):
    p = Project.get(pid)
    f = p.render_path(name)
    if download:
        return FileResponse(f, media_type="video/mp4", filename=f"{pid}-{name}")
    return FileResponse(f, media_type="video/mp4")


@app.delete("/api/projects/{pid}/renders/{name}")
def delete_render(pid: str, name: str):
    p = Project.get(pid)
    f = p.render_path(name)
    f.unlink()
    f.with_suffix(".json").unlink(missing_ok=True)
    return {"deleted": name}


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
        "presets": {
            "broll": {k: {"steps": v["steps"], "frames": v["frames"], "portrait": v["portrait"], "landscape": v["landscape"],
                          "seconds": round(v["frames"] / 24, 2), "estimate": ai.estimate("broll", k, v["steps"]),
                          "need_gb": ai.need_gb("broll", k), "measured": bool(tm.get(f"broll/{k}", {}).get("runs"))}
                      for k, v in L.BROLL_PRESETS.items()},
            "presenter": {k: {**v, "need_gb": ai.need_gb("presenter", k),
                              "per_second": tm.get(f"presenter/{k}", {}).get("per_unit"),
                              "load": tm.get(f"presenter/{k}", {}).get("load"),
                              "measured": bool(tm.get(f"presenter/{k}", {}).get("runs"))}
                          for k, v in PR.PRESETS.items()},
            "portrait": {"estimate": ai.estimate("portrait", "final", 40), "need_gb": ai.need_gb("portrait", "final")}},
        "licences": LICENCES, "disclosure": DISCLOSURE, "consent_text": L.CONSENT_TEXT,
    }, headers=NOCACHE)


@app.get("/api/ai/jobs")
def ai_jobs(project: str | None = None):
    return JSONResponse(ai.list(project=project), headers=NOCACHE)


@app.get("/api/ai/jobs/{jid}")
def ai_job(jid: str):
    j = ai.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return JSONResponse(j, headers=NOCACHE)


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
    preset = d.get("preset") if d.get("preset") in L.BROLL_PRESETS else "draft"
    orient = d.get("orientation") if d.get("orientation") in ("portrait", "landscape") else "portrait"
    try:
        seed = int(d.get("seed") if d.get("seed") not in (None, "") else 42)
    except (TypeError, ValueError):
        raise HTTPException(422, "seed must be an integer") from None
    pr = L.BROLL_PRESETS[preset]
    w, h = pr[orient]
    params = {"prompt": prompt[:800], "negative": str(d.get("negative") or "").strip()[:800] or None, "seed": seed,
              "orientation": orient}
    a = d.get("assign") if isinstance(d.get("assign"), dict) else None
    if a and a.get("project") and a.get("scene"):
        Project.get(a["project"])
        params["assign"] = {"project": a["project"], "scene": a["scene"],
                            **{k: float(a[k]) for k in ("dim", "blur") if isinstance(a.get(k), (int, float))}}
    project = params.get("assign", {}).get("project")
    cid = L.broll_key(params["prompt"], seed, pr["steps"], w, h, pr["frames"])
    if L.broll_get(cid):
        ai._assign_broll({"params": params}, cid)
        return ai.done_job("broll", params, {"clip": cid}, project=project, label=prompt[:70], preset=preset)
    for j in ai.active(kind="broll"):
        jp = j["params"]
        if j["preset"] == preset and jp["prompt"] == params["prompt"] and jp["seed"] == seed and jp["orientation"] == orient:
            return JSONResponse(j, status_code=409)
    return ai.submit("broll", params, project=project, label=prompt[:70], units=pr["steps"], preset=preset)


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
    brief = p.brief
    tl = build.timeline(p, brief)
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
def make_status(pid: str):
    Project.get(pid)
    return JSONResponse(make.latest(pid) or {}, headers=NOCACHE)


@app.get("/api/make")
def make_list():
    return JSONResponse(make.list(), headers=NOCACHE)


@app.get("/api/make/{mid}")
def make_get(mid: str):
    it = make.get(mid)
    if not it:
        raise HTTPException(404, "pipeline not found")
    return JSONResponse(it, headers=NOCACHE)


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
