"""ComfyUI: mapping auto-detection on the two sample workflows, upload validation, URL / argument rules.
No ComfyUI is started; nothing talks to the network."""
import copy
import json

import pytest

import comfy as C
import comfyflows as F

SAMPLES = ("wan22-5b-draft", "wan22-14b-lightx2v")


def mapping(inputs: dict) -> dict:
    return {k: sorted((t["node"], t["input"]) for t in v) for k, v in inputs.items()}


@pytest.mark.parametrize("wid", SAMPLES)
def test_detect_matches_shipped_manifest(wid):
    m, g = F.load(wid)
    det = F.detect(F.parse(json.dumps(g)))
    assert mapping(det["inputs"]) == mapping(m["inputs"])
    assert det["outputs"] == m["outputs"]
    assert set(det["capabilities"]) == set(m["capabilities"])
    v = F.validate(g, m, None)
    assert v["mapping_errors"] == [] and not v["checked"]


def test_detect_details():
    _, g5 = F.load("wan22-5b-draft")
    d5 = F.detect(g5)
    assert d5["capabilities"] == ["t2v", "i2v"]                     # the LoadImage start image is optional
    assert d5["values"]["width"] and d5["values"]["length"]
    _, g14 = F.load("wan22-14b-lightx2v")
    d14 = F.detect(g14)
    assert len(d14["inputs"]["seed"]) == 2                          # both samplers of the two-stage graph
    assert "steps" not in d14["inputs"]                             # multi-stage: the workflow keeps its own split
    assert any("multi-stage" in n for n in d14["notes"])


def test_fill_and_text_to_video_drop():
    m, g = F.load("wan22-5b-draft")
    out = F.fill(g, m, {"prompt": "ribbons", "seed": 7, "width": 480, "height": 832, "length": 49, "fps": 24.0,
                        "steps": 10, "image": None}, prefix="studio/abc")
    assert out["6"]["inputs"]["text"] == "ribbons" and out["3"]["inputs"]["seed"] == 7
    assert "56" not in out                                          # LoadImage removed for text-to-video
    assert out["58"]["inputs"]["filename_prefix"] == "studio/abc"
    assert g["6"]["inputs"]["text"] != "ribbons"                    # the stored graph is untouched


@pytest.mark.parametrize("raw,needle", [
    ({"nodes": [], "links": []}, "UI* format"),
    ([], "expected a ComfyUI API workflow"),
    ({"1": {"inputs": {}}}, "not an API-format node"),
    ({"../x": {"class_type": "A", "inputs": {}}}, "not a ComfyUI node id"),
    ({"1": {"class_type": "A\nB", "inputs": {}}}, "bad class_type"),
    ({"1": {"class_type": "A", "inputs": {f"i{k}": k for k in range(300)}}}, "too many inputs"),
    ({str(i): {"class_type": "A", "inputs": {}} for i in range(F.MAX_NODES + 1)}, "too many nodes"),
])
def test_parse_rejects_bad_graphs(raw, needle):
    with pytest.raises(F.FlowError) as e:
        F.parse(json.dumps(raw))
    assert needle in str(e.value)


def test_parse_strips_meta_and_cycles_do_not_recurse_forever():
    g = F.parse({"1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}, "_meta": {"title": "T", "evil": "y"}},
                 "2": {"class_type": "ImageScale", "inputs": {"image": ["3", 0]}},
                 "3": {"class_type": "ImageScale", "inputs": {"image": ["2", 0], "src": ["1", 0]}},
                 "4": {"class_type": "CLIPTextEncode", "inputs": {"text": "a", "clip": ["4", 0]}},
                 "5": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["6", 1], "seed": 1}},
                 "6": {"class_type": "WanImageToVideo", "inputs": {"positive": ["5", 0], "negative": ["6", 1]}}})
    assert g["1"]["_meta"] == {"title": "T"}
    d = F.detect(g)                                                 # cycles: no RecursionError
    assert "image" in d["inputs"]


def test_update_validates_types():
    m, g = F.load("wan22-5b-draft")
    for bad in ({"inputs": "x"}, {"capabilities": "t2v"}, {"presets": {"draft": "x"}}, {"presets": {"draft": {"width": "wide"}}},
                {"min_vram_gb": "lots"}, {"presets": {"draft": {"width": 99999}}}):
        with pytest.raises(F.FlowError):
            F.update("wan22-5b-draft", bad)
    assert F.load("wan22-5b-draft")[0] == m                         # nothing was written


@pytest.mark.parametrize("url,ok", [
    ("http://127.0.0.1:8188", True), ("http://localhost:9000/", True), ("https://127.0.0.2:1", True),
    ("http://[::1]:8188", True), ("http://192.168.1.20:8188", False), ("http://evil.example", False),
    ("javascript:alert(1)", False), ("http://user:pw@127.0.0.1:8188", False), ("http://127.0.0.1:8188/x?y", False),
    ("file:///C:/x", False), ("http://127.0.0.1:99999", False),
])
def test_comfy_url_loopback_only(url, ok):
    if ok:
        assert C.check_url(url)
    else:
        with pytest.raises(ValueError):
            C.check_url(url)


def test_comfy_remote_host_needs_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(C, "REMOTE_HOSTS", {"gpu-box.local"})
    assert C.check_url("http://gpu-box.local:8188") == "http://gpu-box.local:8188"
    with pytest.raises(ValueError):
        C.check_url("http://other.local:8188")


@pytest.mark.parametrize("args,ok", [
    ("--lowvram", True), ("--use-sage-attention --fast", True), ("--cache-lru 10", True), ("", True),
    ("--listen 0.0.0.0", False), ("--enable-cors-header *", False), ("--port=9", False),
    ("--output-directory C:/x", False), ("--front-end-root x", False), ("& calc", False), ("\"unbalanced", False),
])
def test_comfy_extra_args(args, ok):
    if ok:
        assert C.check_args(args) == args
    else:
        with pytest.raises(ValueError):
            C.check_args(args)


def test_settings_file_is_checked_on_load(tmp_root):
    C.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    C.SETTINGS_FILE.write_text(json.dumps({"mode": "external", "url": "http://evil.example", "extra_args": "--listen 0.0.0.0"}))
    try:
        s = C.Comfy._load(object.__new__(C.Comfy))
        assert s["url"] == C.DEFAULTS["url"] and s["extra_args"] == "" and s["mode"] == "external"
    finally:
        C.SETTINGS_FILE.unlink()


def test_new_manifest_coerces_meta():
    _, g = F.load("wan22-5b-draft")
    m = F.new_manifest(copy.deepcopy(g), {"name": 123, "licence": ["x"], "notes": None})
    assert m["name"] == "123" and m["licence"] == "['x']" and m["notes"] == ""
