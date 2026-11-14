# Ovoz — text-to-speech for Uzbek, Russian, English and Arabic

A simple web app that turns text into speech, for voice-overs in promo videos (Instagram, App Store and other social media). Uzbek is the main focus. **Russian, English and Arabic** are supported too. You can pick between online Microsoft voices and several **free local models that run on your own GPU**. Models with a **non-commercial (NC)** licence are clearly marked in the UI.

## Languages

Pick the language at the top of the page. The model list only shows models that speak that language:

| Language | Microsoft (online) voices | Local models |
|---|---|---|
| Oʻzbekcha | Madina, Sardor | all models below |
| Русский | Svetlana, Dmitriy | OmniVoice |
| English | Ava, Andrew, Emma, Brian, Aria, Guy (US); Sonia, Ryan (UK) | OmniVoice |
| العربية | Zariyah, Hamed (Saudi); Fatima, Hamdan (UAE); Salma, Shakir (Egypt) | OmniVoice |

- Russian Cyrillic is sent as-is. Only Uzbek text gets the Cyrillic → Latin conversion.
- Microsoft voices read digits themselves in ru/en/ar, including Russian case endings (`более 50 000` → *пятидесяти тысяч*). For OmniVoice, numbers are spelled out with `num2words`.
- OmniVoice clones the voice in `voices/`. The starter clips are Uzbek, so it speaks other languages with an Uzbek accent. For a native accent, add a reference clip recorded in that language (see *Your own voices*).
- Arabic text is shown right to left. Eastern Arabic digits (`٢٥٪`) are supported.

## Models

| Model | Runs | Licence | Commercial use | Voices |
|---|---|---|---|---|
| Microsoft Neural (`edge-tts`) | Online | Microsoft service (unofficial client) | see note | Madina, Sardor |
| [Navoiy TTS](https://huggingface.co/aisha-org/navoiy-tts) (Aisha, CosyVoice2) | Local GPU | Apache-2.0 | ✅ | clones `voices/*`, **10 emotions** |
| [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) | Local GPU | Apache-2.0 | ✅ | clones `voices/*` |
| [Qwen3-TTS Uzbek](https://huggingface.co/mrmuminov/Qwen3-TTS-12Hz-0.6B-Uzbek) (gated, auto-approved) | Local GPU | Apache-2.0 | ✅ | clones `voices/*` |
| [Sayro TTS](https://huggingface.co/uzlm/sayro-tts-1.7B) (gated, manual approval) | Local GPU | Sayro Terms of Use | ❓ **terms unverified** | built-in speaker, 4 styles |
| [Chatterbox Uzbek](https://huggingface.co/Abduqayum/uzbek-tts-natural-speech-chatterbox) | Local GPU | MIT | ✅ | built-in narrator + clones |
| [Anadil Uzbek](https://huggingface.co/Anadilorg/Anadil_Uzbek_TTS) (VoxCPM2) | Local GPU | MIT + Apache-2.0 | ✅ | clones `voices/*` |
| [F5-TTS Uzbek](https://huggingface.co/mrmuminov/f5-tts-uzbek) | Local GPU | CC BY-NC 4.0 | ❌ **NC** | clones `voices/*` |
| [Meta MMS-TTS](https://huggingface.co/facebook/mms-tts-uzb-script_cyrillic) | Local GPU/CPU | CC BY-NC 4.0 | ❌ **NC** | male + fine-tuned female |

- The UI shows an **NC · notijorat** badge on non-commercial models, a warning under audio they produce, and a "commercial only" filter.
- Text handling for every model: Cyrillic → Latin, apostrophe cleanup (`oʻ gʻ ʼ`). Numbers are spelled out in Uzbek for every model, including Microsoft, which otherwise misreads ordinals and grouped thousands (`2026-yil` → `ikki ming yigirma oltinchi yil`, `15 000`, `25%`, `3,5`). MMS gets Latin → Cyrillic, since it was trained on Cyrillic text.
- Local models load on first use and stay in GPU memory. When memory runs short, the least recently used model is unloaded.

## Run (online model only)

```bash
pip install -r requirements.txt
uvicorn app:app --reload        # open http://localhost:8000
```

On Windows you can just double-click `start.bat`.

## Local GPU models (NVIDIA, tested on RTX 5070 Ti)

Use **Python 3.12**. RTX 50-series cards need PyTorch built for CUDA 12.8:

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt -r requirements-gpu.txt
python scripts/setup_models.py   # clones the Chatterbox toolkit, makes reference voices, downloads ~15 GB of weights
uvicorn app:app
```

### Navoiy TTS (optional, separate environment)

Navoiy runs on Alibaba's CosyVoice, which needs older libraries than the other models, so it gets its own virtualenv in `.models/navoiy-venv`. The app talks to it through a worker process.

```bash
python scripts/setup_navoiy.py   # clones CosyVoice, creates the env, downloads ~4 GB
```

The UI shows an emotion picker (xotirjam, xursand, gʻamgin, jahldor, qoʻrqqan, hayratda, pichirlab, mehribon, charchagan, kinoyali) when Navoiy is selected.

### Qwen3-TTS models (optional, separate environment)

Qwen3-TTS Uzbek and Sayro use `qwen-tts`, which pins transformers 4.57, so they share their own virtualenv in `.models/qwen-venv`. Both repos are gated: accept/request access on their Hugging Face pages, then:

```bash
hf auth login                    # paste a Read token from huggingface.co/settings/tokens
python scripts/setup_qwen.py
```

Sayro appears as unavailable until the UzLM team approves your request. Its terms (sayro.uz/terms) could not be verified, so the UI marks it "? Shartlar noaniq" and the commercial-only filter hides it.

### Compare models

```bash
python scripts/compare.py              # all models, or e.g.: python scripts/compare.py omnivoice f5
```

This renders the same test sentences with every model and voice, then builds a listening page at http://localhost:8000/compare/ with load time, speed and GPU memory for each model.

### Your own voices

The cloning models (OmniVoice, F5, Anadil, Chatterbox) copy the voice of a reference clip in `voices/`:

```
voices/aziza.wav    3–10 s of clean speech, one speaker
voices/aziza.txt    exact transcript of the clip (required by OmniVoice and F5)
voices/aziza.name   label shown in the UI, e.g. "Aziza (ayol)"
```

The starter clips `madina` and `sardor` are generated with the Microsoft voices by `scripts/make_reference_voices.py`. They are fine for testing; for a product, record your own speakers (with their consent).

## API

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/models` | – | models with licence, `commercial`, `languages`, availability and voices (each with its `lang`) |
| GET | `/api/languages` | – | supported content languages (`uz`, `ru`, `en`, `ar`) |
| POST | `/api/tts` | `{text, model, voice, rate, pitch, language}` | `audio/mpeg` (online) or `audio/wav` (local) |
| POST | `/api/normalize` | `{text, model, language}` | the exact text the model will read |

`language` defaults to `uz`.

## Tests

```bash
pytest
```

## Notes

- `edge-tts` uses Microsoft Edge's online "Read aloud" service. It is unofficial and sends your text to Microsoft. For production, use the official Azure Speech service, which offers the same voices.
- Chatterbox output carries Resemble AI's inaudible Perth watermark. This is the base model's built-in responsible-AI feature.
