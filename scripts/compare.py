"""Synthesize the same Uzbek test sentences with every model and build a
side-by-side listening page at static/compare/index.html (served at /compare/).

    python scripts/compare.py                 # all available models
    python scripts/compare.py omnivoice f5    # only these
    python scripts/compare.py --asr           # also score intelligibility with an Uzbek ASR model
    python scripts/compare.py --asr-only      # re-score existing clips
"""
import asyncio
import html
import io
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import re  # noqa: E402

import soundfile as sf  # noqa: E402

import engines  # noqa: E402
from app import prepare_text  # noqa: E402
from uzbek import expand_numbers, normalize  # noqa: E402

ASR_MODEL = "navai-uz/whisper-medium-uzbek"  # Apache-2.0, FLEURS WER 14%

OUT = ROOT / "static" / "compare"
AUDIO = OUT / "audio"
RESULTS = OUT / "results.json"

SENTENCES = [
    ("Salomlashish", "Assalomu alaykum! Oʻzbekiston — goʻzal va qadimiy mamlakat."),
    ("Raqamlar", "Bugun 2026-yil 14-noyabr. Mahsulot narxi 15 000 soʻm, chegirma 25%."),
    ("Yangilik", "Samarqand shahrida xalqaro ilmiy anjuman boʻlib oʻtdi. Unda oʻttizdan ortiq davlatdan kelgan olimlar sunʼiy intellekt va taʼlim masalalarini muhokama qildi."),
    ("Kirill", "Тошкент — Ўзбекистоннинг пойтахти. Шаҳар аҳолиси уч миллиондан ортиқ."),
    ("Savol", "Siz ertaga qayerga borasiz? Men bozorga borib, gʻoʻza va qovun olmoqchiman."),
]
# Models with emotion control also render this sentence in several styles.
EMOTION_SENTENCE = "Men bugun imtihondan oʻtdim! Bunga hali ham ishonolmayapman."
EMOTION_STYLES = ["xotirjam", "xursand", "g'amgin", "jahldor", "pichirlab", "mehribon"]
# Which voices to test per model (keeps the run short).
VOICES = {
    "edge": ["uz-UZ-MadinaNeural", "uz-UZ-SardorNeural"],
    "navoiy": ["madina", "sardor"],
    "qwen3uz": ["madina", "sardor"],
    "sayro": ["sayro"],
    "omnivoice": ["madina", "sardor"],
    "chatterbox": ["diktor", "madina"],
    "anadil": ["madina", "sardor"],
    "f5": ["madina", "sardor"],
    "mms": ["mms-male", "mms-female"],
}


def audio_seconds(data: bytes) -> float:
    info = sf.info(io.BytesIO(data))
    return info.frames / info.samplerate


def gpu_used_gb() -> float:
    """Device-wide GPU memory in use (also counts worker processes)."""
    import subprocess

    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.split()[0]) / 1024


