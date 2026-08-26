"""Example: import and use the ``yue_voice`` package from within the yue project.

``yue_voice`` bundles the XTTS v2 engine, the voice profiles and an HTTP
server, and is importable by any Python code. It needs a Python 3.10
interpreter with ``coqui-tts`` installed (Yue's default interpreter is 3.12,
which cannot host coqui), so run this with the configured worker interpreter::

    XTTS_CKPT_DIR=/path/to/checkpoints/xtts_v2 \
        /path/to/tts-training/.venv/bin/python examples/use_yue_voice.py

Set ``XTTS_CKPT_DIR`` (or pass ``checkpoint_dir``) to the extracted XTTS v2
checkpoint — the voice profiles ship inside the package, the model does not.
"""
import os
import sys

import yue_voice as yv


def main() -> int:
    print(f"yue_voice {yv.__version__}  public API: {yv.__all__}")

    # checkpoint comes from XTTS_CKPT_DIR env, else the default path.
    engine = yv.XTTSEngine(checkpoint_dir=os.environ.get("XTTS_CKPT_DIR"))

    voices = engine.voices()
    print(f"{len(voices)} voices bundled: {voices[:5]} ...")

    audio = engine.synthesize(
        "Hello. This is Yue reading with the bundled voice package.",
        voice="af_bella", lang="en",
    )
    print(f"synthesized {len(audio)} samples = {len(audio) / engine.sample_rate:.2f}s")

    wav_bytes = engine.to_bytes(audio)
    print(f"in-memory WAV: {len(wav_bytes)} bytes")

    engine.to_wav(audio, "yue_voice_demo.wav")
    print("wrote yue_voice_demo.wav")
    return 0


if __name__ == "__main__":
    sys.exit(main())
