"""Regression test: a failed/empty paragraph must not kill the audio producer.

When paragraph synthesis raised (e.g. the CosyVoice worker returning
``{error: 'empty text'}`` for a formatting-only paragraph), the uncaught
exception killed ``_producer_loop`` and reading stopped after whatever was
already queued. The producer now (a) skips paragraphs that sanitize to empty
text and (b) catches a failed paragraph synthesis, logs it, and advances to the
next paragraph so reading continues.
"""

import asyncio

import numpy as np
import soundfile as sf

from yue import audio
from yue.reader import Yue


class FakeTTS:
    synthesize_paragraph = True
    initialized = True
    output_format = "wav"

    def __init__(self, fail_text=None):
        self.fail_text = fail_text
        self.calls = []
        self.generated = []

    async def generate_audio(self, text, path):
        self.calls.append(text)
        if self.fail_text is not None and text == self.fail_text:
            raise RuntimeError("CosyVoice synthesis failed: empty text")
        # A short silence wav is enough for the silence splitter to work.
        sf.write(path, np.zeros(12000, dtype=np.float32), 24000)
        self.generated.append(text)


def _make_reader(chapters, tts):
    reader = object.__new__(Yue)
    reader.chapters = chapters
    reader.chapter_idx = 0
    reader.paragraph_idx = 0
    reader.sentence_idx = 0
    reader.running = True
    reader.audio_queue = asyncio.Queue(maxsize=50)
    reader.tts_model = tts
    return reader


def _run_producer(reader):
    return audio._producer_loop(reader)


def test_empty_paragraph_is_skipped_without_tts_call():
    # A paragraph of only header/symbol chars sanitizes to empty text.
    chapters = [[
        "Good morning. The sun is rising.",
        "### @@@ ^^^",                      # sanitizes to "" -> must be skipped
        "Birds are singing. It is spring.",
    ]]
    tts = FakeTTS()
    reader = _make_reader(chapters, tts)

    async def scenario():
        await _run_producer(reader)

    asyncio.run(scenario())

    # The empty paragraph must never reach the worker; the other two do.
    assert len(tts.generated) == 2
    assert "sun is rising" in tts.generated[0]
    assert "Birds are singing" in tts.generated[1]
    assert not any("###" in t for t in tts.calls)


def test_failed_paragraph_does_not_kill_producer():
    chapters = [[
        "First paragraph. It works.",
        "This paragraph will fail to synthesize.",
        "Third paragraph. Reading continues.",
    ]]
    # Make the second paragraph's sanitized text raise.
    from yue.content_parser import sanitize_text_for_tts
    fail_text = sanitize_text_for_tts(chapters[0][1])
    tts = FakeTTS(fail_text=fail_text)
    reader = _make_reader(chapters, tts)

    async def scenario():
        await _run_producer(reader)

    asyncio.run(scenario())

    # Producer survives the failure and produces paragraphs 0 and 2.
    assert len(tts.generated) == 2
    assert "First paragraph" in tts.generated[0]
    assert "Third paragraph" in tts.generated[1]
