"""Render a promo from timeline.json with HTML/CSS + headless Chromium.

    video/.venv/Scripts/python video/html/render.py [--timeline T] [--out O]
                                                    [--workers N] [--quality Q]
    video/.venv/Scripts/python video/html/render.py --project video/projects/<id> [--out O]
                                                    [--timeline DIR/build/timeline-<lang>-<fmt>.json]
                                                    [--cover T --cover-out cover.png]

--project DIR renders a Video Studio project: DIR/build/timeline.json (+ its
voiceover.wav, or silence when the timeline has no audio), assets from
DIR/assets/, output DIR/renders/<timestamp>.mp4 unless --out is given.
--progress prints machine-readable "@@PROGRESS {json}" lines (used by the studio).

How it works
  1. Analyse the voice-over (RMS + 24 log-spaced FFT bands per video frame) so
     the on-screen waveforms react to the actual voice.
  2. Write build/data.js (timeline + audio analysis) next to index.html.
  3. Open index.html?render in N headless Chromium pages. Each page owns every
     N-th frame: window.seek(t) sets all animated styles as a pure function of
     t, then the page is screenshotted as JPEG. No real-time recording.
  4. Frames are re-ordered and piped (image2pipe) into ffmpeg, which encodes
     H.264 yuv420p (BT.709) and muxes the voice-over as AAC, +faststart.

Isolation: the page is not opened from file:// (that needed --allow-file-access-from-files, which let any
template.js read every file on the disk). It is served from a virtual origin (http://render.studio.local)
by a Playwright route that answers only from the folders this render needs: the renderer, its fonts, and
the folders the timeline's file:// URIs point to (the project's assets, the template, the b-roll / presenter
media). data.js is served from memory. Every other request is aborted, and Chromium runs with a dead proxy
and no DNS, so template code cannot reach the network either.
"""
import argparse
import asyncio
import json
import mimetypes
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

import numpy as np
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parent
FFMPEG = VIDEO / "bin" / "ffmpeg.exe"
DEFAULT_TIMELINE = VIDEO / "shared" / "build" / "timeline.json"
DEFAULT_OUT = VIDEO / "out" / "html.mp4"


PROGRESS = False
VIRT = "http://render.studio.local"     # never resolved: every request is answered by Mounts.route()
NET_ARGS = [  # belt and braces: a request the route did not answer has nowhere to go
    "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-background-networking", "--no-pings",
]
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
         ".html": "text/html", ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".mp4": "video/mp4",
         ".webm": "video/webm", ".wav": "audio/wav", ".ogg": "audio/ogg", ".mp3": "audio/mpeg",
         ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}


def report(stage: str, done: int = 0, total: int = 0, **extra) -> None:
    if PROGRESS:
        print("@@PROGRESS " + json.dumps({"stage": stage, "done": done, "total": total, **extra}), flush=True)


