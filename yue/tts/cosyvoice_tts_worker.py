"""CosyVoice2 worker: a JSON-lines subprocess under the cosyvoice venv.

Runs CosyVoice2 (zero-shot, reference-voice cloning) and exposes it to the yue
reader over stdin/stdout. The model requires a reference prompt audio + its
transcript for every synthesis; we pick a per-language prompt voice.

Protocol:
  in:  {"text": str, "lang": "zh"|"en", "out": str}
  out: {"ok": true, "path": str}  |  {"error": str}

stdout carries JSON responses; the model's own logging/tqdm is routed to stderr
so it can never corrupt the protocol. On exit we os._exit(0) to skip torch/MPS
teardown, which otherwise aborts the interpreter.
"""

import json
import os
import sys
import time

_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr  # keep model logs off the protocol channel


def emit(obj):
    _REAL_STDOUT.write(json.dumps(obj) + "\n")
    _REAL_STDOUT.flush()


def _load_prompt(prompt_dir, lang):
    key = "zh" if str(lang).lower().startswith("zh") else "en"
    wav = os.path.join(prompt_dir, f"{key}.wav")
    txt_path = os.path.join(prompt_dir, f"{key}.txt")
    with open(txt_path, encoding="utf-8") as f:
        text = f.read().strip()
    return wav, text


def main():
    repo_dir = os.environ.get("YUE_COSYVOICE_REPO")
    model_dir = os.environ.get("YUE_COSYVOICE_MODEL_DIR")
    prompt_dir = os.environ.get("YUE_COSYVOICE_PROMPT_DIR")
    if repo_dir:
        sys.path.insert(0, repo_dir)

    import torch
    import soundfile as sf

    device = torch.device("mps") if torch.backends.mps.is_available() else torch.device("cpu")

    try:
        from cosyvoice.cli.cosyvoice import CosyVoice2

        cv = CosyVoice2(model_dir, load_jit=False, load_trt=False, fp16=False)
        # CosyVoice2 defaults to cpu on non-CUDA; move it to MPS where possible.
        cv.model.device = device
        cv.model.llm = cv.model.llm.to(device)
        cv.model.flow = cv.model.flow.to(device)
        cv.model.hift = cv.model.hift.to(device)
    except Exception as e:  # noqa: BLE001
        emit({"error": f"model load failed: {e}"})
        os._exit(1)

    emit({"ready": True, "device": str(device), "sample_rate": cv.sample_rate})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:  # noqa: BLE001
            emit({"error": f"bad request: {e}"})
            continue
        text = req.get("text", "")
        out = req.get("out") or "/tmp/cosyvoice_out.wav"
        if not text:
            emit({"error": "empty text"})
            continue
        t0 = time.time()
        try:
            prompt_wav, prompt_text = _load_prompt(prompt_dir, req.get("lang", "zh"))
            chunks = [
                o["tts_speech"]
                for o in cv.inference_zero_shot(text, prompt_text, prompt_wav, speed=1.0)
            ]
            wav = torch.cat(chunks, dim=1)
            sf.write(out, wav.squeeze(0).detach().cpu().numpy(), cv.sample_rate)
            emit({"ok": True, "path": out, "elapsed": round(time.time() - t0, 2)})
        except Exception as e:  # noqa: BLE001
            import traceback
            emit({"error": f"{e}\n{traceback.format_exc()}"})

    os._exit(0)


if __name__ == "__main__":
    main()
