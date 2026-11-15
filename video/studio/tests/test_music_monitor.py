"""Music: settings defaults, the voice + music mix (loudness, peak, ducking), tone shaping of generated tracks.
Monitor: history ring file survives a restart."""
import json
import time

import numpy as np
import pytest

import music as MU
import sysmon
from conftest import tone_wav


@pytest.fixture(scope="module")
def track(tmp_path_factory):
    """A 12 s 'music' track (two tones + a 45 Hz rumble, i.e. bass-heavy) in the library."""
    d = tmp_path_factory.mktemp("mu")
    src = tone_wav(d / "music.wav", 12.0, freqs=(45.0, 330.0, 660.0), amp=0.5, channels=2)
    return MU.add_track(src, {"title": "Test", "licence": "CC0", "source": "upload"})


def test_settings_defaults(track):
    assert MU.settings(None) is None and MU.settings({"track": "m-nope000"}) is None
    s = MU.settings({"track": track["id"], "volume_db": -12})
    assert s["volume_db"] == -12 and s["duck"] is True and s["duck_db"] == -10.0 and s["fade_out"] == 2.5


def test_speech_gate_and_duck_curve():
    sr = MU.SR
    t = np.arange(4 * sr) / sr
    voice = np.where((t % 2) < 1, 0.3 * np.sin(2 * np.pi * 200 * t), 0.0)     # 1 s speech, 1 s pause
    gate, hop = MU.speech_gate(voice)
    assert gate[int(0.5 / hop)] == 1 and gate[int(1.7 / hop)] == 0
    curve = MU.duck_curve(gate, hop, -10.0)
    assert curve[int(0.9 / hop)] == pytest.approx(-10, abs=0.5) and curve[int(1.8 / hop)] > -5   # released in the pause
    assert gate[int(1.2 / hop)] == 1 and gate[int(1.9 / hop)] == 1     # hold 0.30 s after, look-ahead 0.15 s before


def test_mix_loudness_peak_and_ducking(track, tmp_path):
    voice = tone_wav(tmp_path / "voice.wav", 8.0, freqs=(180.0, 360.0), amp=0.4, gate=(1.0, 1.0))
    m = MU.settings({"track": track["id"]})
    out = tmp_path / "mix.wav"
    st = MU.mix(voice, m, 8.0, out, stems=True)
    assert abs(st["lufs"] - MU.TARGET) <= 1.0                      # -14 LUFS
    assert st["true_peak"] <= MU.TRUE_PEAK + 0.6
    assert 0.3 < st["speech_fraction"] < 0.8
    assert st["music_gain_db"] == pytest.approx(MU.MUSIC_REF - track["lufs"] + m["volume_db"], abs=0.01)
    mus = MU.decode(out.with_suffix(".music.wav"), 1)[:, 0]
    sr = MU.SR
    rms = lambda a, b: float(np.sqrt(np.mean(mus[int(a * sr):int(b * sr)] ** 2)))
    ducked, free = rms(2.2, 2.8), rms(3.4, 3.8)                     # speech 2-3 s, pause 3-4 s
    assert 20 * np.log10(free / ducked) > 4                         # music is clearly lower under the voice
    assert MU.mix_key(voice, m, 8.0) == MU.mix_key(voice, m, 8.0)
    assert MU.mix_key(voice, {**m, "volume_db": -10}, 8.0) != MU.mix_key(voice, m, 8.0)


def test_mix_without_voice_and_fades(track, tmp_path):
    out = tmp_path / "m.wav"
    st = MU.mix(None, MU.settings({"track": track["id"], "fade_in": 2.0}), 20.0, out)   # longer than the track: loops
    x = MU.decode(out, 1)[:, 0]
    assert len(x) == pytest.approx(20 * MU.SR, abs=10)
    assert np.abs(x[: int(0.05 * MU.SR)]).max() < np.abs(x[int(5 * MU.SR):int(6 * MU.SR)]).max() * 0.2
    assert abs(st["lufs"] - MU.TARGET) <= 1.0


