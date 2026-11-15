"""Template manifests and zip import, including malicious zips (zip-slip, absolute paths, symlinks, device names,
bombs, code in theme templates)."""
import json
import struct

import pytest

import templates as TP
from conftest import make_zip, manifest

CSS = "#stage{outline:0}"
JS = "export default {};\n"


def imp(tmp_path, files, replace=False, symlinks=None, name="t.zip"):
    z = tmp_path / name
    z.write_bytes(make_zip(files, symlinks))
    return TP.import_zip(z, replace=replace)


def errors_of(tmp_path, files, **kw) -> list[str]:
    with pytest.raises(TP.TemplateError) as e:
        imp(tmp_path, files, **kw)
    return e.value.errors


def test_builtin_manifests_are_valid():
    for tid in TP.BUILTIN:
        m = TP.load(tid)
        assert m["id"] == tid
    assert [t["id"] for t in TP.list_all()][:3] == list(TP.BUILTIN)


@pytest.mark.parametrize("patch,needle", [
    ({"id": "Bad Id"}, "id must be"), ({"kind": "plugin"}, "kind must be"), ({"formats": ["4:3"]}, "formats must be"),
    ({"tokens": {"colors": {"primary": "red; background:url(x)"}}}, "tokens.colors.primary"),
    ({"tokens": {"shadows": {"card": "0 0 1px url(http://x)"}}}, "box-shadow"),
    ({"tokens": {"fonts": {"display": "</style><script>"}}}, "font family"),
    ({"name": "x" * 200}, "too long"), ({"options": [{"key": "a", "type": "text"}] * 41}, "at most 40"),
    ({"tokens": {"caption": {"style": "comic"}}}, "caption.style"), ({"default_transition": "warp"}, "default_transition"),
    ({"tokens": {"x": "y" * 40000}}, "tokens: too large"),
])
def test_manifest_validation(patch, needle):
    m = json.loads(manifest(kind="theme"))
    m.update(patch)
    errs, _ = TP.check_manifest(m)
    assert any(needle in e for e in errs), errs


def test_import_good_template_and_replace(tmp_path):
    files = {"my-look/manifest.json": manifest(), "my-look/template.css": CSS, "my-look/template.js": JS,
             "my-look/fonts/Brand.woff2": b"wOF2", "__MACOSX/._x": b"junk"}
    m, warns = imp(tmp_path, files)
    assert m["id"] == "my-look" and m["has_css"] and m["has_js"]
    assert (TP.ROOT / "my-look" / "fonts" / "Brand.woff2").read_bytes() == b"wOF2"
    assert not (TP.ROOT / "__MACOSX").exists()
    with pytest.raises(TP.TemplateError) as e:
        imp(tmp_path, files)
    assert e.value.status == 409
    m2, _ = imp(tmp_path, {**files, "my-look/template.css": CSS + "\n/* v2 */"}, replace=True)
    assert "v2" in (TP.ROOT / "my-look" / "template.css").read_text()
    assert not [p for p in TP.ROOT.iterdir() if p.name.startswith((".new-", ".old-"))]
    TP.delete("my-look")
    assert not (TP.ROOT / "my-look").exists()


@pytest.mark.parametrize("evil", [
    "../evil.css", "x/../../evil.css", "/abs.css", "\\\\server\\share\\x.css", "C:/Windows/evil.css", "C:evil.css",
    "a\\..\\..\\evil.css", "CON.css", "fonts/nul.woff2", "trailing.css.", "space .css ", "x\x01.css",
])
def test_zip_slip_and_unsafe_names_rejected(tmp_path, evil):
    errs = errors_of(tmp_path, {"manifest.json": manifest(kind="theme"), evil: "x"})
    assert errs and all("unsafe path" in e or "outside" in e or "not allowed" in e for e in errs), errs
    assert not (tmp_path.parent / "evil.css").exists()
    assert not (TP.ROOT / "my-look").exists()                     # nothing installed


def test_symlink_entry_rejected(tmp_path):
    errs = errors_of(tmp_path, {"manifest.json": manifest(kind="theme")}, symlinks={"bg.png": "C:/Windows/win.ini"})
    assert any("symbolic link" in e for e in errs)


@pytest.mark.parametrize("name", ["evil.html", "run.exe", "x.py", "page.htm", "a.bat"])
def test_file_types_rejected(tmp_path, name):
    errs = errors_of(tmp_path, {"manifest.json": manifest(kind="theme"), name: "x"})
    assert any("file type not allowed" in e for e in errs)


def test_case_duplicates_and_code_in_theme(tmp_path):
    errs = errors_of(tmp_path, {"manifest.json": manifest(kind="full"), "template.css": CSS, "Template.CSS": CSS})
    assert any("duplicate" in e for e in errs)
    errs = errors_of(tmp_path, {"manifest.json": manifest(kind="theme"), "template.js": JS})
    assert any("cannot contain code" in e for e in errs)
    errs = errors_of(tmp_path, {"manifest.json": manifest(tid="aurora", kind="theme")})
    assert any("built-in" in e for e in errs)


def test_zip_bombs_and_garbage(tmp_path):
    big = "0" * (TP.MAX_FILE + 10)                                  # compresses to almost nothing
    errs = errors_of(tmp_path, {"manifest.json": manifest(kind="full"), "template.css": big})
    assert any("too large" in e for e in errs)
    many = {"manifest.json": manifest(kind="theme"), **{f"p{i}.png": b"x" for i in range(TP.MAX_FILES + 1)}}
    errs = errors_of(tmp_path, many)
    assert any("too many files" in e for e in errs)
    (tmp_path / "junk.zip").write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(TP.TemplateError):
        TP.import_zip(tmp_path / "junk.zip")
    # an end-of-central-directory record that claims a million entries is refused before parsing
    fake = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 60000, 60000, 0, 0, 0)
    (tmp_path / "eocd.zip").write_bytes(fake)
    with pytest.raises(TP.TemplateError) as e:
        TP.import_zip(tmp_path / "eocd.zip")
    assert "too many files" in str(e.value)


def test_manifest_missing_or_nested_twice(tmp_path):
    errs = errors_of(tmp_path, {"readme.txt": "hi"})
    assert any("manifest.json not found" in e for e in errs)
    errs = errors_of(tmp_path, {"a/manifest.json": manifest(), "b/manifest.json": manifest()})
    assert any("manifest.json not found" in e for e in errs)


def test_safe_rel():
    assert TP._safe_rel("fonts/Inter.woff2") and TP._safe_rel("bg.png")
    for bad in ("", "..", "a//b", "a/./b", "x" * 200, "a/b/c/d/e/f/g/h.css", "COM1.txt", "lpt9", "aux.js", "con"):
        assert not TP._safe_rel(bad), bad
