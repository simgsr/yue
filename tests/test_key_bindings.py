"""Regression tests for the documented display keys.

The README documents `v` (cycle UI modes), `w` (word highlight) and `s`
(sentence highlight), but the input handler never mapped them, so the keys
did nothing. These tests pin the re-binding: the keys must dispatch to the
matching commands, and the built-in layout files must define them. The dead
`c`/`w` temperature bindings (left over from removed TTS backends) must be gone.
"""

import json
import os
import unittest

from yue import input_handler


class _FakeReader:
    """Minimal reader exposing only what `_process_normal_key` touches."""

    def __init__(self):
        self.commands = []
        self.running = True
        self.command_received_event = type("E", (), {"set": lambda self: None})()
        self.left_arrow_chapter_start = False

    def post_command(self, cmd):
        self.commands.append(cmd)


def _dispatch(key):
    reader = _FakeReader()
    input_handler._process_normal_key(reader, key)
    return reader.commands


class TestDisplayKeyBindings(unittest.TestCase):
    def test_v_cycles_ui_complexity(self):
        self.assertEqual(_dispatch("v"), ["cycle_ui_complexity"])

    def test_s_toggles_sentence_highlight(self):
        self.assertEqual(_dispatch("s"), ["toggle_sentence_highlight"])

    def test_w_toggles_word_highlight(self):
        self.assertEqual(_dispatch("w"), ["toggle_word_highlight"])

    def test_speed_keys_still_work(self):
        self.assertEqual(_dispatch(","), ["decrease_speed"])
        self.assertEqual(_dispatch("."), ["increase_speed"])

    def test_layout_files_define_display_keys(self):
        for name in ("keys_default.json", "keys_vim.json"):
            path = os.path.join(os.path.dirname(input_handler.__file__), name)
            with open(path, encoding="utf-8") as fh:
                layout = json.load(fh)
            display = layout["display_controls"]
            self.assertEqual(display["cycle_ui_complexity"], "v", name)
            self.assertEqual(display["toggle_sentence_highlight"], "s", name)
            self.assertEqual(display["toggle_word_highlight"], "w", name)

    def test_temperature_bindings_removed(self):
        for name in ("keys_default.json", "keys_vim.json"):
            path = os.path.join(os.path.dirname(input_handler.__file__), name)
            with open(path, encoding="utf-8") as fh:
                layout = json.load(fh)
            self.assertNotIn("decrease_temperature", layout["tts_controls"], name)
            self.assertNotIn("increase_temperature", layout["tts_controls"], name)


if __name__ == "__main__":
    unittest.main()