def test_preview_info(track, tmp_path):
    voice = tone_wav(tmp_path / "v.wav", 3.0, amp=0.4)
    info = MU.preview_info(MU.settings({"track": track["id"]}), voice, 3.0)
    assert info["url"].startswith(f"/api/library/music/{track['id']}/track") and info["duck_step"] == 0.1
    assert len(info["duck"]) == pytest.approx(30, abs=2) and min(info["duck"]) < -5


def test_generated_track_is_tone_shaped_and_original_kept(tmp_path):
    src = tone_wav(tmp_path / "gen.wav", 10.0, freqs=(30.0, 50.0, 440.0), amp=0.5, channels=2)
    m = MU.add_track(src, {"title": "Gen", "licence": MU.GENERATED_LICENCE, "source": "generated"})
    d = MU.MUSIC / m["id"]
    assert (d / "original.wav").exists() and (d / "track.wav").exists()
    tone = m["tone"]
    assert tone["highpass_hz"] == 38.0 and tone["shelf_db"] < 0
    assert tone["sub_db_after"] < tone["sub_db_before"] - 3        # the sub-bass drops (ffmpeg astats)
    assert abs(tone["lufs_after"] - tone["lufs_before"]) <= 1.0     # loudness kept (make-up gain)
    assert m["lufs"] == pytest.approx(tone["lufs_after"], abs=0.1)
    up = MU.add_track(src, {"title": "Up", "licence": "CC0", "source": "upload", "id": "m-upload-test"})
    assert "tone" not in up and not (MU.MUSIC / up["id"] / "original.wav").exists()   # uploads are untouched
    MU.delete(m["id"])
    MU.delete(up["id"])


# ---------------------------------------------------------------- monitor history persistence
def test_monitor_history_survives_restart(tmp_path, monkeypatch):
    f = tmp_path / "monitor-history.jsonl"
    s1 = sysmon.Sampler(persist=f, start=False)
    now = time.time()
    old = {"t": round(now - sysmon.HISTORY_S - 60, 1), "cpu": 1.0}           # older than 30 min: not restored
    samples = [old] + [{"t": round(now - 100 + i, 1), "cpu": float(i), "ru": 3.0} for i in range(50)]
    s1.hist.extend(samples)
    s1.markers.append({"key": "job:1", "label": "Render · x", "cat": "render", "t0": now - 90, "t1": None})
    assert s1.flush(force=True) == 51
    assert s1.flush(force=True) == 0                                         # nothing new
    s1.hist.append({"t": round(now - 49, 1), "cpu": 99.0})
    assert s1.flush(force=True) == 1
    with open(f, "a", encoding="utf-8") as fh:
        fh.write('{"t": 12')                                                  # torn last line (crash)
    s2 = sysmon.Sampler(persist=f, start=False)
    h = s2.history()
    assert len(h) == 51 and h[0]["cpu"] == 0.0 and h[-1]["cpu"] == 99.0
    mk = s2.marker_list()
    assert mk[0]["key"] == "job:1" and mk[0]["t1"] is not None              # open marker closed at the last sample


def test_monitor_ring_file_is_compacted(tmp_path, monkeypatch):
    f = tmp_path / "h.jsonl"
    monkeypatch.setattr(sysmon, "COMPACT_LINES", 100)
    s = sysmon.Sampler(persist=f, start=False)
    now = time.time()
    s.hist.extend({"t": round(now - 2000 + i, 1), "cpu": 0.0} for i in range(90))   # mostly older than 30 min
    s.flush(force=True)
    s.hist.extend({"t": round(now - 50 + i * 0.5, 1), "cpu": 1.0} for i in range(40))
    s.flush(force=True)
    lines = f.read_text().splitlines()
    assert len(lines) < 100 and all(json.loads(x)["t"] > now - sysmon.HISTORY_S for x in lines)
