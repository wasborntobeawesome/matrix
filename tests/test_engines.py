from engines.base import split_text


def test_split_text_respects_limit():
    text = "Birinchi gap. Ikkinchi gap juda uzun, vergul bilan, yana davom etadi! Uchinchi?"
    chunks = split_text(text, 30)
    assert all(len(c) <= 30 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_split_text_keeps_short_text_whole():
    assert split_text("Salom dunyo.", 200) == ["Salom dunyo."]


def test_models_endpoint_marks_nc():
    from fastapi.testclient import TestClient

    from app import app

    models = {m["id"]: m for m in TestClient(app).get("/api/models").json()}
    assert models["edge"]["commercial"] is True
    assert models["f5"]["commercial"] is False
    assert models["mms"]["commercial"] is False
    assert models["omnivoice"]["license"] == "Apache-2.0"


def test_navoiy_listed_as_commercial():
    from fastapi.testclient import TestClient

    from app import app

    models = {m["id"]: m for m in TestClient(app).get("/api/models").json()}
    assert models["navoiy"]["license"] == "Apache-2.0"
    assert models["navoiy"]["commercial"] is True
