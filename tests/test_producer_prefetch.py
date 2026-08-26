"""Regression tests for producer prefetch/backpressure.

CosyVoice paragraph synthesis is slow (tens of seconds), so the producer must
start synthesizing the next paragraph immediately after enqueuing the current
one — overlapping with playback — instead of blocking on the queue-full check
first (which caused a pause between every paragraph). The queue-full condition
is handled as backpressure at put-time, not as a failure.
"""

import asyncio

from yue import audio, config
from yue.reader import Yue


def test_producer_synthesizes_ahead_when_queue_full():
    async def scenario():
        reader = object.__new__(Yue)
        reader.chapters = [["p0", "p1"]]
        reader.chapter_idx = 0
        reader.paragraph_idx = 0
        reader.sentence_idx = 0
        reader.running = True
        reader.audio_queue = asyncio.Queue(maxsize=config.MAX_QUEUE_SIZE)

        tts = type("TTS", (), {"synthesize_paragraph": True, "initialized": True})()
        reader.tts_model = tts

        # Fill the queue so it is full.
        for _ in range(config.MAX_QUEUE_SIZE):
            await reader.audio_queue.put(("dummy", 0, 0, 0, 1.0, {}))

        called = []

        async def fake_produce(r, pos, buf):
            called.append(pos)
            r.running = False
            return None

        orig = audio._produce_paragraph
        audio._produce_paragraph = fake_produce
        try:
            await audio._producer_loop(reader)
        finally:
            audio._produce_paragraph = orig

        assert called, "producer did not synthesize the next paragraph while the queue was full"

    asyncio.run(scenario())


def test_put_item_backpressures_rather_than_failing():
    async def scenario():
        reader = object.__new__(Yue)
        reader.running = True
        reader.audio_queue = asyncio.Queue(maxsize=2)
        await reader.audio_queue.put("a")
        await reader.audio_queue.put("b")  # queue now full

        put_task = asyncio.create_task(audio._put_item(reader, "c"))
        await asyncio.sleep(0.05)
        assert put_task.done() is False, "put should block (backpressure), not fail"

        # Free space -> the pending put completes.
        assert reader.audio_queue.get_nowait() == "a"
        reader.audio_queue.task_done()
        assert await asyncio.wait_for(put_task, timeout=1.0) is True

        # Verify the item was eventually queued.
        assert reader.audio_queue.get_nowait() == "b"
        reader.audio_queue.task_done()
        assert reader.audio_queue.get_nowait() == "c"
        reader.audio_queue.task_done()

    asyncio.run(scenario())
