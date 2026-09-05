"""Regression test: importing the Kokoro backend must have no side effects.

The module used to set `warnings.filterwarnings("ignore")` and several
`HF_HUB_*` environment variables at import time, so merely importing the
backend (even when Kokoro is never used) silenced warnings globally and
mutated the process environment. Those side effects now run inside
`initialize()`, so a plain import must leave the environment untouched.
"""

import os
import unittest

_HF_ENV_KEYS = (
    "HF_HUB_DISABLE_TELEMETRY",
    "HF_HUB_ETAG_TIMEOUT",
    "HF_HUB_DOWNLOAD_TIMEOUT",
)


class TestKokoroImportSideEffects(unittest.TestCase):
    def test_import_leaves_environment_untouched(self):
        before = {key: os.environ.get(key) for key in _HF_ENV_KEYS}
        import yue.tts.kokoro_tts  # noqa: F401
        after = {key: os.environ.get(key) for key in _HF_ENV_KEYS}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
