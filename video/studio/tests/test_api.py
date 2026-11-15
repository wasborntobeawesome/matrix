"""API smoke tests (FastAPI TestClient, in-process): projects, uploads, same-origin / host checks, security headers,
template + workflow upload validation, UI language. Ovoz is down (closed port); no job does GPU work."""
import json
import subprocess
import sys
import time

import pytest

from conftest import BASE, EXAMPLE, make_zip, manifest, png_bytes

EVIL = {"Origin": "https://evil.example"}


def test_health_and_static(client, server, monkeypatch):
    def down(*a, **k):
        raise server.build.V.TTSError("Ovoz is not running", unreachable=True)
    monkeypatch.setattr(server.build.V, "models", down)              # (a closed port takes ~2 s on Windows)
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ovoz"] is False and r.json()["preview_sandbox"] is True
    r = client.get("/")
    assert r.status_code == 200 and r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_create_from_example_update_and_history(client, example_project):
    p = example_project
    assert p["scenes"] >= 6 and p["assets"] == 5 and not [w for w in p["warnings"] if "not an uploaded asset" in w]
    d = client.get(f"/api/projects/{p['id']}").json()
    assert d["assets"]["add-expense.webm"]["type"] == "video"      # the recording was transcoded to VP9 WebM
    assert d["brief"]["scenes"][2]["asset"] == "add-expense.webm"  # and the brief reference rewritten
    b = d["brief"]
    b["title"] = "Edited title"
    b["scenes"][0]["layout"] = "nonsense"
    r = client.put(f"/api/projects/{p['id']}", json={"brief": b})
    assert r.status_code == 200
    assert r.json()["brief"]["title"] == "Edited title" and r.json()["brief"]["scenes"][0]["layout"] == "hero"
    assert any("layout 'nonsense' unknown" in w for w in r.json()["warnings"])
    assert "title" in r.json()["snapshot"]["summary"]
    hist = client.get(f"/api/projects/{p['id']}/history").json()
    assert hist[-1]["kind"] == "import" and hist[0]["kind"] == "edit"
    r = client.post(f"/api/projects/{p['id']}/history/{hist[-1]['id']}/restore")
    assert r.status_code == 200 and r.json()["brief"]["title"] == "Nimbo launch promo"
    assert client.get(f"/api/projects/{p['id']}/history/²").status_code == 404
    tl = client.get(f"/api/projects/{p['id']}/timeline").json()
    assert tl["template"]["base"] == "/templates/aurora/" and tl["asset_base"] == f"/api/projects/{p['id']}/files/"
    fu = client.get(f"/api/projects/{p['id']}/followup").json()
    assert fu["lang"] == "en" and fu["message"].startswith("Hi!")
    assert client.get(f"/api/projects/{p['id']}/followup?lang=uz").json()["message"].startswith("Salom!")


def test_ui_language_header(client, example_project):
    pid = example_project["id"]
    raw = {"product": "", "language": "xx", "scenes": [{"layout": "hero"}]}
    r = client.put(f"/api/projects/{pid}", json=raw, headers={"X-UI-Lang": "uz"})
    assert "mahsulot nomi yoʻq — 'Product' ishlatiladi" in r.json()["warnings"]
    r = client.get("/api/projects/nope-0000", headers={"X-UI-Lang": "uz"})
    assert r.status_code == 404 and r.json()["detail"] == "loyiha topilmadi"
    assert client.get("/api/projects/nope-0000?ui_lang=uz").json()["detail"] == "loyiha topilmadi"
    assert client.get("/api/projects/nope-0000").json()["detail"] == "project not found"
    h = client.get(f"/api/projects/{pid}/history", headers={"X-UI-Lang": "uz"}).json()
    assert any(x["summary"].startswith("Import qilindi") for x in h)
    r = client.post("/api/projects", content=b"{not json", headers={"Content-Type": "application/json", "X-UI-Lang": "uz"})
    assert r.status_code == 422 and r.json()["errors"][0].startswith("brif yaroqli JSON emas")


# ---------------------------------------------------------------- same-origin / host checks
@pytest.mark.parametrize("headers", [EVIL, {"Origin": "null"}, {"Origin": "http://127.0.0.1:9999"},
                                     {"Sec-Fetch-Site": "cross-site"}, {"Origin": "http://127.0.0.1:8001.evil.example"}])
def test_cross_origin_writes_refused(client, headers):
    r = client.post("/api/projects", json={"blank": True}, headers=headers)
    assert r.status_code == 403 and "refused" in r.json()["detail"]
    r = client.delete("/api/library/music/builtin-calm-pad", headers=headers)
    assert r.status_code == 403
    r = client.put("/api/comfy/settings", json={"url": "http://127.0.0.1:1"}, headers=headers)
    assert r.status_code == 403