def run_engine(engine) -> dict:
    import torch

    entry = {"id": engine.id, "name": engine.name, "license": engine.license,
             "commercial": engine.commercial, "terms_unclear": engine.terms_unclear, "kind": engine.kind, "url": engine.url, "clips": [], "error": None}
    voice_names = {v.id: v.name for v in engine.voices()}
    voices = [v for v in VOICES.get(engine.id, []) if v in voice_names]
    entry["voices"] = [{"id": v, "name": voice_names[v]} for v in voices]
    try:
        if engine.kind == "local":
            torch.cuda.reset_peak_memory_stats()
            used0 = gpu_used_gb()
            t = time.perf_counter()
            engine.load()
            entry["load_seconds"] = round(time.perf_counter() - t, 1)
        for vid in voices:
            for i, (_, sentence) in enumerate(SENTENCES):
                text, _ = prepare_text(sentence, engine)
                t = time.perf_counter()
                if engine.kind == "online":
                    data, media = asyncio.run(engine.synthesize_async(text, vid))
                else:
                    data, media = engine.synthesize(text, vid)
                took = time.perf_counter() - t
                ext = "mp3" if media == "audio/mpeg" else "wav"
                name = f"{engine.id}_{vid}_{i}.{ext}"
                (AUDIO / name).write_bytes(data)
                dur = audio_seconds(data)
                entry["clips"].append({"voice": vid, "sentence": i, "file": f"audio/{name}",
                                       "seconds": round(took, 2), "duration": round(dur, 2)})
                print(f"  {engine.id:<10} {vid:<20} #{i}  {dur:5.1f}s audio in {took:5.1f}s")
        styles = {st["id"] for st in engine.styles()}
        for style in [st for st in EMOTION_STYLES if st in styles] if voices else []:
            text, _ = prepare_text(EMOTION_SENTENCE, engine)
            t = time.perf_counter()
            data, _ = engine.synthesize(text, voices[0], style=style)
            took = time.perf_counter() - t
            name = f"{engine.id}_emo_{style.replace(chr(39), '')}.wav"
            (AUDIO / name).write_bytes(data)
            entry["clips"].append({"voice": voices[0], "sentence": -1, "style": style, "text": EMOTION_SENTENCE,
                                   "file": f"audio/{name}", "seconds": round(took, 2), "duration": round(audio_seconds(data), 2)})
            print(f"  {engine.id:<10} emotion {style:<12} {took:5.1f}s")
        if engine.kind == "local":
            if getattr(engine, "out_of_process", False):  # model lives in a worker process
                entry["peak_vram_gb"] = round(gpu_used_gb() - used0, 1)
            else:
                entry["peak_vram_gb"] = round(torch.cuda.max_memory_allocated() / 2**30, 1)
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if engine.kind == "local":
            engine.unload()
    # Warm speed = synthesis time / audio length, ignoring the first clip (warm-up).
    warm = [c for c in entry["clips"] if c["sentence"] >= 0][1:] or entry["clips"]
    if warm:
        entry["rtf"] = round(sum(c["seconds"] for c in warm) / sum(c["duration"] for c in warm), 2)
    return entry


def _canon(text: str) -> str:
    """Comparable form: Latin, numbers as words, lowercase, no punctuation/apostrophes."""
    text = expand_numbers(normalize(text)[0]).lower()
    text = re.sub(r"[^\w\s]|_", "", text)
    return " ".join(text.split())


def _cer(ref: str, hyp: str) -> float:
    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, 1):
        cur = [i]
        for j, hc in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / max(1, len(ref))


def score_with_asr(results: list[dict]):
    """Transcribe every clip with an Uzbek Whisper model and store the character error rate."""
    import librosa
    import torch
    from transformers import pipeline

    print(f"== ASR check with {ASR_MODEL}")
    asr = pipeline("automatic-speech-recognition", model=ASR_MODEL, device="cuda", dtype=torch.float16)
    for e in results:
        for c in e["clips"]:
            audio, _ = librosa.load(OUT / c["file"], sr=16000, mono=True)
            hyp = asr(audio, generate_kwargs={"language": "uzbek", "task": "transcribe"})["text"].strip()
            c["asr"] = hyp
            ref = c.get("text") or SENTENCES[c["sentence"]][1]
            c["cer"] = round(_cer(_canon(ref), _canon(hyp)) * 100, 1)
        main = [c for c in e["clips"] if c["sentence"] >= 0]  # score on the shared sentences only
        if main:
            e["cer"] = round(sum(c["cer"] for c in main) / len(main), 1)
            print(f"  {e['id']:<10} CER {e['cer']}%")
    del asr
    torch.cuda.empty_cache()


