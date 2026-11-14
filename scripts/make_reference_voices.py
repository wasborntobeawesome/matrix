"""Create starter reference clips in voices/ for the voice-cloning models.

They are synthesised with the Microsoft voices, which gives us an exact transcript.
For production, replace them with your own recordings: voices/<id>.wav (3-10 s of
clean speech), voices/<id>.txt (exact transcript), voices/<id>.name (label shown in the UI).
"""
import asyncio
import io
import sys
from pathlib import Path

import edge_tts
import numpy as np
import soundfile as sf

VOICES = Path(__file__).resolve().parent.parent / "voices"
CLIPS = {
    "madina": ("uz-UZ-MadinaNeural", "Madina namunasi (ayol)",
               "Assalomu alaykum. Bugun sizga oʻzbek tilidagi eng soʻnggi yangiliklarni oʻqib beraman."),
    "sardor": ("uz-UZ-SardorNeural", "Sardor namunasi (erkak)",
               "Hurmatli tinglovchilar, xush kelibsiz. Endi ob-havo va sport xabarlariga oʻtamiz."),
}


async def main():
    VOICES.mkdir(exist_ok=True)
    for vid, (voice, label, text) in CLIPS.items():
        if (VOICES / f"{vid}.wav").exists() and "--force" not in sys.argv:
            print(f"skip {vid} (exists)")
            continue
        mp3 = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                mp3.extend(chunk["data"])
        audio, sr = sf.read(io.BytesIO(bytes(mp3)), dtype="float32")
        audio = np.pad(audio, (0, int(0.3 * sr)))  # short tail of silence
        sf.write(VOICES / f"{vid}.wav", audio, sr)
        (VOICES / f"{vid}.txt").write_text(text.replace("ʻ", "'") + "\n", encoding="utf-8")
        (VOICES / f"{vid}.name").write_text(label + "\n", encoding="utf-8")
        print(f"{vid}: {len(audio) / sr:.1f}s @ {sr} Hz")


asyncio.run(main())
