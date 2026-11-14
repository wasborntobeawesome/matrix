import pytest
from fastapi.testclient import TestClient

from app import app
from engines.base import split_text
from languages import prepare, spell_numbers

client = TestClient(app)


def test_russian_cyrillic_is_not_transliterated():
    text, converted = prepare("Привет,  мир!", "ru", spell=False)
    assert text == "Привет, мир!"
    assert converted is False


def test_uzbek_cyrillic_still_transliterated():
    text, converted = prepare("Салом, дунё!", "uz", spell=False)
    assert text == "Salom, dunyo!"
    assert converted is True


@pytest.mark.parametrize("lang, src, out", [
    ("en", "Join 50,000 users", "Join fifty thousand users"),
    ("en", "the 1st app", "the first app"),
    ("en", "up 3.5%", "up three point five percent"),
    ("ru", "Более 50 000 пользователей", "Более пятьдесят тысяч пользователей"),
    ("ar", "أكثر من 50,000 مستخدم", "أكثر من خمسون ألف مستخدم"),
    ("ar", "٢٥٪", "خمسة و عشرون بالمئة"),
])
def test_spell_numbers(lang, src, out):
    assert spell_numbers(src, lang) == out


def test_split_text_handles_arabic_punctuation():
    chunks = split_text("مرحبا بكم؟ هذا تطبيق جديد. حمّله الآن!", 20)
    assert len(chunks) == 3


def test_models_report_languages():
    models = {m["id"]: m for m in client.get("/api/models").json()}
    assert set(models["edge"]["languages"]) == {"uz", "ru", "en", "ar"}
    assert set(models["omnivoice"]["languages"]) == {"uz", "ru", "en", "ar"}
    assert models["f5"]["languages"] == ["uz"]
    edge_langs = {v["lang"] for v in models["edge"]["voices"]}
    assert edge_langs == {"uz", "ru", "en", "ar"}


def test_edge_reads_digits_itself_outside_uzbek():
    res = client.post("/api/normalize", json={"text": "Скачайте 2 раза", "model": "edge", "language": "ru"})
    assert res.json()["text"] == "Скачайте 2 раза"
    res = client.post("/api/normalize", json={"text": "2 marta", "model": "edge", "language": "uz"})
    assert res.json()["text"] == "ikki marta"


def test_tts_rejects_language_the_model_cannot_speak():
    res = client.post("/api/tts", json={"text": "Hello", "model": "f5", "voice": "madina", "language": "en"})
    assert res.status_code in (400, 503)  # 503 when F5 isn't installed on this machine


def test_tts_rejects_voice_in_wrong_language():
    res = client.post("/api/tts", json={"text": "Hello", "model": "edge", "voice": "ru-RU-DmitryNeural", "language": "en"})
    assert res.status_code == 400


def test_tts_rejects_unknown_language():
    res = client.post("/api/tts", json={"text": "Hola", "model": "edge", "voice": "uz-UZ-MadinaNeural", "language": "es"})
    assert res.status_code == 422
