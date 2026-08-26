"""CosyVoice2 backend for the yue reader.

CosyVoice2 runs as a separate subprocess under its own venv (the .venv-cosyvoice
pins torch 2.13 / transformers 4.51.3 which must not touch the reader's venv).
It talks JSON-lines over stdin/stdout (see cosyvoice_tts_worker.py).

CosyVoice2 is reference-voice (zero-shot) only: every synthesis needs a prompt
audio + its transcript, which the worker picks per language. It auto-detects
English/Chinese per sentence and on MPS synthesises faster than realtime.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess

from .base import TTSBase
from .. import config


class CosyVoiceTTS(TTSBase):
    """TTS implementation for CosyVoice2."""

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
        """Drain stderr in a thread so the worker never deadlocks on a full pipe."""
        loop = asyncio.get_running_loop()

        def _drain():
            for line in self._proc.stderr:
                line = line.rstrip("\n")
                if line:
                    logging.debug("cosyvoice worker: %s", line)

        loop.run_in_executor(None, _drain)

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

    def _blocking_generate(self, text, output_path):
        request = {"text": text, "voice": self.voice, "out": output_path}
        self._proc.stdin.write(json.dumps(request) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("CosyVoice worker closed unexpectedly")
        data = json.loads(line)
        if "error" in data:
            raise RuntimeError(f"CosyVoice synthesis failed: {data['error']}")
        # worker writes directly to output_path; ensure it exists
        if not os.path.exists(output_path):
            shutil.copy(data["path"], output_path)

    async def generate_audio(self, text: str, output_path: str):
        if not self.initialized or self._proc is None:
            raise RuntimeError("CosyVoice has not been initialized.")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._blocking_generate, text, output_path)

    async def shutdown(self):
        """Terminate the worker subprocess so the reader can exit cleanly.

        Closing stdin alone is not enough: if a synthesis is in flight the
        worker is blocked in inference and won't exit, and the reader's
        executor thread would stay stuck on readline() forever. So we close
        stdin, give the worker a short grace period, then kill it.
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