def render(results: list[dict]):
    def badge(e):
        b = f'<span class="badge {"online" if e["kind"] == "online" else "local"}">{"Onlayn" if e["kind"] == "online" else "Lokal GPU"}</span>'
        if e.get("terms_unclear"):
            b += '<span class="badge nc" title="Shartlar tasdiqlanmagan">? Shartlar noaniq</span>'
        elif not e["commercial"]:
            b += '<span class="badge nc" title="Notijorat litsenziya">NC · notijorat</span>'
        return b + f'<span class="badge lic">{html.escape(e["license"])}</span>'

    def unit(e, key, suffix):
        return f"{e[key]}{suffix}" if key in e else "–"

    rows = []
    for e in results:
        rtf = e.get("rtf")
        speed = f"{1 / rtf:.1f}× real vaqt" if rtf else "–"
        rows.append(
            f'<tr><td><a href="{e["url"]}" target="_blank" rel="noopener">{html.escape(e["name"])}</a></td>'
            f'<td><span class="badges">{badge(e)}</span></td>'
            f'<td>{unit(e, "cer", "%")}</td><td>{unit(e, "load_seconds", " s")}</td>'
            f'<td>{speed}</td><td>{unit(e, "peak_vram_gb", " GB")}</td>'
            f'<td class="err">{html.escape(e["error"] or "")}</td></tr>'
        )
    sections = []
    for i, (title, sentence) in enumerate(SENTENCES):
        cards = []
        for e in results:
            for v in e.get("voices", []):
                clip = next((c for c in e["clips"] if c["voice"] == v["id"] and c["sentence"] == i), None)
                if not clip:
                    continue
                cards.append(
                    f'<div class="clip{" is-nc" if not e["commercial"] else ""}"><div class="clip-head"><b>{html.escape(e["name"])}</b>'
                    f'{"<span class=\"badge nc\">NC</span>" if not e["commercial"] else ""}</div>'
                    f'<div class="voice">{html.escape(v["name"])} · {clip["duration"]} s'
                    + (f' · <span class="cer" title="ASR: {html.escape(clip["asr"])}">xato {clip["cer"]}%</span>' if "cer" in clip else "")
                    + "</div>"
                    f'<audio controls preload="none" src="{clip["file"]}"></audio></div>'
                )
        sections.append(
            f'<section><h2>{i + 1}. {title}</h2><p class="sentence">{html.escape(sentence)}</p>'
            f'<div class="grid">{"".join(cards)}</div></section>'
        )
    emo_cards = []
    for e in results:
        for c in (c for c in e["clips"] if c.get("style")):
            emo_cards.append(
                f'<div class="clip"><div class="clip-head"><b>{html.escape(c["style"].replace(chr(39), "ʻ").capitalize())}</b></div>'
                f'<div class="voice">{html.escape(e["name"])} · {c["duration"]} s</div>'
                f'<audio controls preload="none" src="{c["file"]}"></audio></div>'
            )
    if emo_cards:
        sections.append(
            f'<section><h2>{len(SENTENCES) + 1}. Hissiyotlar</h2><p class="sentence">{html.escape(EMOTION_SENTENCE)}</p>'
            f'<div class="grid">{"".join(emo_cards)}</div></section>'
        )
    page = (OUT / "template.html").read_text(encoding="utf-8")
    page = page.replace("{{ROWS}}", "".join(rows)).replace("{{SECTIONS}}", "".join(sections))
    page = page.replace("{{DATE}}", time.strftime("%Y-%m-%d %H:%M"))
    (OUT / "index.html").write_text(page, encoding="utf-8")


def main():
    AUDIO.mkdir(parents=True, exist_ok=True)
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or list(engines.ENGINES)
    previous = {e["id"]: e for e in json.loads(RESULTS.read_text(encoding="utf-8"))} if RESULTS.exists() else {}

    def save():
        results = [previous[k] for k in engines.ENGINES if k in previous]
        RESULTS.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        render(results)
        return results

    if "--asr-only" not in flags:
        for eid in wanted:
            engine = engines.get(eid)
            if reason := engine.missing_dependency():
                print(f"skip {eid}: {reason}")
                continue
            print(f"== {engine.name}")
            previous[eid] = run_engine(engine)
            save()
    if flags & {"--asr", "--asr-only"}:
        score_with_asr([previous[k] for k in wanted if k in previous])
        save()
    print(f"\nOpen http://localhost:8000/compare/  ({OUT / 'index.html'})")


if __name__ == "__main__":
    main()