class Mounts:
    """Folders the render page may read, each under a URL prefix of the virtual origin.

    A folder inside video/ keeps its path (video/html -> /html, video/shared/fonts -> /shared/fonts), so the
    renderer's relative URLs (../shared/fonts/Inter.ttf) work unchanged; other folders get /_ext<n>.
    """

    def __init__(self):
        self.roots: list[tuple[str, Path]] = []
        self.mem: dict[str, tuple[bytes, str]] = {}
        self.cache: dict[Path, bytes] = {}
        self.blocked: list[str] = []
        self.served = 0

    def add(self, d: Path) -> str:
        d = Path(d).resolve()
        for pre, r in self.roots:
            if r == d:
                return pre
        try:
            pre = "/" + d.relative_to(VIDEO.resolve()).as_posix()
        except ValueError:
            pre = f"/_ext{len(self.roots)}"
        self.roots.append((pre, d))
        self.roots.sort(key=lambda x: -len(x[0]))       # longest prefix first
        return pre

    def url_of(self, f: Path, is_dir: bool = False) -> str:
        f = Path(f).resolve()
        pre = self.add(f if is_dir else f.parent)
        return VIRT + pre + "/" + ("" if is_dir else f.name)

    def file_uri(self, uri: str) -> str:
        """file:///C:/x/y/ (a folder) or file:///C:/x/y.mp4 -> its virtual URL (mounting that folder)."""
        u = urlsplit(uri)
        p = Path(url2pathname(unquote(u.path)))
        if uri.endswith("/"):
            return self.url_of(p, is_dir=True)
        return self.url_of(p) + (f"?{u.query}" if u.query else "")

    def rewrite(self, v):
        """Every file:// URI in the timeline -> the virtual origin."""
        if isinstance(v, dict):
            return {k: self.rewrite(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.rewrite(x) for x in v]
        if isinstance(v, str) and v.startswith("file:"):
            return self.file_uri(v)
        return v

    def resolve(self, path: str) -> Path | None:
        path = unquote(path)
        for pre, root in self.roots:
            if path.startswith(pre + "/"):
                rel = path[len(pre) + 1:]
                if not rel or "\\" in rel or ":" in rel or any(x in ("", ".", "..") for x in rel.split("/")):
                    return None
                f = (root / rel).resolve()
                return f if root in f.parents and f.is_file() else None
        return None

    def _bytes(self, f: Path) -> bytes:
        b = self.cache.get(f)
        if b is None:
            b = f.read_bytes()
            if len(b) <= 256 * 2**20:
                self.cache[f] = b
        return b

    async def route(self, route) -> None:
        req = route.request
        url = req.url
        if not url.startswith(VIRT + "/"):
            self.blocked.append(url[:200])
            await route.abort("blockedbyclient")
            return
        path = urlsplit(url).path
        self.served += 1
        if path in self.mem:
            body, ctype = self.mem[path]
            await route.fulfill(status=200, body=body, headers={"Content-Type": ctype, "Cache-Control": "no-store"})
            return
        f = self.resolve(path)
        if f is None:
            await route.fulfill(status=404, body=b"not found", headers={"Content-Type": "text/plain"})
            return
        data = await asyncio.to_thread(self._bytes, f)
        ctype = TYPES.get(f.suffix.lower()) or mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        headers = {"Content-Type": ctype, "Accept-Ranges": "bytes", "Cache-Control": "max-age=3600"}
        n = len(data)
        rng = re.match(r"bytes=(\d*)-(\d*)$", (req.headers.get("range") or "").strip())
        if rng and (rng.group(1) or rng.group(2)) and n:
            if rng.group(1):
                a = int(rng.group(1))
                b = min(n - 1, int(rng.group(2))) if rng.group(2) else n - 1
            else:
                a, b = max(0, n - int(rng.group(2))), n - 1
            if a >= n or a > b:
                await route.fulfill(status=416, headers={"Content-Range": f"bytes */{n}"})
                return
            await route.fulfill(status=206, body=data[a:b + 1], headers={**headers, "Content-Range": f"bytes {a}-{b}/{n}"})
            return
        await route.fulfill(status=200, body=data, headers=headers)


async def open_context(p, W: int, H: int, mounts: Mounts, scale: float = 1):
    """Chromium without file access or network; pages of the returned context see only `mounts`."""
    browser = await p.chromium.launch(args=[
        "--force-color-profile=srgb", "--hide-scrollbars", "--font-render-hinting=none",
        "--disable-lcd-text", "--autoplay-policy=no-user-gesture-required", *NET_ARGS])
    ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=scale,
                                    service_workers="block", accept_downloads=False)
    await ctx.route("**/*", mounts.route)
    try:
        await ctx.route_web_socket(re.compile(".*"), lambda ws: asyncio.ensure_future(ws.close()))
    except AttributeError:      # older Playwright: the dead proxy still stops WebSockets
        pass
    return browser, ctx


