"""Configuration settings for the Yue eBook reader."""

import os
import shutil
from platformdirs import user_data_dir, user_cache_dir

# Default TTS model
DEFAULT_TTS_MODEL = "xtts"

# Default voices for TTS models
TTS_VOICES = {
    "edge": "en-US-JennyNeural",
    "kokoro": "af_heart",
    "xtts": "en-US-AvaMultilingualNeural",
}

# Language codes for TTS models that require them
TTS_LANGUAGE_CODES = {
    "kokoro": "a",  # a=English, e=Spanish, j=Japanese, etc.
    "xtts": "en",   # XTTS v2 language id (en, zh-cn, es, fr, ...)
}

# TTS model-specific seconds of overlap between sentences (overrides default OVERLAP_SECONDS if specified)
TTS_OVERLAP_SECONDS = {
    "kokoro": 0.6,
    # XTTS already ends each utterance with a short natural pause. A large
    # overlap cuts into that pause and makes sentences feel rushed, so keep it
    # small (next sentence starts only slightly before the current tail ends).
    "xtts": 0.1,
}

# XTTS v2 model checkpoint directory (contains config.json, model.pth, vocab.json).
XTTS_CHECKPOINT_DIR = os.environ.get(
    "YUE_XTTS_CHECKPOINT_DIR",
    "/Users/randallsim/Documents/python_project/tts-training/checkpoints/xtts_v2",
)

# Directory containing XTTS v2 .pt voice profiles (gpt_cond_latent + speaker_embedding).
XTTS_PROFILES_DIR = os.environ.get(
    "YUE_XTTS_PROFILES_DIR",
    "/Users/randallsim/Documents/python_project/tts-training/edge_voice_profiles/profiles",
)

# XTTS runs in a separate Python 3.10 venv (the coqui TTS package does not
# support Python 3.12). Yue drives the importable ``yue_voice`` package from
# that interpreter — ``python -m <XTTS_MODULE> worker|server`` — so it can run
# and trigger the XTTS server entirely on its own.
XTTS_WORKER_PYTHON = os.environ.get(
    "YUE_XTTS_WORKER_PYTHON",
    "/Users/randallsim/Documents/python_project/tts-training/.venv/bin/python",
)
# The installed package exposing the XTTS engine/server/worker (importable by
# any Python code; `pip install yue_voice`). Override to point at a fork.
XTTS_MODULE = os.environ.get("YUE_XTTS_MODULE", "yue_voice")
# Where the standalone XTTS HTTP server binds when launched via `yue xtts-server`.
XTTS_SERVER_HOST = os.environ.get("YUE_XTTS_HOST", "0.0.0.0")
XTTS_SERVER_PORT = int(os.environ.get("YUE_XTTS_PORT", "8765"))

# Target trailing silence (the pause after a full stop) that the XTTS worker
# pads each sentence to, in seconds. Tune this to control how much breathing
# room there is between sentences. 0 disables the padding.
XTTS_TRAILING_PAUSE = float(os.environ.get("YUE_XTTS_TRAILING_PAUSE", "0.4"))

# Per-voice XTTS synthesis speed multiplier (<1 = slower, >1 = faster; 1.0 =
# normal). Some profiles read faster than others (af_bella runs ~35% faster
# than the Edge-derived voices), so give them a slight slowdown. Tune per voice.
XTTS_VOICE_SPEEDS = {
    "af_bella": 0.95,
}

# XTTS decoder sampling temperature. Lower = more deterministic/more consistent
# pacing (less "sometimes fast, sometimes slow"); higher = more natural variety
# but more erratic timing. XTTS's own default is 0.75.
XTTS_TEMPERATURE = float(os.environ.get("YUE_XTTS_TEMPERATURE", "0.6"))

