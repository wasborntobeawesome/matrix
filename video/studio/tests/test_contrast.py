"""Text contrast over AI b-roll (QA #4): the editor's placeholder background, validate.py's contrast maths, and
(slow, opt-in) every built-in template measured over the sample frame."""
import asyncio
import sys

import pytest

from conftest import VIDEO

sys.path.insert(0, str(VIDEO / "templates"))
import validate as VAL  # noqa: E402


def test_contrast_ratio_maths():
    import numpy as np
    assert VAL.contrast_ratio(np.array([255, 255, 255]), np.array([0, 0, 0])) == pytest.approx(21.0, abs=0.01)
    assert VAL.contrast_ratio(np.array([118, 118, 118]), np.array([255, 255, 255])) == pytest.approx(4.54, abs=0.02)
    # white text over a background that is dark except for one bright "bokeh" strip: the p10 catches the strip
    bg = np.zeros((100, 100, 3), np.uint8)
    bg[:, :15] = 240
    r = VAL.text_contrast(bg, (255, 255, 255, 1.0), (0, 0, 100, 100))
    assert r["p10"] < 1.2 and r["mean_bg_luma"] > 0.1
    r = VAL.text_contrast(bg, (255, 255, 255, 1.0), (40, 0, 100, 100))
    assert r["p10"] == pytest.approx(21.0, abs=0.01)
    # half-transparent text is blended over each pixel
    assert VAL.text_contrast(bg, (255, 255, 255, 0.5), (40, 0, 100, 100))["p10"] < 6
    assert VAL.contrast_level(138, 700) == "large" and VAL.contrast_level(46, 450) == "normal"
    assert VAL.contrast_level(58, 650) == "large"


def test_sample_frame_is_busy_mid_tone():
    import numpy as np
    from PIL import Image
    assert VAL.SAMPLE_FRAME.exists(), "run: video/templates/validate.py --make-sample"
    a = np.asarray(Image.open(VAL.SAMPLE_FRAME).convert("RGB"))
    lum = VAL.luminance(a)
    assert 0.1 < lum.mean() < 0.5 and np.percentile(lum, 90) > 0.6     # bright bokeh on a mid-dark base


def _broll_project(client, **bg):
    pid = client.post("/api/projects", json={"blank": True, "product": "Contrast"}).json()["id"]
    b = client.get(f"/api/projects/{pid}").json()["brief"]
    b["template"] = "clean"
    b["scenes"][0]["background"] = {"type": "broll", "prompt": "bokeh ribbons", "dim": 0.5, **bg}
    assert client.put(f"/api/projects/{pid}", json={"brief": b}).status_code == 200
    return pid


def test_preview_shows_placeholder_until_the_clip_exists(client, server):
    pid = _broll_project(client)
    tl = client.get(f"/api/projects/{pid}/timeline").json()
    bg = tl["scenes"][0]["background"]
    assert bg == {"type": "broll", "placeholder": True, "dim": 0.5, "blur": 0}
    assert tl["broll_placeholder"] == "/renderer/broll-placeholder.jpg" and tl["broll"] is True
    r = client.get(tl["broll_placeholder"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    # a clip id that is not in the library: still the placeholder in the preview
    pid2 = _broll_project(client, clip="b-0000000000")
    assert client.get(f"/api/projects/{pid2}/timeline").json()["scenes"][0]["background"]["placeholder"] is True


def test_renders_never_get_the_placeholder(client, server):
    import build
    from store import Project
    pid = _broll_project(client)
    p = Project.get(pid)
    tl = build.timeline(p)                         # what renders (write_render_timeline) use
    assert "background" not in tl["scenes"][0] and not tl.get("broll")
    tl = build.write_render_timeline(p)
    assert "background" not in tl["scenes"][0] and "broll_placeholder" not in tl


@pytest.mark.slow
@pytest.mark.parametrize("tid", ["clean", "aurora", "bold"])
def test_templates_keep_text_readable_over_footage(tid):
    """Every text of every layout over the sample frame (dim 0.5): >= 4.5:1 body, >= 3:1 large (en, 3 formats)."""
    import templates as T
    m = T.read_manifest(T.ROOT / tid)
    info = T._derive(m, T.ROOT / tid)
    tinfo = {"id": m["id"], "name": m.get("name"), "kind": m["kind"], "version": info["version_hash"],
             "tokens": m.get("tokens") or {}, "options": T.option_values(m, {})[0],
             "default_transition": m.get("default_transition") or "lift", "base": (T.ROOT / tid).resolve().as_uri() + "/"}
    if info.get("css_file"):
        tinfo["css"] = info["css_file"]
    if info.get("js_file"):
        tinfo["js"] = info["js_file"]
    rep = asyncio.run(VAL.render_checks(tinfo, ["en"], None, None, tid, VAL.frame_from(None, VAL.HERE / ".validate-tmp")))
    assert len(rep["contrast"]) > 40
    bad = [c for c in rep["contrast"] if not c["ok"]]
    assert not bad, bad
