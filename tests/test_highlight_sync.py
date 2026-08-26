"""Regression test for word-highlight state reset on sentence/paragraph/chapter
navigation.

When the reading position moves, `_reset_highlight_state` must drop the previous
sentence's word index and word-timing state, otherwise a stale index from the old
sentence keeps being drawn on the new one (the "highlight all over the place"
bug when changing paragraph/chapter).
"""

import unittest

from yue.reader import Yue


def _make_state():
    """A bare instance exposing only the fields `_reset_highlight_state` touches,
    so the reset logic is testable without constructing the full reader."""
    state = object.__new__(Yue)
    state.ui_word_idx = 7
    state.current_sentence_words = ["hello", "world"]
    state.current_word_timings = [("hello", 0.0, 0.5), ("world", 0.5, 1.0)]
    state.current_word_mapping = [0, 1]
    state.current_sentence_duration = 1.0
    return state


class TestHighlightSync(unittest.TestCase):
    def test_reset_clears_word_highlight_and_timing(self):
        state = _make_state()
        Yue._reset_highlight_state(state)
        self.assertEqual(state.ui_word_idx, 0)
        self.assertEqual(state.current_sentence_words, [])
        self.assertIsNone(state.current_word_timings)
        self.assertIsNone(state.current_word_mapping)
        self.assertEqual(state.current_sentence_duration, 0)

    def test_reset_is_idempotent(self):
        state = _make_state()
        Yue._reset_highlight_state(state)
        Yue._reset_highlight_state(state)
        self.assertEqual(state.ui_word_idx, 0)


if __name__ == "__main__":
    unittest.main()
