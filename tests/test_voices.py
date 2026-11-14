import base64
import io

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

import engines
from app import app
from engines import base

# Writes are only accepted for a loopback Host, as when the app runs on localhost:8000.
client = TestClient(app, base_url="http://localhost:8000")


def wav_b64(seconds=5.0, sr=24000, amp=0.3):
    t = np.arange(int(seconds * sr)) / sr
    buf = io.BytesIO()
    sf.write(buf, (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr, format="WAV")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def voices_dir(tmp_path, monkeypatch):
    sf.write(tmp_path / "madina.wav", np.zeros(24000, np.float32), 24000)
    (tmp_path / "madina.txt").write_text("Salom", encoding="utf-8")
    monkeypatch.setattr(base, "VOICES_DIR", tmp_path)
    return tmp_path


def upload(**overrides):
    body = {"name": "Anna (ayol)", "language": "ru", "transcript": "Привет! Это тест.", "consent": True, "audio": wav_b64()}
    return client.post("/api/voices", json={**body, **overrides})


def test_upload_saves_native_voice_in_language_folder(voices_dir):
    res = upload()
    assert res.status_code == 200, res.text
    assert res.json()["id"] == "ru/anna-ayol"
    assert (voices_dir / "ru" / "anna-ayol.wav").exists()
    assert (voices_dir / "ru" / "anna-ayol.txt").read_text(encoding="utf-8") == "Привет! Это тест."
    assert (voices_dir / "ru" / "anna-ayol.name").read_text(encoding="utf-8") == "Anna (ayol)"
    assert upload().json()["id"] == "ru/anna-ayol-2"  # same name -> unique id


def test_uploaded_voice_only_reaches_models_that_speak_the_language(voices_dir):
    upload()
    by_id = {e.id: {v.id: v for v in e.voices()} for e in engines.ENGINES.values()}
    assert by_id["omnivoice"]["ru/anna-ayol"].lang == "ru"
    assert "ru/anna-ayol" in by_id["chatterbox-ml"]
    assert "ru/anna-ayol" not in by_id["f5"]       # Uzbek-only model
    assert "madina" in by_id["f5"]                # root clips stay Uzbek
    assert "madina" not in by_id["chatterbox-ml"]  # ...and don't reach models without Uzbek


@pytest.mark.parametrize("overrides, message", [
    ({"consent": False}, "roziligi"),
    ({"audio": wav_b64(seconds=1)}, "soniya"),
    ({"audio": wav_b64(seconds=40)}, "soniya"),
    ({"audio": wav_b64(amp=0.001)}, "jim"),
    ({"audio": base64.b64encode(b"not audio").decode()}, "o'qib"),
])
def test_upload_rejects_bad_clips(voices_dir, overrides, message):
    res = upload(**overrides)
    assert res.status_code == 400
    assert message in res.json()["detail"]


def test_upload_rejects_unknown_language(voices_dir):
    assert upload(language="es").status_code == 422


def test_delete_voice(voices_dir):
    upload()
    assert client.delete("/api/voices/ru/anna-ayol").status_code == 200
    assert not (voices_dir / "ru" / "anna-ayol.wav").exists()
    assert client.delete("/api/voices/ru/anna-ayol").status_code == 400


def test_delete_refuses_starter_voices_and_path_tricks(voices_dir):
    assert client.delete("/api/voices/uz/..").status_code != 200
    assert client.delete("/api/voices/uz/%2E%2E").status_code != 200
    assert client.delete("/api/voices/xx/madina").status_code == 400
    assert (voices_dir / "madina.wav").exists()


def test_cloned_voice_may_speak_another_language_with_accent(voices_dir):
    # A cloned Uzbek clip is accepted for Russian (accented); the request then fails only
    # at synthesis/availability, never with the "wrong language voice" 400.
    res = client.post("/api/tts", json={"text": "Привет", "model": "omnivoice", "voice": "madina", "language": "ru"})
    assert "gapirmaydi" not in res.text


def test_chatterbox_multilingual_listed():
    m = {x["id"]: x for x in client.get("/api/models").json()}["chatterbox-ml"]
    assert m["license"] == "MIT" and m["commercial"] is True
    assert set(m["languages"]) == {"ru", "en", "ar"}
    assert [s["id"] for s in m["styles"]] == ["oddiy", "jonli", "hayajonli"] or not m["available"]


# ---- review fixes ---------------------------------------------------------------

@pytest.mark.parametrize("origin, host, status", [
    ("https://evil.example", "localhost:8000", 403),        # another website in the browser
    ("null", "localhost:8000", 403),                        # sandboxed iframe / page opened from disk
    ("http://localhost:8000", "localhost:8000", 200),       # the app's own page
    ("http://127.0.0.1:8000", "127.0.0.1:8000", 200),
    ("http://evil.example:8000", "evil.example:8000", 403), # DNS rebinding: same origin, foreign host
])
def test_voice_writes_only_from_the_apps_own_page(voices_dir, origin, host, status):
    res = client.post(
        "/api/voices", headers={"Origin": origin, "Host": host},
        json={"name": "Anna", "language": "ru", "transcript": "Привет", "consent": True, "audio": wav_b64()},
    )
    assert res.status_code == status, res.text
    res = client.delete("/api/voices/ru/anna", headers={"Origin": origin, "Host": host})
    assert res.status_code == status, res.text


@pytest.mark.parametrize("field", ["name", "transcript"])
def test_upload_rejects_blank_name_or_transcript(voices_dir, field):
    res = upload(**{field: "   "})
    assert res.status_code == 400
    assert not (voices_dir / "ru").exists() or not list((voices_dir / "ru").glob("*.wav"))


def test_concurrent_uploads_with_same_name_get_distinct_voices(voices_dir):
    from concurrent.futures import ThreadPoolExecutor

    import voice_library

    wav = base64.b64decode(wav_b64())
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(lambda i: voice_library.save_voice("Anna", "ru", f"matn {i}", wav), range(8)))
    assert len(set(ids)) == 8
    for vid in ids:  # each voice kept its own transcript
        txt = (voices_dir / f"{vid}.txt").read_text(encoding="utf-8")
        assert txt.startswith("matn ")
    assert len({(voices_dir / f"{vid}.txt").read_text(encoding="utf-8") for vid in ids}) == 8


