"""Build video/out/index.html: every rendered option side by side.

    video/.venv/Scripts/python video/compare.py
    # then open video/out/index.html in a browser
"""
import html
import json
from pathlib import Path

VIDEO = Path(__file__).resolve().parent
OUT = VIDEO / "out"

OPTIONS = [
    ("html", "HTML + Playwright + FFmpeg", "html", "Apache-2.0 / LGPL — free"),
    ("remotion", "Remotion (React)", "remotion", "Remotion License — free ≤3 people, else paid"),
    ("motion-canvas", "Motion Canvas", "motion-canvas", "MIT — free"),
    ("revideo", "Revideo", "revideo", "MIT — free"),
    ("moviepy", "MoviePy + Pillow", "moviepy", "MIT — free"),
    ("ai-broll", "AI b-roll (Wan 2.2)", "ai", "Apache-2.0 — free"),
    ("ai-avatar", "AI presenter", "ai", "see video/ai/README.md"),
]


def main() -> None:
    timeline = json.loads((VIDEO / "shared" / "build" / "timeline.json").read_text(encoding="utf-8"))
    cards = []
    for file, name, folder, licence in OPTIONS:
        mp4 = OUT / f"{file}.mp4"
        body = (f'<video src="{file}.mp4" controls preload="metadata" playsinline></video>'
                if mp4.exists() else '<div class="missing">not rendered</div>')
        size = f"{mp4.stat().st_size / 1e6:.1f} MB" if mp4.exists() else "—"
        cards.append(f"""<figure>{body}<figcaption><b>{html.escape(name)}</b>
<span>{html.escape(licence)}</span><span>{size} · <code>video/{folder}/</code></span></figcaption></figure>""")
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Video options</title>
<style>
:root{{color-scheme:dark;--bg:#0b1020;--card:#151b30;--fg:#f5f7ff;--mut:#9aa3c0}}
body{{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}}
h1{{margin:0 0 4px;font-size:22px}} p{{margin:0 0 20px;color:var(--mut)}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}}
figure{{margin:0;background:var(--card);border-radius:14px;overflow:hidden}}
video,.missing{{width:100%;aspect-ratio:9/16;display:block;background:#000}}
.missing{{display:grid;place-items:center;color:var(--mut)}}
figcaption{{padding:10px 12px;display:grid;gap:2px}} figcaption span{{color:var(--mut);font-size:13px}}
</style>
<h1>{html.escape(timeline['title'])} — one script, every renderer</h1>
<p>{timeline['width']}×{timeline['height']} · {timeline['fps']} fps · {timeline['duration']:.1f} s · voice: {html.escape(timeline['voice']['model'])} / {html.escape(timeline['voice']['voice'])}</p>
<main>{''.join(cards)}</main>
""", encoding="utf-8")
    print(OUT / "index.html")


if __name__ == "__main__":
    main()
