"""Qwen3-TTS worker (runs inside .models/qwen-venv; qwen-tts pins transformers 4.57).

    python qwen_worker.py <hf-repo>

Requests (one JSON per line on stdin):
    {"mode": "clone",  "text": "...", "ref": "voice.wav", "out": "x.wav"}          # x-vector voice cloning
    {"mode": "custom", "text": "...", "speaker": "sayro", "instruct": "Happy", "out": "x.wav"}
Replies: {"ok": true, "seconds": 3.1} / {"ok": false, "error": "..."}; first line {"ready": true, ...}.
"""
import json
import sys
import traceback
from pathlib import Path

PROTO = sys.stdout
sys.stdout = sys.stderr  # keep stdout for the protocol


def reply(**msg):
    PROTO.write(json.dumps(msg, ensure_ascii=False) + "\n")
    PROTO.flush()


def main():
    repo = sys.argv[1]
    import soundfile as sf
    import torch
    from huggingface_hub import snapshot_download
    from qwen_tts import Qwen3TTSModel

    path = Path(snapshot_download(repo))
    clean = None
    if (path / "uzbek_normalizer.py").exists():  # sayro ships its own text cleaner
        sys.path.insert(0, str(path))
        from uzbek_normalizer import clean_uzbek_text as clean

    model = Qwen3TTSModel.from_pretrained(str(path), device_map="cuda:0", dtype=torch.bfloat16)
    cfg = json.loads((path / "config.json").read_text(encoding="utf-8"))
    speakers = list((cfg.get("talker_config", {}).get("spk_id") or {}).keys())
    reply(ready=True, speakers=speakers)

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            text = clean(req["text"]) if clean else req["text"]
            torch.manual_seed(int(req.get("seed", 42)))
            with torch.inference_mode():
                if req["mode"] == "clone":
                    wavs, sr = model.generate_voice_clone(
                        text=text, language="Auto", ref_audio=req["ref"],
                        x_vector_only_mode=True, non_streaming_mode=True,
                    )
                else:
                    wavs, sr = model.generate_custom_voice(
                        text=[text], speaker=[req.get("speaker") if req.get("speaker") in speakers else speakers[0]],
                        instruct=[req.get("instruct", "")],
                    )
            sf.write(req["out"], wavs[0], sr)
            reply(ok=True, seconds=len(wavs[0]) / sr)
        except Exception as exc:
            traceback.print_exc()
            reply(ok=False, error=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        reply(ready=False, error=f"{type(exc).__name__}: {exc}")
