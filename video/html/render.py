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
"""
import argparse
import asyncio
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from playwright.async_api import async_playwright

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parent
FFMPEG = VIDEO / "bin" / "ffmpeg.exe"
DEFAULT_TIMELINE = VIDEO / "shared" / "build" / "timeline.json"
DEFAULT_OUT = VIDEO / "out" / "html.mp4"


PROGRESS = False


def report(stage: str, done: int = 0, total: int = 0, **extra) -> None:
    if PROGRESS:
        print("@@PROGRESS " + json.dumps({"stage": stage, "done": done, "total": total, **extra}), flush=True)


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
    # screenshots / recordings referenced by the timeline are loaded from file://
    if "asset_base" not in tl and (project or tl.get("assets")):
        tl["asset_base"] = (tl_path.parent / tl.get("assets_dir", "../assets")).resolve().as_uri() + "/"

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
    url = (HERE / "index.html").as_uri() + "?render"
    if project:
        url += "&data=" + data_js.as_uri()

    if args.stills:
        sdir = Path(args.stills_dir) if args.stills_dir else ((project / "build" / "stills") if project else HERE / "preview")
        await stills(url, W, H, [float(x) for x in args.stills.split(",")], sdir)
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

    done: dict[int, bytes] = {}
    cond = asyncio.Condition()
    n = max(1, args.workers)

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=[
            "--force-color-profile=srgb", "--hide-scrollbars", "--font-render-hinting=none",
            "--disable-lcd-text", "--autoplay-policy=no-user-gesture-required"])

        async def worker(k: int) -> None:
            page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
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

        await asyncio.gather(writer(), *(worker(k) for k in range(n)))
        if args.cover is not None and args.cover_out:
            # cover image: one still without captions / progress bar, as PNG
            page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            await page.goto(url + "&cover")
            await page.wait_for_function("window.__ready === true", timeout=60000)
            await page.evaluate("t => window.seek(t)", float(args.cover))
            await page.evaluate("t => window.seek(t)", float(args.cover))
            await page.screenshot(path=str(Path(args.cover_out).resolve()), type="png",
                                  clip={"x": 0, "y": 0, "width": W, "height": H})
            await page.close()
        await browser.close()

    report("encoding", frames, frames)
    ff.stdin.close()
    if ff.wait() != 0:
        sys.exit("ffmpeg failed")
    report("done", frames, frames, out=str(out))
    el = time.perf_counter() - t_start
    print(f"\nwrote {out}  ({out.stat().st_size / 1e6:.2f} MB)  in {el:.1f}s "
          f"for {frames} frames ({frames / el:.1f} fps, {n} workers)")


async def stills(url: str, W: int, H: int, times: list, out_dir: Path) -> None:
    """Quick look: screenshot single moments straight from the page (no video)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--force-color-profile=srgb", "--font-render-hinting=none"])
        page = await browser.new_page(viewport={"width": W, "height": H})
        page.on("pageerror", lambda e: print(f"[page] {e}", file=sys.stderr))
        page.on("console", lambda m: print(f"[console] {m.text}", file=sys.stderr))
        await page.goto(url)
        await page.wait_for_function("window.__ready === true", timeout=60000)
        for t in times:
            await page.evaluate("t => window.seek(t)", t)
            f = out_dir / f"still_{t:06.2f}.png"
            await page.screenshot(path=str(f))
            print(f)
        await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--timeline", default=str(DEFAULT_TIMELINE))
    ap.add_argument("--project", help="studio project folder (uses its build/timeline.json and assets/)")
    ap.add_argument("--out", help=f"output mp4 (default {DEFAULT_OUT} or <project>/renders/<timestamp>.mp4)")
    ap.add_argument("--workers", type=int, default=8, help="parallel Chromium pages")
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
