"""Spark-TTS worker (JSON-lines over stdin/stdout) for the Yue reader.

Spark-TTS is an efficient LLM-based bilingual (English + Chinese) TTS. Like the
other reader workers, this is a standalone subprocess driven by the reader:

    Request  -> {"text": "...", "gender": "female", "pitch": "moderate",
                 "speed": "moderate", "prompt_path": null, "prompt_text": null}
    Response <- {"path": "/tmp/xxx.wav"}   (16 kHz WAV)
             or {"error": "..."}

With ``prompt_path`` it does zero-shot voice cloning from a reference clip;
otherwise it synthesizes a virtual speaker from the gender/pitch/speed tokens.

Runs under ``config.SPARK_TTS_WORKER_PYTHON`` (a dedicated venv) so the pinned
torch/transformers versions never touch the reader's own venv.
"""
import json
import os
import sys
import tempfile


def main() -> None:
    real_stdout = sys.stdout

    def emit(obj) -> None:
        real_stdout.write(json.dumps(obj) + "\n")
        real_stdout.flush()

    # Keep library/model prints off the protocol pipe for the whole run.
    sys.stdout = sys.stderr

    repo_dir = os.environ.get("YUE_SPARK_TTS_REPO")
    if not repo_dir or not os.path.isdir(repo_dir):
        emit({"error": "YUE_SPARK_TTS_REPO is not set or missing."})
        return
    sys.path.insert(0, repo_dir)

    import torch

    device = torch.device("mps:0") if torch.backends.mps.is_available() else torch.device("cpu")

    model_dir = os.environ.get("YUE_SPARK_TTS_MODEL_DIR")
    try:
        from cli.SparkTTS import SparkTTS
        model = SparkTTS(model_dir, device)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"spark-tts failed to load model: {e}\n")
        sys.stderr.flush()
        emit({"error": str(e)})
        return

    import numpy as np
    import soundfile as sf

    emit({"ready": True})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            text = req.get("text", "")
            prompt_path = req.get("prompt_path")
            with torch.no_grad():
                if prompt_path:
                    wav = model.inference(
                        text,
                        prompt_path,
                        prompt_text=req.get("prompt_text"),
                    )
                else:
                    wav = model.inference(
                        text,
                        gender=req.get("gender", "female"),
                        pitch=req.get("pitch", "moderate"),
                        speed=req.get("speed", "moderate"),
                    )
            audio = np.asarray(wav).squeeze()
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            sf.write(path, audio, model.sample_rate)
            emit({"path": path})
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"spark-tts request failed: {e}\n")
            sys.stderr.flush()
            emit({"error": str(e)})


if __name__ == "__main__":
    main()
