"""Regression test for a deterministic EPUB `dc:identifier`.

The identifier used Python's salted built-in `hash()`, so the same book
exported twice produced a different `urn:uuid:yue-...` value on every run.
The fix derives it from an md5 of the title, so identical titles yield
identical identifiers and different titles yield different ones.
"""

import re
import unittest

from yue.epub_exporter import _build_opf

_ID_RE = re.compile(r"urn:uuid:yue-\d+-([0-9a-f]+)")


def _identifier(title, chapter_count=1):
    opf = _build_opf(title, chapter_count)
    return _ID_RE.search(opf).group(1)


def _full_identifier(title, chapter_count=1):
    opf = _build_opf(title, chapter_count)
    return _ID_RE.search(opf).group(0)


class TestEpubIdentifier(unittest.TestCase):
    def test_same_title_is_deterministic(self):
        self.assertEqual(_identifier("My Book"), _identifier("My Book"))

    def test_different_titles_differ(self):
        self.assertNotEqual(_identifier("My Book"), _identifier("Other Book"))

    def test_chapter_count_is_part_of_identifier(self):
        self.assertNotEqual(
            _full_identifier("My Book", chapter_count=1),
            _full_identifier("My Book", chapter_count=2),
        )

    def test_identifier_is_hex(self):
        self.assertRegex(_identifier("My Book"), r"^[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
