"""Build the shared inputs every renderer uses.

    video/.venv/Scripts/python video/shared/make_shared.py

Reads script.yaml, asks the running Ovoz app for one voice-over clip per scene,
and writes into video/shared/build/:
    scene-<id>.wav      48 kHz mono voice-over per scene
    voiceover.wav       all scenes joined, with each scene's padding
    timeline.json       scenes with start/end times + caption chunks with times

Caption timing is estimated from the length of each word (characters), which
is close enough for 2-4 word chunks. Renderers must not call the TTS themselves.
The TTS / WAV / caption helpers live in voicelib.py (also used by video/studio).
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

from voicelib import TTSError, caption_chunks, duration, join, to_wav, tts

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default=str(HERE / "script.yaml"))
    ap.add_argument("--out", default=str(HERE / "build"))
    args = ap.parse_args()

    spec = yaml.safe_load(Path(args.script).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    lang = spec.get("language", "uz")
    v = spec["voice"]

    t = 0.0
    scenes, captions, parts = [], [], []
    for s in spec["scenes"]:
        wav = out / f"scene-{s['id']}.wav"
        print(f"voice-over: {s['id']}", flush=True)
        try:
            audio = tts(s["say"], v["model"], v["voice"], lang, v.get("rate", 0), api=v["api"])
        except TTSError as e:
            sys.exit(str(e) + (" Start it with `uvicorn app:app`." if e.unreachable else ""))
        to_wav(audio, wav)
        speech = duration(wav)
        length = speech + s.get("pad", 0.4)
        scenes.append({**s, "start": round(t, 3), "end": round(t + length, 3),
                       "speech_end": round(t + speech, 3), "audio": wav.name})
        captions += caption_chunks(s["say"], t, speech, lang=lang)
        parts.append((wav, 0, s.get("pad", 0.4)))
        t += length

    # Join scenes with their padding into one voice-over track.
    join(parts, out / "voiceover.wav")

    timeline = {
        "title": spec["title"], "language": lang,
        "width": spec["width"], "height": spec["height"], "fps": spec["fps"],
        "duration": round(t, 3), "frames": round(t * spec["fps"]),
        "brand": spec["brand"], "voice": spec["voice"],
        "audio": "voiceover.wav", "scenes": scenes, "captions": captions,
    }
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"done: {len(scenes)} scenes, {t:.1f}s -> {out / 'timeline.json'}")


if __name__ == "__main__":
    main()
