"""Spark-TTS bilingual (English + Chinese) TTS provider for Yue.

Spark-TTS is an efficient LLM-based model with a single model that natively
handles both English and Chinese (including code-switching), so one voice reads
both languages with steady pacing. It runs as a separate subprocess
(``spark_tts_worker.py``) under its own venv (``config.SPARK_TTS_WORKER_PYTHON``)
so its pinned torch/transformers versions never touch the reader's venv.

The "voice" here maps to a virtual speaker (gender/pitch/speed). Optionally a
reference clip can be supplied for zero-shot voice cloning.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess

from rich.console import Console

from .base import TTSBase
from .. import config

# Map voice name -> (gender, pitch, speed) for the virtual speaker.
_VOICES = {
    "female": ("female", "moderate", "moderate"),
    "male": ("male", "moderate", "moderate"),
}


class SparkTTS(TTSBase):
    """Spark-TTS bilingual TTS using a persistent worker subprocess."""

    @property
    def name(self) -> str:
        return "spark"

    @property
    def output_format(self) -> str:
        return "wav"

    def __init__(self, console: Console, voice: str = None, lang: str = None):
        super().__init__(console, voice, lang)
        self._proc: subprocess.Popen | None = None
        self._stderr_task = None

        if self.voice is None:
            self.voice = config.TTS_VOICES.get(self.name, "female")
        if self.lang is None:
            self.lang = config.TTS_LANGUAGE_CODES.get(self.name, "en")

    async def initialize(self) -> bool:
        """Spawn the Spark-TTS worker subprocess and wait for it to be ready."""
        if self._proc is not None:
            return True
        env = {
            **os.environ,
            "YUE_SPARK_TTS_REPO": config.SPARK_TTS_REPO,
            "YUE_SPARK_TTS_MODEL_DIR": config.SPARK_TTS_MODEL_DIR,
        }
        try:
            self._proc = subprocess.Popen(
                [config.SPARK_TTS_WORKER_PYTHON, config.SPARK_TTS_WORKER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            self._start_stderr_drain()
        except FileNotFoundError:
            self.console.print(
                f"[bold red]Error: Spark-TTS worker interpreter not found: "
                f"{config.SPARK_TTS_WORKER_PYTHON}[/bold red]"
            )
            return False

        line = self._proc.stdout.readline()
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            self.console.print("[bold red]Error: Spark-TTS worker produced invalid output.[/bold red]")
            self._shutdown()
            return False

        if data.get("error"):
            self.console.print(f"[bold red]Error starting Spark-TTS worker: {data['error']}[/bold red]")
            self._shutdown()
            return False

        self.initialized = True
        self.console.print("[green]Spark-TTS bilingual model is available.[/green]")
        return True

    def _start_stderr_drain(self) -> None:
        """Drain the worker's stderr so the pipe can never fill and deadlock."""
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            self._stderr_task = loop.run_in_executor(None, self._drain_stderr_blocking)
        except RuntimeError:
            self._stderr_task = None

    def _drain_stderr_blocking(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for line in proc.stderr:
                if line and line.strip():
                    logging.debug("Spark-TTS worker stderr: %s", line.rstrip())
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
            raise RuntimeError("Spark-TTS has not been initialized.")

        gender, pitch, speed = _VOICES.get(self.voice, _VOICES["female"])

        def _blocking_generate():
            req = json.dumps({"text": text, "gender": gender, "pitch": pitch, "speed": speed})
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("Spark-TTS worker closed unexpectedly.")
            data = json.loads(line)
            if data.get("error"):
                raise RuntimeError(f"Spark-TTS synthesis failed: {data['error']}")
            shutil.copy(data["path"], output_path)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _blocking_generate)

    def get_overlap_seconds(self) -> float | None:
        """Spark-TTS reads at a steady pace with natural pauses; small overlap keeps flow."""
        return config.TTS_OVERLAP_SECONDS.get(self.name)
