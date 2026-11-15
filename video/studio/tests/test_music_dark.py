"""Music defaults from the Ovoz QA run: volume_db -15 by default, "too dark" tracks (< 2 % energy above 2 kHz)
are marked and get a gentle high shelf in the mix unless the project turns it off."""
import numpy as np

import brief as B
import music as MU
from conftest import tone_wav


def test_default_volume_is_minus_15():
    assert B.MUSIC_DEFAULT["volume_db"] == -15.0
    b, _ = B.normalize({"product": "X", "language": "en", "scenes": [{"layout": "hero", "say": "hi"}], "music": "m-abc"})
    assert b["music"]["volume_db"] == -15.0 and "brighten" not in b["music"]
    b, _ = B.normalize({"product": "X", "language": "en", "scenes": [{"layout": "hero"}],
                        "music": {"track": "m-abc", "brighten": False}})
    assert b["music"]["brighten"] is False


def test_dark_track_is_marked_and_brightened(tmp_path):
    dark = MU.add_track(tone_wav(tmp_path / "dark.wav", 3.0, freqs=(80.0,) * 10 + (3000.0,), amp=0.5, channels=2),
                        {"title": "Dark", "licence": "CC0", "source": "generated", "tone": False})
    bright = MU.add_track(tone_wav(tmp_path / "bright.wav", 6.0, freqs=(220.0, 2600.0, 5200.0), amp=0.5, channels=2),
                          {"title": "Bright", "licence": "CC0", "source": "upload"})
    assert dark["dark"] is True and dark["hf"] < MU.DARK_HF
    assert bright["dark"] is False and bright["hf"] > 0.3
    assert MU.brighten_on({}, dark) and not MU.brighten_on({}, bright) and not MU.brighten_on({"brighten": False}, dark)
    # the shelf lifts the highs of what the mix uses
    lifted = MU.hf_fraction(MU.track_file(dark["id"]), af=MU.BRIGHTEN)
    assert lifted > dark["hf"]
    voice = tone_wav(tmp_path / "v.wav", 6.0, freqs=(200.0,), amp=0.3, gate=(1.0, 1.0))
    s_on = MU.mix(voice, MU.settings({"track": dark["id"]}), 6.0, tmp_path / "on.wav")
    s_off = MU.mix(voice, MU.settings({"track": dark["id"], "brighten": False}), 6.0, tmp_path / "off.wav")
    assert s_on["brightened"] is True and s_off["brightened"] is False
    k1 = MU.mix_key(voice, MU.settings({"track": dark["id"]}), 6.0)
    k2 = MU.mix_key(voice, MU.settings({"track": dark["id"], "brighten": False}), 6.0)
    assert k1 != k2
    # a lazily measured older meta (no "hf" yet) gets it on the first listing
    m = {k: v for k, v in dark.items() if k not in ("hf", "dark", "hf_version")}
    assert MU.brightness(m)["dark"] is True
    up = MU.add_track(tone_wav(tmp_path / "pad.wav", 2.0, freqs=(80.0,), amp=0.5), {"title": "Pad", "licence": "CC0"})
    assert up["hf"] < MU.DARK_HF and up["dark"] is False                    # uploads / builtins are not judged
    assert np.isfinite(s_on["lufs"])
