"""Regression tests for the temporary file `--guide` writes.

The guide used to be written to a fixed path in the shared temp directory.
On systems where that directory is world-writable another local user could
pre-create the name as a symlink, and the write would follow it and truncate
the target. These tests pin the fix: an unguessable private directory.

Run with: python -m unittest discover tests
"""

import os
import stat
import subprocess
import sys
import tempfile
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `tempfile.gettempdir()` caches its answer on first use, so TMPDIR has to be
# set before the interpreter starts. Each case therefore runs in a subprocess.
RUNNER = """
import json, os, sys
sys.path.insert(0, {root!r})
from yue.__main__ import get_guide_file_path
path = get_guide_file_path()
print(json.dumps({{"path": path}}))
"""


def run_with_tmpdir(tmpdir, setup=None):
    """Call get_guide_file_path() in a subprocess using `tmpdir` as TMPDIR."""
    if setup is not None:
        setup(tmpdir)
    env = dict(os.environ, TMPDIR=tmpdir)
    proc = subprocess.run(
        [sys.executable, "-c", RUNNER.format(root=PROJECT_ROOT)],
        capture_output=True, text=True, env=env, cwd=tmpdir,
    )
    if proc.returncode != 0:
        raise AssertionError(f"runner failed: {proc.stderr}")
    import json
    return json.loads(proc.stdout.strip().splitlines()[-1])["path"]


class GuideTempFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_does_not_follow_planted_symlink(self):
        """A symlink at the old predictable name must not be written through."""
        victim = os.path.join(self.tmp, "victim")
        original = "important data\n"
        with open(victim, "w") as fh:
            fh.write(original)

        def plant(d):
            os.symlink(victim, os.path.join(d, "Yue Navigation Guide.txt"))

        path = run_with_tmpdir(self.tmp, setup=plant)

        with open(victim) as fh:
            self.assertEqual(fh.read(), original, "victim file was overwritten")
        self.assertNotEqual(
            path, os.path.join(self.tmp, "Yue Navigation Guide.txt"),
            "guide still uses the predictable shared-tempdir path",
        )

    def test_guide_dir_is_private_and_unpredictable(self):
        path = run_with_tmpdir(self.tmp)
        guide_dir = os.path.dirname(path)

        self.assertNotEqual(
            os.path.realpath(guide_dir), os.path.realpath(self.tmp),
            "guide must not be written directly into the shared temp dir",
        )
        mode = stat.S_IMODE(os.stat(guide_dir).st_mode)
        self.assertEqual(mode, 0o700, f"guide dir mode is {mode:o}, expected 700")

    def test_guide_is_readable_and_keeps_its_title(self):
        """The fix must not change the name the reader shows as the title."""
        path = run_with_tmpdir(self.tmp)

        self.assertEqual(os.path.basename(path), "Yue Navigation Guide.txt")
        # reader.py derives book_title this way; it keys the progress file.
        title = os.path.splitext(os.path.basename(path))[0]
        self.assertEqual(title, "Yue Navigation Guide")
        with open(path, encoding="utf-8") as fh:
            self.assertIn("Welcome to Yue", fh.read())


if __name__ == "__main__":
    unittest.main()
