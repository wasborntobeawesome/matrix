"""Fetch the two optional extras render.py uses, into this folder only.

    video/.venv/Scripts/python video/moviepy/setup_deps.py

1. _dll/fribidi-0.dll  (LGPL-2.1, from conda-forge) -> turns on Pillow's bundled
   libraqm/HarfBuzz text layout on Windows, so Arabic is shaped + laid out RTL.
2. _vendor/ arabic-reshaper (MIT) + python-bidi (LGPL-3.0) -> fallback Arabic
   shaping when raqm is not available.

render.py works without either (Arabic would then render as unshaped letters).
"""
import io
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRIBIDI_URL = "https://conda.anaconda.org/conda-forge/win-64/fribidi-1.0.10-h8d14728_0.tar.bz2"


def main() -> None:
    dll_dir = HERE / "_dll"
    if sys.platform == "win32" and not (dll_dir / "fribidi-0.dll").exists():
        print("downloading fribidi-0.dll ...")
        data = urllib.request.urlopen(FRIBIDI_URL, timeout=120).read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tar:
            member = tar.getmember("Library/bin/fribidi-0.dll")
            dll_dir.mkdir(exist_ok=True)
            (dll_dir / "fribidi-0.dll").write_bytes(tar.extractfile(member).read())
    vendor = HERE / "_vendor"
    if not (vendor / "arabic_reshaper").exists():
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", str(vendor),
                        "arabic-reshaper", "python-bidi"], check=True)
    print("ok")


if __name__ == "__main__":
    main()
