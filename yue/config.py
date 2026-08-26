"""Configuration settings for the Yue eBook reader."""

import os
import shutil
from platformdirs import user_data_dir, user_cache_dir

# Default TTS model
DEFAULT_TTS_MODEL = "edge"

# Default voices for TTS models
TTS_VOICES = {
    "edge": "en-US-JennyNeural",
    "kokoro": "af_heart",
    "cosyvoice": "zh",  # default reference prompt (zh = Chinese female voice)
}

# Language codes for TTS models that require them
TTS_LANGUAGE_CODES = {
    "kokoro": "a",  # a=English, e=Spanish, j=Japanese, etc.
    "cosyvoice": "zh",  # CosyVoice2 auto-detects EN/CN; lang picks the prompt voice
}

# TTS model-specific seconds of overlap between sentences (overrides default OVERLAP_SECONDS if specified)
TTS_OVERLAP_SECONDS = {
    "kokoro": 0.6,
    "cosyvoice": 0.3,
}

# CosyVoice2 runs as a separate subprocess under its own venv (it pins
# torch/transformers versions that must not touch the reader's venv). It uses a
# JSON-lines protocol over stdin/stdout (see cosyvoice_tts_worker.py).
COSYVOICE_REPO = os.environ.get(
    "YUE_COSYVOICE_REPO",
    "/Users/randallsim/Documents/python_project/tts-training/CosyVoice",
)
COSYVOICE_MODEL_DIR = os.environ.get(
    "YUE_COSYVOICE_MODEL_DIR",
    "/Users/randallsim/Documents/python_project/tts-training/pretrained_models/CosyVoice2-0.5B",
)
COSYVOICE_PROMPT_DIR = os.environ.get(
    "YUE_COSYVOICE_PROMPT_DIR",
    "/Users/randallsim/Documents/python_project/tts-training/cosyvoice_prompts",
)
COSYVOICE_WORKER_PYTHON = os.environ.get(
    "YUE_COSYVOICE_WORKER_PYTHON",
    "/Users/randallsim/Documents/python_project/tts-training/.venv-cosyvoice/bin/python",
)
COSYVOICE_WORKER_SCRIPT = os.environ.get(
    "YUE_COSYVOICE_WORKER_SCRIPT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts", "cosyvoice_tts_worker.py"),
)

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
