"""Configuration settings for the Yue eBook reader."""

import os
from platformdirs import user_data_dir, user_cache_dir

# Default TTS model
DEFAULT_TTS_MODEL = "edge"

# Default voices for TTS models
TTS_VOICES = {
    "edge": "en-US-EmmaMultilingualNeural",
    "kokoro": "af_heart",
}

# Language codes for TTS models that require them
TTS_LANGUAGE_CODES = {
    "kokoro": "a",  # a=English, e=Spanish, j=Japanese, etc.
}

# TTS model-specific seconds of overlap between sentences (overrides default OVERLAP_SECONDS if specified).
# A NEGATIVE value means a real pause is inserted between sentences instead of
# overlapping them.
TTS_OVERLAP_SECONDS = {
    "kokoro": 0.6,
}

# Extra pause (seconds) inserted at paragraph boundaries, per model.
TTS_PARAGRAPH_PAUSE_SECONDS = {
    "edge": 0.4,
    "kokoro": 0.5,
}
# Fallback used when a model has no specific paragraph pause set.
PARAGRAPH_PAUSE_SECONDS = 0.5

# Audio processing settings
AUDIO_DATA_DIR = user_cache_dir("yue")
os.makedirs(AUDIO_DATA_DIR, exist_ok=True)
AUDIO_BUFFERS = [os.path.join(AUDIO_DATA_DIR, f"buffer_{i}") for i in range(6)]
# Number of audio items buffered ahead of the player.
MAX_QUEUE_SIZE = 4
OVERLAP_SECONDS = 0.5 # Seconds of overlap between sentences

# Progress tracking settings
PROGRESS_FILE_DIR = user_data_dir("yue")
os.makedirs(PROGRESS_FILE_DIR, exist_ok=True)

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