def test_delete_requires_exact_id():
    import voice_library

    with pytest.raises(voice_library.VoiceError):
        voice_library.delete_voice("ru/anna\n")


def test_cache_key_needs_no_file(tmp_path):
    v = base.Voice("ru/gone", "Gone", tmp_path / "gone.wav", "x", "ru", version=7)
    assert v.cache_key == "ru/gone:7"  # no stat(): works after the clip is deleted


def test_voice_deleted_mid_request_returns_clear_error(voices_dir, monkeypatch):
    engine = engines.get("omnivoice")
    monkeypatch.setattr(engine, "missing_dependency", lambda: None)

    def gone(*a, **k):
        raise base.VoiceNotFound("deleted")

    monkeypatch.setattr(engine, "synthesize", gone)
    res = client.post("/api/tts", json={"text": "Salom", "model": "omnivoice", "voice": "madina", "language": "uz"})
    assert res.status_code == 410
    assert "o" in res.json()["detail"] and "WinError" not in res.json()["detail"]


def test_chatterbox_builtin_speaker_is_native_english_and_usable_elsewhere(monkeypatch):
    engine = engines.get("chatterbox-ml")
    builtin = next(v for v in engine.voices() if v.id == "default")
    assert builtin.lang == "en" and builtin.cross_lingual and not builtin.clone
    monkeypatch.setattr(engine, "missing_dependency", lambda: None)
    monkeypatch.setattr(engine, "synthesize", lambda *a, **k: (b"RIFF", "audio/wav"))
    res = client.post("/api/tts", json={"text": "Привет", "model": "chatterbox-ml", "voice": "default", "language": "ru"})
    assert res.status_code == 200  # accented, but allowed


def test_languages_endpoint_has_uzbek_labels():
    langs = {l["id"]: l for l in client.get("/api/languages").json()}
    assert langs["ru"]["label"] == "Rus" and langs["ar"]["dir"] == "rtl"