# Audio processing settings
AUDIO_DATA_DIR = user_cache_dir("yue")
os.makedirs(AUDIO_DATA_DIR, exist_ok=True)
AUDIO_BUFFERS = [os.path.join(AUDIO_DATA_DIR, f"buffer_{i}") for i in range(6)]
MAX_QUEUE_SIZE = 4
OVERLAP_SECONDS = 0.5 # Seconds of overlap between sentences

# Progress tracking settings
PROGRESS_FILE_DIR = user_data_dir("yue")
os.makedirs(PROGRESS_FILE_DIR, exist_ok=True)

# The project was called "lue" before, so an existing install keeps its reading
# progress and settings.json under that app name. Carry them over once, on the
# first run after upgrading, or every book would silently restart at position 0
# and the start menu would re-run its first-run wizard.
LEGACY_DATA_DIR = user_data_dir("lue")


def _is_user_state(name):
    """True for the files we persist: per-book progress and settings.json."""
    return name.endswith(".progress.json") or name == "settings.json"


def migrate_legacy_data_dir(src=LEGACY_DATA_DIR, dst=PROGRESS_FILE_DIR):
    """Copy pre-rename user state from `src` into `dst`, once.

    Skipped entirely if `dst` already holds state, so this can never clobber
    newer progress, and the originals are left in place as a fallback. Copy
    failures are ignored: a missing bookmark must not stop the reader starting.

    Returns the number of files copied (0 when nothing needed migrating).
    """
    if os.path.abspath(src) == os.path.abspath(dst) or not os.path.isdir(src):
        return 0
    try:
        if any(_is_user_state(n) for n in os.listdir(dst)):
            return 0
        names = [n for n in os.listdir(src) if _is_user_state(n)]
    except OSError:
        return 0

    copied = 0
    for name in names:
        source = os.path.join(src, name)
        if not os.path.isfile(source):
            continue
        try:
            shutil.copy2(source, os.path.join(dst, name))
            copied += 1
        except OSError:
            continue
    return copied


migrate_legacy_data_dir()

# General settings
SHOW_ERRORS_ON_EXIT = True

# PDF parsing settings
PDF_FILTERS_ENABLED = False  # You can also enable this with the --filter or -f command-line option
PDF_FILTER_HEADERS = True  # Filter headers in top margin of pages
PDF_FILTER_FOOTNOTES = True  # Filter page numbers and footnotes in bottom margin of pages

# PDF filtering thresholds (only used when respective filters are enabled)
PDF_HEADER_MARGIN = 0.1  # Top 10% of page considered header area
PDF_FOOTNOTE_MARGIN = 0.1  # Bottom 10% of page considered footnote area

# UI settings
SMOOTH_SCROLLING_ENABLED = True  # Enable smooth scrolling for keyboard navigation
UI_MODE = 2  # 0=minimal (text only), 1=medium (top bar only), 2=full (default), 3=speed reading

# Highlighting settings
SENTENCE_HIGHLIGHTING_ENABLED = True  # Enable sentence-level highlighting
WORD_HIGHLIGHT_MODE = 1  # 0=off, 1=normal highlighting, 2=standout highlighting

# Front/back matter skipping. When enabled (the default) Yue leaves out
# navigation/boilerplate sections while reading: the title page, copyright
# page, table of contents, dedication, index, about the author/publisher, and
# similar. For EPUBs whole spine files are dropped; for single-file formats
# (PDF/TXT/DOCX/HTML/RTF/MD) a conservative paragraph-level pass is applied.
# Set to False to keep everything in the book.
SKIP_FRONT_MATTER = True
SKIP_BACK_MATTER = True

# When opening a single-file format (PDF/TXT/DOCX/HTML/RTF/MD), Yue can offer
# to save the book as a standard EPUB for use in other readers. Set to False to
# never ask.
OFFER_EPUB_CONVERSION = True

# Keyboard settings
# Can be set to "default", "vim", or a path to a custom keyboard shortcuts JSON file
CUSTOM_KEYBOARD_SHORTCUTS = "default"
