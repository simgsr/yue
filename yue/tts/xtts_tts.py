"""XTTS v2 TTS provider for Yue, using pre-extracted .pt voice profiles.

The coqui ``TTS`` package does not support Python 3.12 (Yue's interpreter), so
this provider drives a persistent worker subprocess that runs under the
tts-training venv (Python 3.10). The worker loads the XTTS v2 checkpoint and
voice profiles once and serves synthesis requests over stdin/stdout.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import warnings

from rich.console import Console

from .base import TTSBase
from .. import config

warnings.filterwarnings("ignore")


class XttsTTS(TTSBase):
    """TTS implementation for XTTS v2 using .pt voice profiles."""

    @property
    def name(self) -> str:
        return "xtts"

    @property
    def output_format(self) -> str:
        return "wav"

    def __init__(self, console: Console, voice: str = None, lang: str = None):
        super().__init__(console, voice, lang)
        self._proc: subprocess.Popen | None = None
        self._stderr_task = None

        if self.voice is None:
            self.voice = config.TTS_VOICES.get(self.name)
        if self.lang is None:
            self.lang = config.TTS_LANGUAGE_CODES.get(self.name)

    @staticmethod
    def _detect_lang(text: str, default: str = "en") -> str:
        """Pick an XTTS language id from the text's dominant script.

        The extracted profiles are multilingual, so this lets one voice read
        English, Chinese, and Japanese without the user selecting a language.
        Returns ``zh-cn`` / ``ja`` for CJK-heavy text, otherwise ``default``.
        """
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")   # Han
        kana = sum(1 for c in text if "\u3040" <= c <= "\u30ff")  # Hiragana/Katakana
        letters = sum(1 for c in text if c.isalnum())
        if letters == 0:
            return default
        if (cjk + kana) / letters > 0.15:
            return "ja" if kana > cjk else "zh-cn"
        return default

    async def initialize(self) -> bool:
        """Spawn the persistent XTTS worker subprocess and wait for it to be ready."""
        if self._proc is not None:
            return True
        try:
            env = {
                **os.environ,
                "XTTS_CKPT_DIR": config.XTTS_CHECKPOINT_DIR,
                "XTTS_PROFILES_DIR": config.XTTS_PROFILES_DIR,
                "XTTS_TRAILING_PAUSE": str(config.XTTS_TRAILING_PAUSE),
            }
            self._proc = subprocess.Popen(
                [config.XTTS_WORKER_PYTHON, "-m", config.XTTS_MODULE, "worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            # Drain the worker's stderr continuously. The coqui/TTS library
            # writes progress/warnings to stderr during model load and synthesis;
            # if it fills the 64KB pipe buffer the worker blocks on write and the
            # reader (which reads stdout) deadlocks with it — the "reader stalls /
            # repeats after a few sentences" bug. Draining keeps the pipe open.
            self._start_stderr_drain()
        except FileNotFoundError:
            self.console.print(
                f"[bold red]Error: XTTS worker interpreter not found: "
                f"{config.XTTS_WORKER_PYTHON}[/bold red]"
            )
            return False

        # Wait for the worker's ready/error line.
        line = self._proc.stdout.readline()
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            self.console.print("[bold red]Error: XTTS worker produced invalid output.[/bold red]")
            self._shutdown()
            return False

        if data.get("error"):
            self.console.print(f"[bold red]Error starting XTTS worker: {data['error']}[/bold red]")
            self._shutdown()
            return False

        self.initialized = True
        self.console.print("[green]XTTS v2 model is available.[/green]")
        return True

    def _start_stderr_drain(self) -> None:
        """Kick off a background thread that reads the worker's stderr so the
        pipe buffer can never fill and deadlock the worker against the reader.
        """
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return
        try:
            self._stderr_task = loop.run_in_executor(None, self._drain_stderr_blocking)
        except RuntimeError:
            self._stderr_task = None

    def _drain_stderr_blocking(self) -> None:
        """Blocking loop, run in an executor thread: consume worker stderr lines.
        Ends on EOF (worker process exited) or if the handle is gone.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if line and line.strip():
                    logging.debug("XTTS worker stderr: %s", line.rstrip())
        except Exception:  # noqa: BLE001
            pass

    def _shutdown(self) -> None:
        if self._stderr_task is not None:
            try:
                self._stderr_task.cancel()
            except Exception:  # noqa: BLE001
                pass
            self._stderr_task = None
        if self._proc is not None:
            try:
                self._proc.stdin.close()
                self._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None

    async def generate_audio(self, text: str, output_path: str):
        """Synthesize text with the worker and copy the WAV to ``output_path``."""
        if not self.initialized or self._proc is None:
            raise RuntimeError("XTTS TTS has not been initialized.")

        def _blocking_generate():
            lang = self._detect_lang(text, self.lang)
            speed = config.XTTS_VOICE_SPEEDS.get(self.voice, 1.0)
            req = json.dumps({
                "text": text, "lang": lang, "voice": self.voice,
                "speed": speed, "temperature": config.XTTS_TEMPERATURE,
            })
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("XTTS worker closed unexpectedly.")
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(f"XTTS synthesis failed: {data['error']}")
            shutil.copy(data["path"], output_path)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _blocking_generate)
