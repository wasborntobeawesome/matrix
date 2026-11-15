"""Assemble AI b-roll clips + scene text + voice-over into video/out/ai-broll.mp4.

Usage (from video/ai):  .venv/Scripts/python assemble.py [--map hook=waves,languages=phone,...]

Each scene gets one b-roll clip (scaled/cropped to 1080x1920, slowed if shorter than the
scene, 30 fps), a darkening gradient, and ffmpeg drawtext overlays (harfbuzz shaping, so the
Arabic chip renders correctly). Scenes are joined with short crossfades that keep the
timeline in sync with voiceover.wav.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
VIDEO = HERE.parent
FFMPEG = str(VIDEO / "bin" / "ffmpeg.exe")
SHARED = VIDEO / "shared"
BUILD = HERE / "build"
CLIPS = HERE / "clips"
OUT = VIDEO / "out" / "ai-broll.mp4"
W, H, FPS = 1080, 1920, 30
XF = 0.4  # crossfade seconds

DEFAULT_MAP = {"hook": "waves", "languages": "phone", "models": "mic",
               "emotions": "waves", "cta": "mic"}


def static_font(weight: int) -> Path:
    p = BUILD / f"Inter-{weight}.ttf"
    if not p.exists():
        f = TTFont(SHARED / "fonts" / "Inter.ttf")
        instantiateVariableFont(f, {"wght": weight, "opsz": 32}, inplace=True)
        f.save(p)
    return p


def ff_path(p: Path) -> str:
    # drawtext option values: escape the drive colon and use forward slashes.
    return str(p).replace("\\", "/").replace(":", r"\:")


def strip_emoji(s: str) -> str:
    return re.sub(r"[\U0001F000-\U0001FFFF☀-➿️‍]", "", s).strip()


class Text:
    n = 0

    def __init__(self, bold, regular, arabic):
        self.fonts = {"bold": bold, "regular": regular, "arabic": arabic}

    def fit(self, text, font, size, maxw=960):
        while size > 30:
            w = ImageFont.truetype(str(self.fonts[font]), size).getlength(text)
            if w <= maxw:
                return size, w
            size -= 4
        return size, w

    def draw(self, text, y, size, color, font="bold", t0=0.0, x=None, alpha_max=1.0):
        """drawtext filter that fades/slides in at local time t0."""
        size, _ = self.fit(text, font, size)
        Text.n += 1
        tf = BUILD / f"txt{Text.n}.txt"
        tf.write_text(text, encoding="utf-8")
        a = f"{alpha_max}*min(1,max(0,(t-{t0})/0.35))"
        xexpr = x if x is not None else "(w-text_w)/2"
        yexpr = f"{y}+28*(1-min(1,max(0,(t-{t0})/0.35)))"
        return (f"drawtext=fontfile='{ff_path(self.fonts[font])}':textfile='{ff_path(tf)}':"
                f"expansion=none:fontsize={size}:fontcolor={color}:x={xexpr}:y='{yexpr}':"
                f"alpha='{a}':shadowcolor=black@0.55:shadowx=0:shadowy=4")

    def chip(self, text, cy, t0, fill, font="bold", size=58):
        size, tw = self.fit(text, font, size)
        bw, bh = int(tw + 96), 110
        x0, y0 = (W - bw) // 2, int(cy - bh / 2)
        box = (f"drawbox=x={x0}:y={y0}:w={bw}:h={bh}:color={fill}@0.55:t=fill:"
               f"enable='gte(t,{t0})'")
        return [box, self.draw(text, int(cy - size * 0.62), size, "#F5F7FF", font, t0)]


def scene_filters(sc, brand, T):
    acc, pri, txt, mut = brand["accent"], brand["primary"], brand["text"], brand["muted"]
    L = sc["layout"]
    f = []
    if L == "hero":
        f += [T.draw(sc["kicker"], 700, 44, acc, "bold", 0.15),
              T.draw(sc["headline"], 790, 132, txt, "bold", 0.35),
              T.draw(sc["sub"], 990, 60, mut, "regular", 0.8)]
    elif L == "chips":
        f.append(T.draw(sc["headline"], 520, 200, txt, "bold", 0.15))
        for i, it in enumerate(sc["items"]):
            font = "arabic" if re.search(r"[؀-ۿ]", it) else "bold"
            f += T.chip(it, 900 + i * 150, 0.9 + i * 0.75, pri if i % 2 == 0 else acc, font)
    elif L == "stat":
        f += [T.draw(sc["stat"], 520, 420, pri, "bold", 0.15),
              T.draw(sc["headline"], 1000, 110, txt, "bold", 0.5),
              T.draw(sc["sub"], 1160, 52, mut, "regular", 1.0)]
    elif L == "list":
        f.append(T.draw(sc["headline"], 560, 110, txt, "bold", 0.15))
        for i, it in enumerate(sc["items"]):
            f += T.chip(strip_emoji(it), 840 + i * 150, 1.0 + i * 0.8, pri if i % 2 == 0 else acc)
    elif L == "cta":
        f += [T.draw(sc["headline"], 760, 112, txt, "bold", 0.1),
              T.draw(sc["sub"], 930, 56, acc, "regular", 0.5),
              T.draw("Ovoz", 1500, 72, pri, "bold", 0.8)]
    return f


def probe_duration(p: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(p)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def make_vignette():
    p = BUILD / "shade.png"
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        # darker in the middle text band and at the bottom
        mid = max(0.0, 1 - abs(y - 1000) / 760)
        a = int(255 * min(0.6, 0.10 + 0.40 * mid))
        d.line([(0, y), (W, y)], fill=(11, 16, 32, a))
    img.save(p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="", help="scene=clip,... overrides")
    args = ap.parse_args()
    mapping = dict(DEFAULT_MAP)
    for kv in filter(None, args.map.split(",")):
        k, v = kv.split("=")
        mapping[k] = v

    BUILD.mkdir(exist_ok=True)
    tl = json.loads((SHARED / "build" / "timeline.json").read_text(encoding="utf-8"))
    T = Text(static_font(800), static_font(500), SHARED / "fonts" / "NotoSansArabic.ttf")
    shade = make_vignette()
    scenes = tl["scenes"]
    segs = []
    for i, sc in enumerate(scenes):
        dur = sc["end"] - sc["start"] + (XF if i < len(scenes) - 1 else 0)
        clip = CLIPS / f"{mapping[sc['id']]}.mp4"
        clen = probe_duration(clip)
        slow = max(1.0, dur / (clen - 0.05))
        # scene 'emotions' reuses a clip: play it reversed so it doesn't look repeated
        rev = ",reverse" if list(mapping.values()).index(mapping[sc["id"]]) != i else ""
        vf = (f"[0:v]setpts=PTS*{slow:.4f}{rev},scale={W}:-2:flags=lanczos,crop={W}:{H},"
              f"fps={FPS},eq=gamma=1.25:saturation=1.15,format=yuv420p[bg];[bg][1:v]overlay=0:0[v0];[v0]"
              + ",".join(scene_filters(sc, tl["brand"], T)) + "[v]")
        seg = BUILD / f"seg-{i}-{sc['id']}.mp4"
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip),
               "-loop", "1", "-i", str(shade), "-filter_complex", vf, "-map", "[v]",
               "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-crf", "16",
               "-preset", "medium", "-pix_fmt", "yuv420p", str(seg)]
        subprocess.run(cmd, check=True)
        segs.append(seg)
        print(f"segment {sc['id']}: {dur:.2f}s from {clip.name} (slow x{slow:.2f}{' rev' if rev else ''})")

    # crossfade chain; transition k starts exactly at scene k's start time
    inputs, fc, last = [], [], "[0:v]"
    for s in segs:
        inputs += ["-i", str(s)]
    for k in range(1, len(segs)):
        off = scenes[k]["start"]
        out = f"[x{k}]"
        fc.append(f"{last}[{k}:v]xfade=transition=fade:duration={XF}:offset={off:.3f}{out}")
        last = out
    audio_idx = len(segs)
    OUT.parent.mkdir(exist_ok=True)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-i", str(SHARED / "build" / "voiceover.wav"),
           "-filter_complex", ";".join(fc), "-map", last, "-map", f"{audio_idx}:a",
           "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-t", f"{tl['duration']:.3f}",
           "-movflags", "+faststart", str(OUT)]
    subprocess.run(cmd, check=True)
    print("wrote", OUT, f"{probe_duration(OUT):.2f}s")


if __name__ == "__main__":
    main()
