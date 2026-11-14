import edge_tts

from .base import Engine, Voice

# (voice id, UI label). The first two letters of the id are the language.
VOICES = [
    ("uz-UZ-MadinaNeural", "Madina (ayol)"),
    ("uz-UZ-SardorNeural", "Sardor (erkak)"),
    ("ru-RU-SvetlanaNeural", "Svetlana (ayol)"),
    ("ru-RU-DmitryNeural", "Dmitriy (erkak)"),
    ("en-US-AvaNeural", "Ava (ayol, AQSH)"),
    ("en-US-AndrewNeural", "Andrew (erkak, AQSH)"),
    ("en-US-EmmaNeural", "Emma (ayol, AQSH)"),
    ("en-US-BrianNeural", "Brian (erkak, AQSH)"),
    ("en-US-AriaNeural", "Aria (ayol, AQSH)"),
    ("en-US-GuyNeural", "Guy (erkak, AQSH)"),
    ("en-GB-SoniaNeural", "Sonia (ayol, Britaniya)"),
    ("en-GB-RyanNeural", "Ryan (erkak, Britaniya)"),
    ("ar-SA-ZariyahNeural", "Zariyah (ayol, Saudiya)"),
    ("ar-SA-HamedNeural", "Hamed (erkak, Saudiya)"),
    ("ar-AE-FatimaNeural", "Fatima (ayol, BAA)"),
    ("ar-AE-HamdanNeural", "Hamdan (erkak, BAA)"),
    ("ar-EG-SalmaNeural", "Salma (ayol, Misr)"),
    ("ar-EG-ShakirNeural", "Shakir (erkak, Misr)"),
]


class EdgeEngine(Engine):
    id = "edge"
    name = "Microsoft Neural (onlayn)"
    description = ("Microsoft Edge/Azure neyron ovozlari: oʻzbek, rus, ingliz va arab tillarida. "
                   "Internet kerak; matn Microsoft serveriga yuboriladi.")
    license = "Microsoft xizmati (norasmiy edge-tts)"
    commercial = True
    kind = "online"
    url = "https://github.com/rany2/edge-tts"
    supports_rate = supports_pitch = True
    # The service reads plain digits, but gets ordinals ("14-noyabr") and grouped
    # thousands ("15 000") wrong, so we spell numbers out for it too.
    spell_numbers = True
    languages = ("uz", "ru", "en", "ar")
    # Russian, English and Arabic voices read digits correctly (incl. Russian case endings).
    reads_digits = ("ru", "en", "ar")

    def voices(self):
        return [Voice(vid, name, lang=vid[:2]) for vid, name in VOICES]

    @property
    def loaded(self):
        return True

    async def synthesize_async(self, text, voice_id, rate=0, pitch=0):
        if voice_id not in {v.id for v in self.voices()}:
            raise ValueError(f"Unknown voice '{voice_id}'")
        communicate = edge_tts.Communicate(text, voice_id, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz")
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.extend(chunk["data"])
        return bytes(audio), "audio/mpeg"
