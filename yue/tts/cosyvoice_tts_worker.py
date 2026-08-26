"""CosyVoice2 worker: a JSON-lines subprocess under the cosyvoice venv.

Runs CosyVoice2 (zero-shot, reference-voice cloning) and exposes it to the yue
reader over stdin/stdout. The model requires a reference prompt audio + its
transcript for every synthesis; we pick a per-language prompt voice.

Protocol:
  in:  {"text": str, "voice": str, "out": str}   # voice = prompt id (e.g. "zh"|"en"|"zh_amy")
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


def _load_prompt(prompt_dir, voice):
    """Return (wav_path, text) for a prompt id, falling back along lang → zh.

    A prompt id is the stem of a pair `<id>.wav` / `<id>.txt` in the prompts
    dir. The part before the first "_" (or the whole id) is the language used
    for the fallback chain.
    """
    cands = [voice] if voice else []
    if voice and "_" in voice:
        cands.append(voice.split("_")[0])
    cands += ["zh"]
    for cand in cands:
        wav = os.path.join(prompt_dir, f"{cand}.wav")
        txt = os.path.join(prompt_dir, f"{cand}.txt")
        if os.path.exists(wav) and os.path.exists(txt):
            with open(txt, encoding="utf-8") as f:
                return wav, f.read().strip()
    # No prompt at all: fall back to a silent/empty text so the model still runs.
    return os.path.join(prompt_dir, "zh.wav"), ""


def main():
    # onnxruntime's Microsoft telemetry logger (Microsoft::Applications::Events)
    # can deadlock/abort on macOS with a recursive_mutex::lock failure, which
    # terminates this whole worker mid-read. Disable it before any onnxruntime
    # session/model is created. Also honor the env var set by the reader.
    os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
    try:
        import onnxruntime as _ort
        _ort.disable_telemetry_events()
    except Exception:  # noqa: BLE001
        pass

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
            prompt_wav, prompt_text = _load_prompt(prompt_dir, req.get("voice", ""))
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