def test_same_origin_and_tools_allowed(client):
    r = client.post("/api/projects", json={"blank": True, "product": "Same origin"}, headers={"Origin": BASE})
    assert r.status_code == 201
    r2 = client.post("/api/projects", json={"blank": True, "product": "curl"})            # no Origin: local tool
    assert r2.status_code == 201
    for pid in (r.json()["id"], r2.json()["id"]):
        assert client.delete(f"/api/projects/{pid}", headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.get("/api/projects", headers=EVIL).status_code == 200                   # reads: CORS protects them


@pytest.mark.parametrize("host", ["evil.example", "attacker.com:8001", "192.168.1.5:8001", "127.0.0.1.nip.io:8001"])
def test_dns_rebinding_hosts_refused(client, host):
    assert client.get("/api/projects", headers={"Host": host}).status_code == 403
    assert client.get("/", headers={"Host": host}).status_code == 403
    assert client.get("/api/projects", headers={"Host": "localhost:8001"}).status_code == 200


# ---------------------------------------------------------------- uploads
def upload(client, pid, files):
    return client.post(f"/api/projects/{pid}/assets", files=files)


def test_upload_validation(client, example_project):
    pid = example_project["id"]
    r = upload(client, pid, [("f", ("../../evil name!.png", png_bytes(1170, 2532), "image/png"))])
    assert r.status_code == 201 and r.json()["added"][0]["name"] == "evil-name.png"   # basename, safe characters
    r = upload(client, pid, [("f", ("fake.png", b"<html><script>alert(1)</script>", "image/png"))])
    assert r.status_code == 400 and "not a valid image" in r.json()["detail"]
    r = upload(client, pid, [("f", ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml"))])
    assert r.status_code == 400 and "unsupported file type" in r.json()["detail"]
    r = upload(client, pid, [("f", ("x.exe", b"MZ", "application/octet-stream"))])
    assert r.status_code == 400
    r = upload(client, pid, [("f", ("empty.png", b"", "image/png"))])
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    r = upload(client, pid, [("f", ("v.mp4", b"\x00\x00\x00\x18ftypmp42 not a video", "video/mp4"))])
    assert r.status_code == 400 and "not a readable video" in r.json()["detail"]
    r = upload(client, pid, [("f", ("jpg-as-png.png", png_bytes(10, 10), "image/png"))])
    assert r.status_code == 201
    r = client.post(f"/api/projects/{pid}/assets", content=b"x", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_body_limits(client, example_project):
    pid = example_project["id"]
    big = b"{" + b" " * (3 * 1024 * 1024) + b"}"
    r = client.put(f"/api/projects/{pid}", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    r = client.post("/api/projects", content=b"x", headers={"Content-Type": "application/json",
                                                              "Content-Length": str(2 * 1024 ** 3)})
    assert r.status_code == 413

    def chunks():                                                    # chunked: no Content-Length at all
        for _ in range(40):
            yield b" " * (1024 * 1024)
    r = client.put(f"/api/comfy/settings", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_ids_and_paths_in_urls(client, example_project):
    pid = example_project["id"]
    for url in (f"/api/projects/{pid}/files/..%2F..%2Fstudio%2Fserver.py", f"/api/projects/{pid}/files/..%5Cproject.json",
                f"/api/projects/{pid}/thumbs/..%2Fproject.json", f"/api/projects/{pid}/renders/..%2Fproject.json",
                "/api/projects/..%2F..%2Fstudio/files/server.py", "/api/projects/UPPER/files/x.png",
                "/api/library/music/..%2F..%2Fai-jobs.json/track.ogg", "/api/library/broll/..%2Ftmp/clip.mp4",
                "/api/comfy/workflows/..%2F..%2Fstudio/preview", f"/api/projects/{pid}/presenter/..%2Fproject.json",
                "/templates/aurora/..%2F..%2Fstudio%2Fserver.py", "/templates/..%2Fstudio/server.py"):
        r = client.get(url)
        assert r.status_code in (400, 404), (url, r.status_code)
        assert b"FastAPI" not in r.content


def test_template_files_are_sandboxed_and_cors(client):
    r = client.get("/templates/bold/template.js")
    assert r.status_code == 200 and r.headers["content-security-policy"] == "sandbox"
    assert r.headers["access-control-allow-origin"] == "*" and r.headers["content-type"].startswith("text/javascript")
    r = client.get("/renderer/index.html")
    assert r.headers["access-control-allow-origin"] == "*"
    assert "same-origin data script" in r.text
    assert client.get("/shared/fonts/Inter.ttf").headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-origin" not in client.get("/api/projects").headers


def test_template_import_api(client):
    files = {"manifest.json": manifest(tid="api-look", kind="theme")}
    r = client.post("/api/templates", files={"file": ("t.zip", make_zip({**files, "../../x.css": "x"}), "application/zip")})
    assert r.status_code == 422 and any("unsafe path" in e for e in r.json()["errors"])
    r = client.post("/api/templates", files={"file": ("t.txt", b"x", "text/plain")})
    assert r.status_code == 422
    r = client.post("/api/templates", files={"file": ("t.zip", make_zip(files), "application/zip")})
    assert r.status_code == 201 and r.json()["id"] == "api-look"
    assert client.post("/api/templates", files={"file": ("t.zip", make_zip(files), "application/zip")}).status_code == 409
    assert client.delete("/api/templates/api-look").status_code == 200
    assert client.delete("/api/templates/aurora").status_code == 409


def test_workflow_upload_api(client):
    g = json.loads((client.get("/api/comfy/workflows/wan22-5b-draft/workflow_api.json")).content)
    r = client.post("/api/comfy/workflows/detect", json={"workflow": g, "name": 42})
    assert r.status_code == 200 and r.json()["manifest"]["name"] == "42"
    r = client.post("/api/comfy/workflows", files={"workflow": ("w.json", json.dumps(g).encode(), "application/json"),
                                                    "preview": ("p.png", b"<svg onload=alert(1)>", "image/png")})
    assert r.status_code == 422 and "not a valid image" in r.json()["detail"]
    r = client.post("/api/comfy/workflows", json={"workflow": {str(i): {"class_type": "A", "inputs": {}} for i in range(2000)}})
    assert r.status_code == 422 and "too many nodes" in r.json()["detail"]
    r = client.post("/api/comfy/workflows", files={"workflow": ("w.json", json.dumps(g).encode(), "application/json"),
                                                    "preview": ("p.png", png_bytes(64, 64), "image/png")},
                    data={"name": "My flow", "licence": "Apache-2.0", "id": "my-flow"})
    assert r.status_code == 201 and r.json()["id"] == "my-flow" and r.json()["preview_url"]
    r = client.put("/api/comfy/workflows/my-flow", json={"inputs": "nope"})
    assert r.status_code == 422
    assert client.delete("/api/comfy/workflows/my-flow").status_code == 200


def test_comfy_settings_api(client):
    r = client.put("/api/comfy/settings", json={"mode": "external", "url": "http://evil.example:8188"})
    assert r.status_code == 422 and "only a ComfyUI on this PC" in r.json()["detail"]
    r = client.put("/api/comfy/settings", json={"extra_args": "--listen 0.0.0.0"})
    assert r.status_code == 422
    r = client.put("/api/comfy/settings", json={"mode": "managed", "url": "http://127.0.0.1:8188", "extra_args": "--lowvram"})
    assert r.status_code == 200 and r.json()["extra_args"] == "--lowvram"


def test_render_request_clamps_workers(client, server, example_project, monkeypatch):
    seen = {}
    monkeypatch.setattr(server.jobs, "submit", lambda kind, pid, **kw: seen.update(kw) or {"id": "x", **kw})
    r = client.post(f"/api/projects/{example_project['id']}/render", json={"workers": 100000})
    assert r.status_code == 202 and seen["workers"] == 16
    assert client.post(f"/api/projects/{example_project['id']}/render", json={"workers": "many"}).status_code == 422


def test_providers_default_and_legacy(client):
    d = client.get("/api/ai/providers").json()
    assert d["default"] == "fastwan-5b"
    legacy = {p["id"]: p.get("legacy", False) for p in d["providers"]}
    assert legacy["wan22-5b"] is True and legacy["fastwan-5b"] is False


# ---------------------------------------------------------------- resource leaks
def test_render_cancel_kills_the_whole_process_tree():
    import psutil

    import jobs
    child = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"
    proc = subprocess.Popen([sys.executable, "-c", child])
    for _ in range(50):
        kids = psutil.Process(proc.pid).children(recursive=True)
        if kids:
            break
        time.sleep(0.1)
    assert kids
    jobs.kill_tree(proc)
    time.sleep(0.5)
    assert proc.poll() is not None
    assert not any(k.is_running() and k.status() != psutil.STATUS_ZOMBIE for k in kids)
