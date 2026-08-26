"""Tests for the paragraph audio splitter."""

import os
import tempfile
import unittest

import numpy as np
import soundfile as sf

from yue.tts.paragraph_split import split_audio_by_silence


def _make_audio(n_segments, sr=24000):
    """Build a mono signal: n_segments tones separated by 0.5s of silence."""
    tone = 0.5 * np.sin(2 * np.pi * 220 * np.arange(int(0.3 * sr)) / sr)
    silence = np.zeros(int(0.5 * sr))
    parts = []
    for i in range(n_segments):
        if i:
            parts.append(silence)
        parts.append(tone)
    return np.concatenate(parts)


class TestSplit(unittest.TestCase):
    def test_splits_into_n_segments(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.wav")
            sf.write(src, _make_audio(3), 24000)
            segs = split_audio_by_silence(src, 3, d, out_prefix="p")
            self.assertEqual(len(segs), 3)
            for s in segs:
                self.assertTrue(os.path.exists(s))

    def test_total_duration_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.wav")
            data = _make_audio(4)
            sf.write(src, data, 24000)
            segs = split_audio_by_silence(src, 4, d, out_prefix="p")
            total = sum(len(sf.read(s)[0]) for s in segs)
            self.assertAlmostEqual(total, len(data), delta=2000)

    def test_fallback_when_no_silence(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "in.wav")
            # Continuous tone, no silence -> proportional fallback.
            sf.write(src, 0.5 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000), 24000)
            segs = split_audio_by_silence(src, 2, d, out_prefix="p")
            self.assertEqual(len(segs), 2)


if __name__ == "__main__":
    unittest.main()
