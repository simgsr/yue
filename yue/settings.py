"""Persistent per-user settings for the Yue start menu.

Yue otherwise keeps no general settings store (yue/config.py is all constants
and progress_manager only persists per-book progress), so this module owns a
single small settings.json next to the progress files. Only a handful of known
keys are written; unknown keys in the file are ignored on load so hand-edited
or older files never crash the reader.

The path can be overridden with the YUE_SETTINGS_FILE environment variable
(used by the pty test harness so it does not touch real user settings). The
pre-rename name LUE_SETTINGS_FILE is still honoured as a fallback: a harness
that set only the old name would otherwise fall through to this path without
any error and read and write the developer's real settings.
"""

import json
import os

from . import config

SETTINGS_FILE = (
    os.environ.get("YUE_SETTINGS_FILE")
    or os.environ.get("LUE_SETTINGS_FILE")
    or os.path.join(config.PROGRESS_FILE_DIR, "settings.json")
)

# Keys and defaults. New/unknown keys fall back to these so a stale or
# hand-edited file cannot crash the loader.
DEFAULTS = {
    # Folder `yue ui` starts browsing from (empty = current working dir).
    "default_start_dir": "",
    # Language the voice list is filtered to ("" = all voices).
    "default_language": "",
}


def load_settings() -> dict:
    """Return persisted settings merged over DEFAULTS (never raises)."""
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save_settings(settings: dict) -> None:
    """Persist settings, keeping only known keys (never raises)."""
    try:
        os.makedirs(config.PROGRESS_FILE_DIR, exist_ok=True)
        data = dict(DEFAULTS)
        data.update({k: v for k, v in settings.items() if k in DEFAULTS})
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except OSError:
        pass
