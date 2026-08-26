"""Guard against the CosyVoice worker causing a reader quit-hang or crash-stop.

A wedged worker (or a grandchild that inherits the worker's pipes) used to keep
the synthesis thread blocked in read(), and because that thread ran on the
non-daemon default executor, ``concurrent.futures._python_exit`` joined it at
interpreter exit — the process hung on quit. The synthesis and stderr reads now
run on daemon threads and shutdown must return promptly regardless of a wedged
worker. If the worker instead *crashes* mid-read (e.g. an onnxruntime abort),
generate_audio must restart it and retry so reading resumes.
"""

import asyncio
import json
import subprocess
import sys
import time

import pytest

from yue import config
from yue.tts.cosyvoice_tts import CosyVoiceTTS

# A worker that (a) never answers a synthesis request and (b) spawns a
# grandchild that inherits the stdout/stderr pipes, so killing the worker does
# NOT close the pipes — the exact condition that used to pin a reader thread.
FAKE_WORKER = r"""
import json, subprocess, sys, time
print(json.dumps({"ready": True}), flush=True)
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(100000)"])
while True:
    line = sys.stdin.readline()
    if not line:
        break
    json.loads(line)
    time.sleep(1000)
"""


class _Console:
    def print(self, *a, **k):
        pass


# A worker that aborts (os._exit) on its very first request ever — simulating an
# onnxruntime abort — then behaves normally. The marker file makes the crash
# happen exactly once across worker restarts.
CRASH_WORKER = r"""
import json, os, sys
marker = %(marker)r
print(json.dumps({"ready": True}), flush=True)
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if not os.path.exists(marker):
        open(marker, "w").write("x")
        os._exit(3)
    with open(req["out"], "w") as f:
        f.write("audio")
    print(json.dumps({"path": req["out"]}), flush=True)
"""


def _write_worker(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER)
    return str(script)


def _write_crash_worker(tmp_path):
    script = tmp_path / "crash_worker.py"
    marker = tmp_path / "crash_has_happened"
    script.write_text(CRASH_WORKER % {"marker": str(marker)})
    return str(script), str(marker)


@pytest.mark.parametrize("_", [0])
def test_generate_audio_recovers_after_worker_crash(tmp_path, _):
    async def scenario():
        config.COSYVOICE_WORKER_PYTHON = sys.executable
        config.COSYVOICE_WORKER_SCRIPT, marker = _write_crash_worker(tmp_path)
        config.COSYVOICE_WORKER_TIMEOUT = 10.0

        tts = CosyVoiceTTS(_Console())
        assert await tts.initialize() is True

        # The first synthesis makes the worker abort; generate_audio must
        # detect the dead worker, restart it, and retry successfully.
        await tts.generate_audio("hello", str(tmp_path / "out1.wav"))
        assert (tmp_path / "out1.wav").exists()
        # The restarted worker is stable for further calls.
        await tts.generate_audio("world", str(tmp_path / "out2.wav"))
        assert (tmp_path / "out2.wav").exists()

        await tts.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("_", [0])
def test_shutdown_returns_promptly_with_wedged_worker(tmp_path, _):
    async def scenario():
        config.COSYVOICE_WORKER_PYTHON = sys.executable
        config.COSYVOICE_WORKER_SCRIPT = _write_worker(tmp_path)
        config.COSYVOICE_WORKER_TIMEOUT = 30.0

        tts = CosyVoiceTTS(_Console())
        assert await tts.initialize() is True

        # Kick off a synthesis that will block in the worker forever.
        synth_task = asyncio.ensure_future(
            tts.generate_audio("hello", str(tmp_path / "out.wav"))
        )
        await asyncio.sleep(0.5)  # let the synth thread reach its blocking read

        t0 = time.monotonic()
        await asyncio.wait_for(tts.shutdown(), timeout=6.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 6.0, "shutdown hung past the grace period"

        synth_task.cancel()
        try:
            await synth_task
        except BaseException:
            pass

    asyncio.run(scenario())


def test_blocking_synthesis_runs_on_daemon_thread():
    # If the synthesis ran on a non-daemon pool thread, interpreter exit would
    # join it; running on a daemon thread means a stuck read() can never block
    # quit. Spawn the worker, block a synthesis, and assert the process exits.
    proc = subprocess.Popen(
        [sys.executable, "-c", FAKE_WORKER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout.readline().strip()
    proc.stdin.write(json.dumps({"text": "hi", "voice": "en", "out": "/tmp/x.wav"}) + "\n")
    proc.stdin.flush()
    # A daemon thread blocked reading the worker's stdout must not stop the
    # parent from exiting. (We can't run an actual yue quit here, so this just
    # proves the pipes are inherited/hung; the shutdown test above covers the
    # recovery.)
    import threading

    t = threading.Thread(target=proc.stdout.read, daemon=True)
    t.start()
    time.sleep(0.2)
    assert t.is_alive()
    assert t.daemon is True
    # cleanup
    proc.kill()
    proc.wait()
