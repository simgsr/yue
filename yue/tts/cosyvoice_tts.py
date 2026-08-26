"""CosyVoice2 backend for the yue reader.

CosyVoice2 runs as a separate subprocess under its own venv (the .venv-cosyvoice
pins torch 2.13 / transformers 4.51.3 which must not touch the reader's venv).
It talks JSON-lines over stdin/stdout (see cosyvoice_tts_worker.py).

CosyVoice2 is reference-voice (zero-shot) only: every synthesis needs a prompt
audio + its transcript, which the worker picks per language. It auto-detects
English/Chinese per sentence and on MPS synthesises faster than realtime.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import select
import shutil
import subprocess
import threading
import time

from .base import TTSBase
from .. import config


class CosyVoiceTTS(TTSBase):
    """TTS implementation for CosyVoice2."""

    # CosyVoice2 produces far more natural prosody when given a whole paragraph
    # than isolated sentences, so the producer synthesizes paragraph-by-paragraph
    # and splits the audio into per-sentence segments.
    synthesize_paragraph = True

    @property
    def name(self) -> str:
        return "cosyvoice"

    @property
    def output_format(self) -> str:
        return "wav"

    def __init__(self, console, voice: str = None, lang: str = None):
        super().__init__(console, voice, lang)
        self._proc = None
        if self.voice is None:
            self.voice = config.TTS_VOICES.get(self.name)
        if self.lang is None:
            self.lang = config.TTS_LANGUAGE_CODES.get(self.name)
        if not self.lang:
            self.lang = "zh"

    def _start_stderr_drain(self):
        """Drain stderr on a daemon thread so the worker never deadlocks on a
        full pipe. A daemon thread (not a pool worker) is used so that a blocked
        readline() here can never pin the interpreter at exit."""
        def _drain():
            try:
                for line in self._proc.stderr:
                    line = line.rstrip("\n")
                    if line:
                        logging.debug("cosyvoice worker: %s", line)
            except Exception:  # noqa: BLE001
                pass

        t = threading.Thread(target=_drain, name="cosyvoice-stderr", daemon=True)
        t.start()

    def _run_blocking_in_daemon(self, fn, *args):
        """Run ``fn`` on a daemon thread, returning a Future for its result.

        The blocking synthesis read must not run on the default executor: its
        threads are non-daemon and ``concurrent.futures._python_exit`` joins
        them at interpreter exit, so a worker that wedges (and keeps a pipe open
        via a grandchild) would hang the reader on quit. A daemon thread never
        blocks interpreter exit.
        """
        fut = concurrent.futures.Future()

        def _run():
            try:
                fut.set_result(fn(*args))
            except BaseException as exc:  # noqa: BLE001
                fut.set_exception(exc)

        threading.Thread(target=_run, name="cosyvoice-synth", daemon=True).start()
        return fut

    async def initialize(self) -> bool:
        if self._proc is not None:
            return True
        env = {
            **os.environ,
            "YUE_COSYVOICE_REPO": config.COSYVOICE_REPO,
            "YUE_COSYVOICE_MODEL_DIR": config.COSYVOICE_MODEL_DIR,
            "YUE_COSYVOICE_PROMPT_DIR": config.COSYVOICE_PROMPT_DIR,
        }
        try:
            self._proc = subprocess.Popen(
                [
                    config.COSYVOICE_WORKER_PYTHON,
                    config.COSYVOICE_WORKER_SCRIPT,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__)),
            )
        except FileNotFoundError as e:
            self.console.print(
                f"[bold red]CosyVoice worker python not found: {e}[/bold red]"
            )
            logging.error("cosyvoice worker python not found: %s", e)
            self._proc = None
            return False

        self._start_stderr_drain()

        # Read the ready line without blocking the event loop (model load can
        # take a few seconds and must not freeze the reader UI).
        self.console.print(
            "[yellow]Loading CosyVoice2 (bilingual EN/CN)... a moment please.[/yellow]"
        )
        try:
            line = await asyncio.wait_for(
                asyncio.to_thread(self._proc.stdout.readline), timeout=120
            )
        except asyncio.TimeoutError:
            self.console.print(
                "[bold red]Timed out waiting for the CosyVoice worker.[/bold red]"
            )
            await self.shutdown()
            return False
        if not line:
            self.console.print(
                "[bold red]CosyVoice worker exited during startup.[/bold red]"
            )
            await self.shutdown()
            return False
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logging.error("cosyvoice bad ready line: %r", line)
            self.console.print("[bold red]CosyVoice worker sent a bad reply.[/bold red]")
            await self.shutdown()
            return False
        if "error" in data:
            self.console.print(f"[bold red]CosyVoice failed to load: {data['error']}[/bold red]")
            await self.shutdown()
            return False
        self.console.print("[green]CosyVoice2 ready.[/green]")
        self.initialized = True
        return True

    def _readline_with_timeout(self, timeout: float):
        """Read one JSON line from the worker's stdout, bounded by ``timeout``.

        Returns the decoded line, ``None`` on timeout, or ``""`` on EOF. Reading
        via ``select`` + ``os.read`` on a non-blocking fd means a wedged worker
        can never pin this executor thread forever — the caller can time out and
        recover (e.g. kill the worker) instead of hanging the reader on quit.
        """
        fd = self._proc.stdout.fileno()
        os.set_blocking(fd, False)
        buf = bytearray()
        deadline = time.monotonic() + timeout
        try:
            while b"\n" not in buf:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                r, _, _ = select.select([fd], [], [], max(0.0, remaining))
                if not r:
                    return None
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    return ""  # EOF
                buf.extend(chunk)
        finally:
            os.set_blocking(fd, True)
        return bytes(buf).split(b"\n", 1)[0].decode("utf-8", "replace")

    def _blocking_generate(self, text, output_path):
        request = {"text": text, "voice": self.voice, "out": output_path}
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        line = self._readline_with_timeout(config.COSYVOICE_WORKER_TIMEOUT)
        if line is None:
            # Worker wedged (e.g. MPS deadlock). Kill it so this thread returns
            # and the producer can fail the paragraph instead of hanging.
            logging.error("CosyVoice synthesis timed out; killing worker")
            self._force_kill()
            raise RuntimeError("CosyVoice synthesis timed out (worker killed)")
        if not line:
            raise RuntimeError("CosyVoice worker closed unexpectedly")
        data = json.loads(line)
        if "error" in data:
            raise RuntimeError(f"CosyVoice synthesis failed: {data['error']}")
        # worker writes directly to output_path; ensure it exists
        if not os.path.exists(output_path):
            shutil.copy(data["path"], output_path)

    def _force_kill(self):
        """Best-effort kill of the worker, without awaiting (called on timeout)."""
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        finally:
            self.initialized = False

    async def generate_audio(self, text: str, output_path: str):
        if not self.initialized or self._proc is None:
            raise RuntimeError("CosyVoice has not been initialized.")
        from .text_normalize import normalize_tts_text
        # CosyVoice2's English number/date/abbreviation normalization is weak;
        # spell English digits out so they read correctly. Chinese passes through.
        text = normalize_tts_text(text, getattr(self, "lang", ""))
        fut = self._run_blocking_in_daemon(self._blocking_generate, text, output_path)
        await asyncio.wrap_future(fut)

    async def shutdown(self):
        """Terminate the worker subprocess so the reader can exit cleanly.

        Closing stdin alone is not enough: if a synthesis is in flight the
        worker is blocked in inference and won't exit, and the reader's
        synthesis/stderr threads would stay stuck reading the worker's pipes.
        So we close stdin, give the worker a short grace period, then kill it.
        The blocking reads run on *daemon* threads (see ``_run_blocking_in_daemon``
        and ``_start_stderr_drain``), so even if the worker (or a grandchild
        inheriting its pipes) keeps a write end open and a thread stays blocked
        in read(), the interpreter is never joined on it at exit — it exits
        cleanly instead of hanging. We deliberately do NOT close stdout/stderr
        here: closing a pipe wrapper while another thread is blocked reading it
        deadlocks on the file lock.
        """
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:  # noqa: BLE001
                    pass
            await asyncio.get_running_loop().run_in_executor(
                None, self._wait_or_kill, proc
            )
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.initialized = False

    @staticmethod
    def _wait_or_kill(proc):
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
