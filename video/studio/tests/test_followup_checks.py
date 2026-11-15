"""Follow-up checks from the Ovoz QA run (UX 2–4, 6): video length vs target, a language > 10 % longer, dead air
after a recording, landscape browser screenshots in a 9:16 project, and per-project dismissals."""
import brief as B
import followup as F


def _brief(**over):
    raw = {"product": "Ovoz", "language": "uz", "languages": ["uz", "en"], "format": "9:16",
           "brand": {"logo": "logo.png"},
           "scenes": [{"id": "hook", "layout": "hero", "headline": "Ovoz", "say": {"uz": "Salom", "en": "Hi"}},
                      {"id": "langs", "layout": "chips", "headline": "4 til", "items": ["a", "b"], "say": {"uz": "x", "en": "y"}},
                      {"id": "play", "layout": "screen", "device": "phone", "asset": "rec.webm", "say": {"uz": "z", "en": "w"}},
                      {"id": "web", "layout": "screen", "device": "browser", "asset": "web.png", "focus": {"x": 0.1, "y": 0.1, "w": 0.46, "h": 0.2},
                       "say": {"uz": "v", "en": "u"}},
                      {"id": "cta", "layout": "cta", "headline": "Ovoz", "say": {"uz": "t", "en": "s"}}]}
    raw.update(over)
    return B.normalize(raw)[0]


ASSETS = {"rec.webm": {"type": "video", "w": 1170, "h": 2532, "duration": 6.7},
          "web.png": {"type": "image", "w": 1440, "h": 900}, "logo.png": {"type": "image", "w": 512, "h": 512}}


def _timing(uz_scale=1.0):
    def lang(k):
        sc = {"hook": {"length": 6.0 * k, "speech": 5.6 * k, "voiced": True},
              "langs": {"length": 5.0 * k, "speech": 4.6 * k, "voiced": True},
              "play": {"length": 7.2, "speech": 2.9 * k, "voiced": True},
              "web": {"length": 5.5 * k, "speech": 5.1 * k, "voiced": True},
              "cta": {"length": 4.0 * k, "speech": 3.0 * k, "voiced": True}}
        return {"duration": sum(x["length"] for x in sc.values()), "speech": sum(x["speech"] for x in sc.values()), "scenes": sc}
    return {"uz": lang(uz_scale), "en": lang(1.0)}


def test_length_language_spread_dead_air_and_small_browser():
    r = F.build(_brief(), ASSETS, timing=_timing(uz_scale=1.18))
    by = {i["type"]: i for i in r["issues"]}
    assert "lang_longer" in by and by["lang_longer"]["lang"] == "uz" and "18 %" in by["lang_longer"]["text"]
    assert "dead_air" in by and by["dead_air"]["scene"] == "play" and "rec.webm" in by["dead_air"]["text"]
    assert "small_browser" in by and by["small_browser"]["scene"] == "web" and "0.46" in by["small_browser"]["text"]
    assert "length" not in by                              # ~30 s: inside 15–45 s
    r = F.build(_brief(target_seconds=20), ASSETS, timing=_timing(uz_scale=1.18))
    ln = [i for i in r["issues"] if i["type"] == "length"]
    assert ln and ln[0]["lang"] == "uz" and "20 s" in ln[0]["text"] and "“hook”" in ln[0]["text"]
    assert "video" in r["message"] and not r["ok"]
    uz = F.build(_brief(), ASSETS, lang="uz", timing=_timing(uz_scale=1.18))
    assert any("ovozsiz boʻshliq" in i["text"] for i in uz["issues"])


def test_no_timing_checks_when_fine():
    b = _brief()
    b["scenes"][3]["focus"] = {"x": 0.3, "y": 0.1, "w": 0.21, "h": 0.15}      # the QA's tighter focus
    tm = _timing()
    tm["uz"]["scenes"]["play"]["speech"] = tm["en"]["scenes"]["play"]["speech"] = 6.4
    r = F.build(b, ASSETS, timing=tm)
    assert not [i for i in r["issues"] if i["type"] in ("length", "lang_longer", "dead_air", "small_browser")]


def test_dismissed_points_are_left_out():
    r = F.build(_brief(), ASSETS, timing=_timing())
    sug = [i for i in r["issues"] if i["type"] == "suggestion" and i.get("scene") == "langs"]
    assert sug, "the chips scene gets the 'could be a screen scene' suggestion"
    key = sug[0]["key"]
    r2 = F.build(_brief(), ASSETS, timing=_timing(), dismissed=[key])
    assert key not in [i["key"] for i in r2["issues"]] and r2["dismissed"] == 1 and "“langs”" not in r2["message"]


def test_dismiss_api(client, example_project):
    pid = example_project["id"]
    r = client.get(f"/api/projects/{pid}/followup").json()
    assert all(i.get("key") for i in r["issues"])
    key = r["issues"][-1]["key"]
    assert client.post(f"/api/projects/{pid}/followup/dismiss", json={"key": key}).json()["dismissed"] == [key]
    r2 = client.get(f"/api/projects/{pid}/followup").json()
    assert key not in [i["key"] for i in r2["issues"]] and r2["dismissed"] == 1
    assert client.post(f"/api/projects/{pid}/followup/dismiss", json={"reset": True}).json()["dismissed"] == []
    assert client.post(f"/api/projects/{pid}/followup/dismiss", json={}).status_code == 422
    assert client.post(f"/api/projects/{pid}/followup/dismiss", json={"key": key},
                       headers={"Origin": "https://evil.example"}).status_code == 403
