"""Tests for the CosyVoice2 English text-normalization pass."""

import unittest

from yue.tts.text_normalize import normalize_tts_text


class TestNormalize(unittest.TestCase):
    def normalize(self, text, lang=""):
        return normalize_tts_text(text, lang)

    def test_years(self):
        self.assertEqual(self.normalize("in 2024", "en"), "in twenty twenty-four")
        self.assertEqual(self.normalize("in 1950", "en"), "in nineteen fifty")

    def test_quantities(self):
        self.assertEqual(self.normalize("34 people", "en"), "thirty-four people")
        self.assertEqual(self.normalize("1,234 words", "en"), "one thousand, two hundred and thirty-four words")
        self.assertEqual(self.normalize("5,000 runners", "en"), "five thousand runners")

    def test_decimal_and_percent(self):
        self.assertEqual(self.normalize("12.5% growth", "en"), "twelve point five percent growth")
        self.assertEqual(self.normalize("0.5 percent", "en"), "zero point five percent")

    def test_ordinal(self):
        self.assertEqual(self.normalize("the 3rd", "en"), "the third")
        self.assertEqual(self.normalize("10th place", "en"), "tenth place")

    def test_abbreviations(self):
        self.assertEqual(self.normalize("Dr. Smith", "en"), "doctor Smith")
        self.assertEqual(self.normalize("Co. and Inc.", "en"), "company and incorporated")
        self.assertEqual(self.normalize("5:30 p.m.", "en"), "five thirty p m")

    def test_mixed_sentence(self):
        out = self.normalize(
            "In 2024, Dr. Smith saw 34 people in 1,234 homes.", "en"
        )
        self.assertIn("twenty twenty-four", out)
        self.assertIn("doctor Smith", out)
        self.assertIn("thirty-four", out)

    def test_chinese_passthrough(self):
        self.assertEqual(self.normalize("三千四百万游客", "zh"), "三千四百万游客")
        # Chinese book sentence with digits stays untouched.
        self.assertEqual(self.normalize("有 34 个人。", "zh"), "有 34 个人。")

    def test_empty(self):
        self.assertEqual(self.normalize(""), "")


if __name__ == "__main__":
    unittest.main()
