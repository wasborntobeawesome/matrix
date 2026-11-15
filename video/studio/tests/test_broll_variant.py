"""B-roll for projects with both orientations (QA: the portrait clip was cropped hard in 16:9) and the seed the
clip was really made with (QA UX 9: the form showed 52109, Make video used 42)."""
import json

import pytest

import brief as B
import build
import library as L
import pipeline as PL
from store import Project


def _clip(cid, w, h, seed=42):
    d = L.BROLL / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "clip.webm").write_bytes(b"x")
    (d / "meta.json").write_text(json.dumps({"id": cid, "w": w, "h": h, "duration": 5.0, "seed": seed}))
    return cid


@pytest.fixture()
def project():
    raw = {"product": "Ovoz", "language": "en", "format": "9:16", "formats": ["9:16", "16:9"],
           "scenes": [{"id": "hook", "layout": "hero", "headline": "Ovoz", "say": "Hi.",
                       "background": {"type": "broll", "prompt": "ribbons", "clip": _clip("b-port000001", 720, 1280),
                                      "seed": 52109, "both": True}}]}
    p = Project.create(B.normalize(raw)[0], "api")
    yield p
    p.delete()


def test_variant_is_used_by_the_formats_it_fits(project):
    assert build.timeline(project, fmt="16:9")["scenes"][0]["background"]["clip"] == "b-port000001"   # crop, as before
    b = project.brief
    assert b["scenes"][0]["background"]["seed"] == 52109 and b["scenes"][0]["background"]["both"] is True
    b["scenes"][0]["background"]["clip_alt"] = _clip("b-land000001", 1280, 720)
    project.save_brief(B.normalize(b)[0])
    assert build.timeline(project, fmt="16:9")["scenes"][0]["background"]["clip"] == "b-land000001"
    assert build.timeline(project, fmt="9:16")["scenes"][0]["background"]["clip"] == "b-port000001"
    assert PL.other_orientation(project.brief) == "landscape"
    assert PL.other_orientation({"format": "9:16", "formats": ["9:16", "1:1"]}) is None


def test_assign_keeps_seed_and_variant(project, server):
    ai = server.ai
    ai._assign_broll({"params": {"seed": 7, "assign": {"project": project.id, "scene": "hook", "variant": True}}},
                     _clip("b-land000002", 1280, 720))
    bg = project.brief["scenes"][0]["background"]
    assert bg["clip"] == "b-port000001" and bg["clip_alt"] == "b-land000002" and bg["seed"] == 52109
    ai._assign_broll({"params": {"seed": 99, "assign": {"project": project.id, "scene": "hook"}}}, _clip("b-port000002", 720, 1280))
    bg = project.brief["scenes"][0]["background"]
    assert bg["clip"] == "b-port000002" and bg["seed"] == 99 and bg["clip_alt"] == "b-land000002" and bg["prompt"] == "ribbons"
