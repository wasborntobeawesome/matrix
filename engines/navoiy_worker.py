"""Navoiy TTS worker. Runs inside .models/navoiy-venv (CosyVoice needs older
dependencies than the main app), loads the model once, then answers JSON requests,
one per line on stdin:

    {"text": "...", "ref": "voice.wav", "instruct": "Speak calmly...", "speed": 1.0, "seed": 1986, "out": "x.wav"}

and replies with one JSON line on stdout: {"ok": true, "seconds": 3.2} or {"ok": false, "error": "..."}.
"""
import json
import random
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COSYVOICE = ROOT / ".models" / "CosyVoice"

# Keep stdout for the protocol only; library logs go to stderr.
PROTO = sys.stdout
sys.stdout = sys.stderr


def reply(**msg):
    PROTO.write(json.dumps(msg, ensure_ascii=False) + "\n")
    PROTO.flush()


def unwrap_state_dict(value):
    if not isinstance(value, dict):
        return value
    for key in ("state_dict", "model", "llm"):
        if isinstance(value.get(key), dict):
            return unwrap_state_dict(value[key])
    return value


def main():
    sys.path[:0] = [str(COSYVOICE), str(COSYVOICE / "third_party" / "Matcha-TTS")]
    import torch
    import torchaudio
    from huggingface_hub import snapshot_download

    navoiy = Path(snapshot_download("aisha-org/navoiy-tts"))
    base = Path(snapshot_download("FunAudioLLM/CosyVoice2-0.5B"))
    sys.path.insert(0, str(navoiy))
    from cosyvoice.cli.cosyvoice import CosyVoice2
    from uztts.normalize import normalize

    model = CosyVoice2(str(base), load_jit=False, load_trt=False, fp16=True)
    state = unwrap_state_dict(torch.load(navoiy / "emotion_600h_joint.pt", map_location="cpu", weights_only=True))
    result = model.model.llm.load_state_dict(state, strict=False)
    model.model.llm.eval()
    reply(ready=True, missing=len(result.missing_keys), unexpected=len(result.unexpected_keys))

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            seed = int(req.get("seed", 1986))
            random.seed(seed)
            torch.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)
            text = normalize(req["text"], mode="infer")
            chunks = []
            with torch.inference_mode():
                for out in model.inference_instruct2(
                    text, req["instruct"].strip() + "<|endofprompt|>", req["ref"],
                    stream=False, speed=float(req.get("speed", 1.0)),
                ):
                    chunks.append(out["tts_speech"].detach().cpu())
            if not chunks:
                raise RuntimeError("model returned no audio")
            audio = torch.cat(chunks, dim=1)
            torchaudio.save(req["out"], audio, model.sample_rate)
            reply(ok=True, seconds=audio.shape[-1] / model.sample_rate, text=text)
        except Exception as exc:
            traceback.print_exc()
            reply(ok=False, error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        reply(ready=False, error=f"{type(exc).__name__}: {exc}")