def analyse_audio(wav, fps: int, frames: int, nbands: int = 24) -> dict:
    """Per-frame loudness + spectrum bands (0..1), smoothed like a VU meter."""
    if wav is None:  # silent video: no audio-reactive motion data
        return {"fps": fps, "rms": [], "bands": []}
    sr = 48000
    raw = subprocess.run(
        [str(FFMPEG), "-v", "error", "-i", str(wav), "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        check=True, capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = sr / fps
    win = 2048
    window = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(90, 7000, nbands + 1)
    idx = [np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0] for i in range(nbands)]
    rms = np.zeros(frames, np.float32)
    bands = np.zeros((frames, nbands), np.float32)
    for f in range(frames):
        c = int(f * hop + hop / 2)
        seg = x[max(0, c - win // 2): c + win // 2]
        if len(seg) < win:
            seg = np.pad(seg, (0, win - len(seg)))
        rms[f] = np.sqrt(np.mean(seg ** 2))
        mag = np.abs(np.fft.rfft(seg * window))
        bands[f] = [mag[i].mean() if len(i) else 0 for i in idx]
    # normalise: loudness-ish (log) per band against its own 98th percentile
    bands = np.log1p(bands * 4)
    ref = np.percentile(bands, 98, axis=0) + 1e-6
    bands = np.clip(bands / ref, 0, 1) ** 1.4
    rms = np.clip(rms / (np.percentile(rms, 98) + 1e-6), 0, 1)
    # fast attack, slower release
    for arr in (rms, bands):
        for f in range(1, frames):
            arr[f] = np.maximum(arr[f], arr[f - 1] * 0.72)
    return {"fps": fps,
            "rms": [round(float(v), 3) for v in rms],
            "bands": [[round(float(v), 3) for v in row] for row in bands]}


async def render(args) -> None:
    project = Path(args.project).resolve() if args.project else None
    if project and args.timeline == str(DEFAULT_TIMELINE):
        tl_path = project / "build" / "timeline.json"
    else:
        tl_path = Path(args.timeline).resolve()
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    W, H, fps = tl["width"], tl["height"], tl["fps"]
    frames = int(tl.get("frames") or round(tl["duration"] * fps))
    audio_name = tl.get("audio", "voiceover.wav")
    audio = (tl_path.parent / audio_name).resolve() if audio_name else None
    if audio is not None and not audio.exists():
        print(f"warning: {audio} not found, rendering with silence", file=sys.stderr)
        audio = None
    if args.out:
        out = Path(args.out).resolve()
    elif project:
        out = project / "renders" / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".mp4")
    else:
        out = DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    # screenshots / recordings referenced by the timeline (studio timelines carry file:// URIs; they are
    # rewritten to the virtual origin, which serves only the folders they name)
    if "asset_base" not in tl and (project or tl.get("assets")):
        tl["asset_base"] = (tl_path.parent / tl.get("assets_dir", "../assets")).resolve().as_uri() + "/"
    mounts = Mounts()
    mounts.add(HERE)
    mounts.add(VIDEO / "shared" / "fonts")
    tl = mounts.rewrite(tl)

    t_start = time.perf_counter()
    report("audio", 0, frames)
    # waveforms follow the voice-over even when the muxed audio is the voice + music mix
    vis = tl.get("analysis_audio")
    vis = (tl_path.parent / vis).resolve() if vis and (tl_path.parent / vis).exists() else audio
    aud = analyse_audio(vis, fps, frames)
    build = (project / "build") if project else HERE / "build"
    build.mkdir(parents=True, exist_ok=True)
    stem = tl_path.stem.replace("timeline", "data", 1) if project and tl_path.stem.startswith("timeline-") else "data"
    data_js = build / f"{stem}.js"
    data_js.write_text(
        "window.TIMELINE = " + json.dumps(tl, ensure_ascii=False) + ";\n"
        "window.AUDIO = " + json.dumps(aud, separators=(",", ":")) + ";\n", encoding="utf-8")
    mounts.mem["/__render/data.js"] = (data_js.read_bytes(), "text/javascript; charset=utf-8")
    url = mounts.url_of(HERE / "index.html") + "?render&data=" + VIRT + "/__render/data.js"

    if args.stills:
        sdir = Path(args.stills_dir) if args.stills_dir else ((project / "build" / "stills") if project else HERE / "preview")
        await stills(url, W, H, [float(x) for x in args.stills.split(",")], sdir, mounts)
        return

    audio_in = ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]

    ff = subprocess.Popen([
        str(FFMPEG), "-y", "-v", "error", "-stats",
        "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
        *audio_in,
        "-map", "0:v", "-map", "1:a",
        "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", str(args.crf),
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{frames / fps:.3f}",
        "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)

    n = max(1, min(32, args.workers))
    try:
        await capture(args, url, W, H, fps, frames, n, mounts, ff, t_start)
    except BaseException:
        # never leave an ffmpeg waiting on its stdin (or a half-written MP4) behind
        try:
            ff.kill()
        except OSError:
            pass
        ff.wait()
        out.unlink(missing_ok=True)
        raise

    report("encoding", frames, frames)
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("ffmpeg failed")
    print(f"served {mounts.served} request(s) from {len(mounts.roots)} folder(s)", file=sys.stderr)
    if mounts.blocked:
        print(f"blocked {len(mounts.blocked)} request(s) outside the render folders, e.g. {mounts.blocked[0]}",
              file=sys.stderr)
    report("done", frames, frames, out=str(out))
    el = time.perf_counter() - t_start
    print(f"\nwrote {out}  ({out.stat().st_size / 1e6:.2f} MB)  in {el:.1f}s "
          f"for {frames} frames ({frames / el:.1f} fps, {n} workers)")


async def capture(args, url: str, W: int, H: int, fps: int, frames: int, n: int, mounts: Mounts, ff,
                  t_start: float) -> None:
    done: dict[int, bytes] = {}
    cond = asyncio.Condition()

    async with async_playwright() as p:
        browser, ctx = await open_context(p, W, H, mounts)

        async def worker(k: int) -> None:
            page = await ctx.new_page()
            page.on("pageerror", lambda e: print(f"[page {k}] {e}", file=sys.stderr))
            await page.goto(url)
            await page.wait_for_function("window.__ready === true", timeout=60000)
            for i in range(k, frames, n):
                await page.evaluate("t => window.seek(t)", i / fps)
                buf = await page.screenshot(type="jpeg", quality=args.quality, caret="hide",
                                            clip={"x": 0, "y": 0, "width": W, "height": H})
                async with cond:
                    done[i] = buf
                    cond.notify_all()
            await page.close()

        report("frames", 0, frames)

        async def writer() -> None:
            for i in range(frames):
                async with cond:
                    await cond.wait_for(lambda: i in done)
                    buf = done.pop(i)
                await asyncio.to_thread(ff.stdin.write, buf)
                if i % 15 == 0 or i == frames - 1:
                    report("frames", i + 1, frames)
                if i % 60 == 0 or i == frames - 1:
                    el = time.perf_counter() - t_start
                    print(f"frame {i + 1}/{frames}  {el:5.1f}s  ({(i + 1) / el:4.1f} fps)", flush=True)

        try:
            await asyncio.gather(writer(), *(worker(k) for k in range(n)))
            if args.cover is not None and args.cover_out:
                # cover image: one still without captions / progress bar, as PNG
                page = await ctx.new_page()
                await page.goto(url + "&cover")
                await page.wait_for_function("window.__ready === true", timeout=60000)
                await page.evaluate("t => window.seek(t)", float(args.cover))
                await page.evaluate("t => window.seek(t)", float(args.cover))
                await page.screenshot(path=str(Path(args.cover_out).resolve()), type="png",
                                      clip={"x": 0, "y": 0, "width": W, "height": H})
                await page.close()
        finally:
            await browser.close()


async def stills(url: str, W: int, H: int, times: list, out_dir: Path, mounts: Mounts) -> None:
    """Quick look: screenshot single moments straight from the page (no video)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser, ctx = await open_context(p, W, H, mounts)
        try:
            page = await ctx.new_page()
            page.on("pageerror", lambda e: print(f"[page] {e}", file=sys.stderr))
            page.on("console", lambda m: print(f"[console] {m.text}", file=sys.stderr))
            await page.goto(url)
            await page.wait_for_function("window.__ready === true", timeout=60000)
            for t in times:
                await page.evaluate("t => window.seek(t)", t)
                f = out_dir / f"still_{t:06.2f}.png"
                await page.screenshot(path=str(f))
                print(f)
        finally:
            await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeline", default=str(DEFAULT_TIMELINE))
    ap.add_argument("--project", help="studio project folder (uses its build/timeline.json and assets/)")
    ap.add_argument("--out", help=f"output mp4 (default {DEFAULT_OUT} or <project>/renders/<timestamp>.mp4)")
    ap.add_argument("--workers", type=int, default=8, help="parallel Chromium pages (1-32)")
    ap.add_argument("--quality", type=int, default=94, help="JPEG quality of captured frames")
    ap.add_argument("--crf", type=int, default=18, help="x264 CRF")
    ap.add_argument("--stills", help="comma-separated seconds: save PNG stills to preview/ and exit")
    ap.add_argument("--stills-dir", help="where --stills writes PNGs")
    ap.add_argument("--progress", action="store_true", help="print @@PROGRESS json lines")
    ap.add_argument("--cover", type=float, help="also save a cover PNG of this moment (no captions / progress bar)")
    ap.add_argument("--cover-out", help="where --cover writes the PNG")
    args = ap.parse_args()
    global PROGRESS
    PROGRESS = args.progress
    asyncio.run(render(args))


if __name__ == "__main__":
    main()
