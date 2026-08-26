"""Interactive in-terminal start menu for the Yue eBook reader.

Lets the user pick a book file (browsing the filesystem, recent books first)
and configure TTS (model, voice, language, speed) before reading starts.
Returns a MenuResult, or None if the user cancels.
"""

import asyncio
import os
import sys
import signal
import termios
import tty
import collections
from dataclasses import dataclass, field

from rich.align import Align
from rich.console import Console, Group
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich import box

from . import ui, config, progress_manager, settings

# Supported book extensions - must match content_parser.extract_content exactly.
START_MENU_EXTENSIONS = (".epub", ".pdf", ".txt", ".docx", ".html", ".rtf", ".md")

# Chinese + English lead every language list and are the out-of-the-box
# selection (kokoro splits English into American "a" / British "b").
PINNED_LANGS = {"edge": ["zh", "en"], "kokoro": ["z", "a"], "cosyvoice": ["zh", "en"]}


# CosyVoice2 reference-voice prompts (each is a cloned speaker + transcript).
# The chosen voice pins the language used to pick the prompt audio.
COSYVOICE_LANG_CODES = {
    "zh": "Chinese",
    "en": "English",
}
COSYVOICE_VOICES = {
    "zh": [("zh_cosy", "Chinese Female")],
    "en": [("en_cosy", "English Female")],
}

# The two built-in reference prompts are shown in the menu as en_cosy / zh_cosy
# (so they read as "the CosyVoice default", distinct from the cloned prompts
# like en_<name>). They still load the en.wav / zh.wav prompts under the hood,
# so these map the menu-facing name back to the prompt id the worker expects
# (identity for any other prompt id).
_COSYVOICE_PROMPT_FOR_NAME = {"en_cosy": "en", "zh_cosy": "zh"}


def _cosyvoice_name_for_prompt(prompt):
    """Menu-facing voice name for a prompt id (en -> en_cosy, zh -> zh_cosy)."""
    for name, p in _COSYVOICE_PROMPT_FOR_NAME.items():
        if p == prompt:
            return name
    return prompt


def _cosyvoice_prompt_for_name(name):
    """Prompt id for a menu-facing voice name (en_cosy -> en, identity otherwise)."""
    return _COSYVOICE_PROMPT_FOR_NAME.get(name, name)


def _cosyvoice_voices_by_lang() -> dict[str, list[tuple[str, str]]]:
    """Discover CosyVoice reference-voice prompts from the prompts dir.

    Each voice is a pair `<id>.wav` + `<id>.txt` in config.COSYVOICE_PROMPT_DIR.
    The language is the part of the id before the first "_" (or the whole id);
    unknown langs default to zh. Falls back to the built-in zh/en prompts if the
    dir is unavailable.
    """
    result: dict[str, list[tuple[str, str]]] = {"zh": [], "en": []}
    default_labels = {"zh": "Chinese Female", "en": "English Female"}
    pdir = getattr(config, "COSYVOICE_PROMPT_DIR", None)
    if pdir and os.path.isdir(pdir):
        for wav in sorted(os.listdir(pdir)):
            if not wav.endswith(".wav"):
                continue
            vid = wav[:-4]
            lang = vid.split("_")[0] if "_" in vid else vid
            if lang not in result:
                lang = "zh"
            stem = vid.split("_", 1)[1] if "_" in vid else vid
            label = default_labels.get(vid, stem.replace("_", " ").title())
            result[lang].append((_cosyvoice_name_for_prompt(vid), label))
    for lang in ("zh", "en"):
        if not result[lang]:
            result[lang] = COSYVOICE_VOICES.get(lang, [])
    return result


# Kokoro language codes (see VOICES.md and kokoro pipeline LANG_CODES).
KOKORO_LANG_CODES = {
    "a": "American English",
    "b": "British English",
    "e": "Spanish",
    "f": "French",
    "h": "Hindi",
    "i": "Italian",
    "p": "Brazilian Portuguese",
    "j": "Japanese",
    "z": "Mandarin Chinese",
}

# Human-readable names for the ISO-639 codes Edge locales reduce to. Only used
# for display in the setup wizard; unknown codes simply show the code itself.
LANG_NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "az": "Azerbaijani",
    "bg": "Bulgarian", "bn": "Bengali", "bs": "Bosnian", "ca": "Catalan",
    "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian",
    "fa": "Persian", "fi": "Finnish", "fil": "Filipino", "fr": "French",
    "ga": "Irish", "gl": "Galician", "gu": "Gujarati", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian", "hy": "Armenian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese",
    "jv": "Javanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer",
    "kn": "Kannada", "ko": "Korean", "lo": "Lao", "lt": "Lithuanian",
    "lv": "Latvian", "mk": "Macedonian", "ml": "Malayalam", "mn": "Mongolian",
    "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese",
    "nb": "Norwegian", "ne": "Nepali", "nl": "Dutch", "pl": "Polish",
    "ps": "Pashto", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "so": "Somali",
    "sq": "Albanian", "sr": "Serbian", "su": "Sundanese", "sv": "Swedish",
    "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "uz": "Uzbek",
    "vi": "Vietnamese", "zh": "Chinese", "zu": "Zulu",
}

# Static Kokoro voice list per language code (Kokoro exposes no runtime list).
KOKORO_VOICES = {
    "a": [
        ("af_heart", "Female"), ("af_alloy", "Female"), ("af_aoede", "Female"),
        ("af_bella", "Female"), ("af_jessica", "Female"), ("af_kore", "Female"),
        ("af_nicole", "Female"), ("af_nova", "Female"), ("af_river", "Female"),
        ("af_sarah", "Female"), ("af_sky", "Female"),
        ("am_adam", "Male"), ("am_echo", "Male"), ("am_eric", "Male"),
        ("am_fenrir", "Male"), ("am_liam", "Male"), ("am_michael", "Male"),
        ("am_onyx", "Male"), ("am_puck", "Male"), ("am_santa", "Male"),
    ],
    "b": [
        ("bf_alice", "Female"), ("bf_emma", "Female"), ("bf_isabella", "Female"),
        ("bf_lily", "Female"), ("bm_daniel", "Male"), ("bm_fable", "Male"),
        ("bm_george", "Male"), ("bm_lewis", "Male"),
    ],
    "e": [("ef_dora", "Female"), ("em_alex", "Male"), ("em_santa", "Male")],
    "f": [("ff_siwis", "Female")],
    "h": [("hf_alpha", "Female"), ("hf_beta", "Female"), ("hm_omega", "Male"), ("hm_psi", "Male")],
    "i": [("if_sara", "Female"), ("im_nicola", "Male")],
    "p": [("pf_dora", "Female"), ("pm_alex", "Male"), ("pm_santa", "Male")],
    "j": [
        ("jf_alpha", "Female"), ("jf_gongitsune", "Female"), ("jf_nezumi", "Female"),
        ("jf_tebukuro", "Female"), ("jm_kumo", "Male"),
    ],
    "z": [
        ("zf_xiaobei", "Female"), ("zf_xiaoni", "Female"), ("zf_xiaoxiao", "Female"),
        ("zf_xiaoyi", "Female"), ("zm_yunjian", "Male"), ("zm_yunxi", "Male"),
        ("zm_yunxia", "Male"), ("zm_yunyang", "Male"),
    ],
}

# Fallback Edge voices (name, gender) used when the network fetch fails.
EDGE_FALLBACK_VOICES = [
    ("en-US-JennyNeural", "Female"), ("en-US-AriaNeural", "Female"),
    ("en-US-AnaNeural", "Female"), ("en-US-MichelleNeural", "Female"),
    ("en-US-ChristopherNeural", "Male"), ("en-US-GuyNeural", "Male"),
    ("en-US-EricNeural", "Male"), ("en-US-RogerNeural", "Male"),
    ("en-US-SteffanNeural", "Male"),
    ("en-GB-SoniaNeural", "Female"), ("en-GB-RyanNeural", "Male"),
    ("en-GB-ThomasNeural", "Male"), ("en-GB-MaisieNeural", "Female"),
    ("en-AU-NatashaNeural", "Female"), ("en-AU-WilliamNeural", "Male"),
    ("en-IN-NeerjaNeural", "Female"), ("en-CA-ClaraNeural", "Female"),
    ("fr-FR-DeniseNeural", "Female"), ("fr-FR-HenriNeural", "Male"),
    ("de-DE-KatjaNeural", "Female"), ("de-DE-ConradNeural", "Male"),
    ("es-ES-ElviraNeural", "Female"), ("es-MX-DaliaNeural", "Female"),
    ("ja-JP-NanamiNeural", "Female"), ("zh-CN-XiaoxiaoNeural", "Female"),
]


@dataclass
class MenuResult:
    """The user's choices from the start menu (None if cancelled)."""

    file_path: str
    tts_name: str
    voice: str | None
    lang: str | None
    speed: float


@dataclass
class Row:
    """A single row in the file browser list."""

    kind: str  # "recent" | "parent" | "dir" | "file"
    title: str
    path: str
    detail: str = ""


@dataclass
class LangPickerState:
    """State for the full-screen multi-select language popup.

    options holds (code, label) rows like the wizard's language list; the
    selection itself lives on MenuState (edge_sel / kokoro_sel) and is toggled
    live so the Language field reflects changes the moment the popup closes.
    """

    options: list[tuple[str, str]]
    cursor: int = 0


@dataclass
class MenuState:
    """All mutable state for the start menu."""

    current_dir: str
    models: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    selection_idx: int = 0
    filter_text: str = ""
    selected_file: str | None = None
    error_msg: str | None = None
    pane_focus: str = "files"  # "files" | "settings"
    model_idx: int = 0
    edge_voices: list[dict] = field(default_factory=list)
    edge_voice_idx: int = 0
    voice_filter: str = ""
    kokoro_voice_idx: int = 0
    cosyvoice_voice_idx: int = 0
    speed: float = 1.0
    field_cursor: int = 0
    default_dir: str = ""           # persisted default start folder ("" = unset)
    edge_sel: list[str] = field(default_factory=list)  # selected edge codes ([] = all)
    edge_langs: list[str] = field(default_factory=list)  # available edge language codes
    kokoro_sel: list[str] = field(default_factory=list)  # selected kokoro codes ([] = all)
    cosyvoice_sel: list[str] = field(default_factory=list)  # selected cosyvoice codes
    lang_picker: LangPickerState | None = None  # when set, the popup owns render/keys
    folder_picker: "FolderPickerState | None" = None  # ditto for the folder chooser
    status_msg: str | None = None   # transient feedback line (e.g. "Default folder set")


# ---------------------------------------------------------------------------
# Voice list helpers
# ---------------------------------------------------------------------------

def _edge_fallback_dicts() -> list[dict]:
    result = []
    for name, gender in EDGE_FALLBACK_VOICES:
        parts = name.split("-")
        locale = "-".join(parts[:2]) if len(parts) >= 2 else ""
        result.append({
            "ShortName": name, "Gender": gender, "Locale": locale, "FriendlyName": name,
        })
    return result


def _get_voice_index(state: MenuState) -> int:
    model = state.models[state.model_idx] if state.models else "none"
    if model == "edge":
        return state.edge_voice_idx
    if model == "kokoro":
        return state.kokoro_voice_idx
    if model == "cosyvoice":
        return state.cosyvoice_voice_idx
    return 0


def _set_voice_index(state: MenuState, new_idx: int) -> None:
    model = state.models[state.model_idx] if state.models else "none"
    if model == "edge":
        state.edge_voice_idx = new_idx
    elif model == "kokoro":
        state.kokoro_voice_idx = new_idx
    elif model == "cosyvoice":
        state.cosyvoice_voice_idx = new_idx


def _current_voice_list(state: MenuState):
    """Return ([(name, info)], current_index) for the active model, applying voice_filter.

    With multi-select the language filter is a union: for edge only voices whose
    locale is in edge_sel (all when empty); for kokoro the union of each selected
    code's voices (all codes when empty).
    """
    model = state.models[state.model_idx] if state.models else "none"
    flt = state.voice_filter.strip().lower()
    if model == "kokoro":
        codes = state.kokoro_sel or list(KOKORO_LANG_CODES)
        all_v = []
        for code in codes:
            all_v.extend((n, g) for n, g in KOKORO_VOICES.get(code, []))
    elif model == "edge":
        sel = set(state.edge_sel)
        voices = state.edge_voices
        if sel:
            voices = [v for v in voices
                      if (v.get("Locale", "") or "").split("-")[0].lower() in sel]
        all_v = [(v.get("ShortName", ""), v.get("Gender", "")) for v in voices]
    elif model == "cosyvoice":
        codes = state.cosyvoice_sel or list(COSYVOICE_LANG_CODES)
        all_v = []
        voices_by_lang = _cosyvoice_voices_by_lang()
        for code in codes:
            all_v.extend((n, g) for n, g in voices_by_lang.get(code, []))
    else:
        return [], 0
    filtered = [(n, g) for n, g in all_v if not flt or flt in n.lower()]
    return filtered, _get_voice_index(state)


def _selected_voice_name(state: MenuState) -> str:
    names, idx = _current_voice_list(state)
    if names and 0 <= idx < len(names):
        return names[idx][0]
    return "-"


def _seed_voice_by_name(state: MenuState, name: str) -> None:
    if not name:
        return
    names, _ = _current_voice_list(state)
    for i, (n, _g) in enumerate(names):
        if n == name:
            _set_voice_index(state, i)
            return


def _seed_default_voice(state: MenuState) -> None:
    model = state.models[state.model_idx] if state.models else "none"
    if model == "edge":
        _seed_voice_by_name(state, config.TTS_VOICES.get("edge"))
    elif model == "kokoro":
        _seed_voice_by_name(state, config.TTS_VOICES.get("kokoro"))
    elif model == "cosyvoice":
        _seed_voice_by_name(state, _cosyvoice_name_for_prompt(config.TTS_VOICES.get("cosyvoice")))


# ---------------------------------------------------------------------------
# Settings pane field helpers
# ---------------------------------------------------------------------------

def _settings_fields(state: MenuState) -> list[str]:
    model = state.models[state.model_idx] if state.models else "none"
    if model in ("edge", "kokoro", "cosyvoice"):
        return ["folder", "model", "lang", "voice", "speed"]
    return ["folder", "model"]


def _field_idx(state: MenuState, name: str) -> int | None:
    fields = _settings_fields(state)
    return fields.index(name) if name in fields else None


def _voice_field_idx(state: MenuState) -> int | None:
    return _field_idx(state, "voice")


def _launch_field_idx(state: MenuState) -> int | None:
    """Index of the bottom "Start Reading" action — a virtual row just below
    the settings fields, so Launch is always one position away."""
    return len(_settings_fields(state))


def _folder_field_idx(state: MenuState) -> int | None:
    return _field_idx(state, "folder")


def _selected_langs(state: MenuState) -> list[str]:
    """The active model's selected language codes ([] = all languages)."""
    model = state.models[state.model_idx] if state.models else "none"
    if model == "kokoro":
        return list(state.kokoro_sel)
    if model == "cosyvoice":
        return list(state.cosyvoice_sel)
    if model == "edge":
        return list(state.edge_sel)
    return []


def _set_selected_langs(state: MenuState, codes: list[str]) -> None:
    """Replace the active model's selected languages and reset the voice pick."""
    model = state.models[state.model_idx] if state.models else "none"
    if model == "kokoro":
        state.kokoro_sel = [c for c in codes if c in KOKORO_LANG_CODES]
    elif model == "cosyvoice":
        state.cosyvoice_sel = [c for c in codes if c in COSYVOICE_LANG_CODES]
    elif model == "edge":
        state.edge_sel = [c for c in codes if c in state.edge_langs]
    state.voice_filter = ""
    _set_voice_index(state, 0)


def _lang_summary(state: MenuState) -> str:
    """Human-readable summary of the active model's language selection."""
    model = state.models[state.model_idx] if state.models else "none"
    codes = _selected_langs(state)
    if not codes:
        return "All languages"
    if model == "kokoro":
        names = [f"{KOKORO_LANG_CODES.get(c, c)} ({c})" for c in codes]
    elif model == "cosyvoice":
        names = [f"{COSYVOICE_LANG_CODES.get(c, c)} ({c})" for c in codes]
    elif model == "edge":
        names = [f"{LANG_NAMES.get(c, c)} ({c})" for c in codes]
    else:
        names = list(codes)
    return " + ".join(names)


def _persist_defaults(state: MenuState) -> None:
    """Save the default start folder + selected language(s) to settings.json."""
    prefs = settings.load_settings()
    prefs["default_start_dir"] = state.default_dir
    prefs["default_language"] = _selected_langs(state)
    settings.save_settings(prefs)


def _parse_lang_value(value) -> list[str]:
    """Normalize a persisted default_language (list or legacy string) to a list."""
    if isinstance(value, list):
        return [str(c) for c in value]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _set_default_dir(state: MenuState, path: str) -> None:
    """Make `path` the persisted default start folder and browse there."""
    state.default_dir = os.path.abspath(path)
    _persist_defaults(state)
    cd(state, state.default_dir)
    state.status_msg = f"Default folder set: {state.default_dir}"


def _open_folder_picker(state: MenuState) -> None:
    """Open the browsable folder chooser on the best-known starting point."""
    start = state.default_dir if os.path.isdir(state.default_dir or "") else state.current_dir
    state.folder_picker = new_folder_picker(start)


def _handle_folder_popup(state: MenuState, key) -> str:
    """Handle a key/mouse event while the folder chooser popup is open."""
    picker = state.folder_picker
    height = ui.get_terminal_size()[1]
    if isinstance(key, tuple) and key[0] == "mouse":
        button, _x, y = key[1]
        if button in (64, 65):
            _folder_wheel(picker, -3 if button == 64 else 3)
            return "continue"
        if button != 0:
            return "continue"
        top = _folder_panel_geometry(picker, height)[0]
        # panel top border + padding + header + blank line
        outcome = _folder_click(picker, y, top + 4, height)
    else:
        outcome = _handle_folder_key(picker, key, height)
    if outcome == "choose":
        state.folder_picker = None
        _set_default_dir(state, picker.cur_dir)
    elif outcome == "cancel":
        state.folder_picker = None
    return "continue"



# ---------------------------------------------------------------------------
# File browser
# ---------------------------------------------------------------------------

def _is_dir(entry) -> bool:
    try:
        return entry.is_dir()
    except OSError:
        return False


def build_file_rows(state: MenuState) -> None:
    """Rebuild state.rows: recent books, then '..', then dirs, then book files."""
    flt = state.filter_text.strip().lower()
    rows: list[Row] = []

    try:
        recent = progress_manager.get_recent_books(limit=5)
    except Exception:
        recent = []
    for rb in recent:
        title = rb.get("title", "")
        if flt and flt not in title.lower():
            continue
        pct = int(rb.get("percentage", 0))
        rows.append(Row("recent", title, os.path.abspath(rb.get("path", "")), f"{pct}%"))

    parent = os.path.dirname(state.current_dir)
    if parent != state.current_dir:
        rows.append(Row("parent", "..", parent))

    entries = []
    state.error_msg = None
    try:
        with os.scandir(state.current_dir) as it:
            entries = list(it)
    except OSError as e:
        state.error_msg = f"Error reading directory: {e}"

    entries.sort(key=lambda e: (not _is_dir(e), e.name.lower()))
    for entry in entries:
        try:
            if _is_dir(entry):
                if flt and flt not in entry.name.lower():
                    continue
                rows.append(Row("dir", entry.name + "/", os.path.join(state.current_dir, entry.name)))
            else:
                ext = os.path.splitext(entry.name)[1].lower()
                if ext not in START_MENU_EXTENSIONS:
                    continue
                if flt and flt not in entry.name.lower():
                    continue
                rows.append(Row("file", entry.name, os.path.abspath(entry.path)))
        except OSError:
            continue

    state.rows = rows
    if rows:
        state.selection_idx = max(0, min(state.selection_idx, len(rows) - 1))
    else:
        state.selection_idx = 0


def cd(state: MenuState, new_dir: str) -> None:
    new_dir = os.path.abspath(new_dir)
    if not os.path.isdir(new_dir):
        return
    state.current_dir = new_dir
    state.filter_text = ""
    state.selection_idx = 0
    state.error_msg = None
    state.status_msg = None
    build_file_rows(state)


def _move_selection(state: MenuState, delta: int) -> None:
    if not state.rows:
        return
    state.selection_idx = max(0, min(len(state.rows) - 1, state.selection_idx + delta))


# ---------------------------------------------------------------------------
# Folder chooser (shared by the setup wizard and the Folder field)
# ---------------------------------------------------------------------------

FOLDER_ROWS = 10  # max directory rows shown at once


@dataclass
class FolderPickerState:
    """A browsable directory chooser with an optional typed-path mode.

    Row 0 of the list is always the "use this folder" action; rows 1.. are
    ".." plus the subdirectories of `cur_dir` (filtered by `filter_text`).
    """

    cur_dir: str
    cursor: int = 0        # 0 = "use this folder", 1.. = entries[cursor - 1]
    filter_text: str = ""
    entries: list[tuple[str, str]] = field(default_factory=list)  # (label, path)
    typing: bool = False   # True while the path text field has focus
    draft: str = ""        # path text being typed
    caret: int = 0         # caret position inside draft
    error: str | None = None
    note: str | None = None  # dim info line (completion matches, etc.)


def _subdirs(path: str, flt: str) -> list[tuple[str, str]]:
    """(label, abspath) for '..' plus the visible subdirectories of `path`."""
    rows: list[tuple[str, str]] = []
    parent = os.path.dirname(path)
    if parent and parent != path:
        rows.append(("..", parent))
    names = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not _is_dir(entry):
                    continue
                # Hidden folders stay out of the way until they are searched for.
                if entry.name.startswith(".") and not flt.startswith("."):
                    continue
                if flt and flt.lower() not in entry.name.lower():
                    continue
                names.append(entry.name)
    except OSError:
        return rows
    names.sort(key=str.lower)
    rows.extend((name + "/", os.path.join(path, name)) for name in names)
    return rows


def _folder_refresh(picker: FolderPickerState) -> None:
    picker.entries = _subdirs(picker.cur_dir, picker.filter_text)
    picker.cursor = max(0, min(picker.cursor, len(picker.entries)))


def _folder_cd(picker: FolderPickerState, path: str) -> None:
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        picker.error = f"Not a folder: {path}"
        return
    picker.cur_dir = path
    picker.filter_text = ""
    picker.cursor = 0
    picker.error = None
    picker.note = None
    _folder_refresh(picker)


def new_folder_picker(start_dir: str) -> FolderPickerState:
    """Build a chooser rooted at `start_dir` (falling back to $HOME)."""
    start = os.path.abspath(start_dir) if start_dir else ""
    if not os.path.isdir(start):
        start = os.path.expanduser("~")
    picker = FolderPickerState(cur_dir=start)
    _folder_refresh(picker)
    return picker


def _folder_scroll(picker: FolderPickerState, height: int) -> tuple[int, int]:
    """Return (first visible entry index, number of entry rows rendered)."""
    total = len(picker.entries)
    visible = max(1, min(FOLDER_ROWS, max(1, height - 14)))
    ent = max(0, picker.cursor - 1)
    start = max(0, ent - visible + 1) if ent >= visible else 0
    start = min(start, max(0, total - visible))
    return start, max(0, min(start + visible, total) - start)


def _folder_body_height(picker: FolderPickerState, height: int) -> int:
    """Rows `_folder_body` will produce — geometry and mouse hits depend on it."""
    extra = (1 if picker.note else 0) + (1 if picker.error else 0)
    if picker.typing:
        return 3 + extra
    _start, count = _folder_scroll(picker, height)
    return 2 + 1 + count + 2 + extra


def _folder_panel_geometry(picker: FolderPickerState, height: int) -> tuple[int, int, int]:
    """(panel top row, panel height, content rows) for a folder-chooser panel.

    Assumes two header lines (title + blank) above the body, which both the
    wizard's step 1 and the standalone popup render.
    """
    content = 2 + _folder_body_height(picker, height)
    panel_h = min(content + 4, max(5, height - 2))  # borders + vertical padding
    top = max(1, (height - panel_h) // 2 + 1)
    return top, panel_h, content


def _folder_body(picker: FolderPickerState, inner: int, height: int) -> list[Text]:
    """Render the chooser body (path line / directory list, hints, errors)."""
    lines: list[Text] = []
    if picker.typing:
        row = Text("  Path: ", style="cyan")
        vis = max(10, inner - 10)
        before = picker.draft[:picker.caret][-vis:]
        at = picker.draft[picker.caret:picker.caret + 1] or " "
        after = picker.draft[picker.caret + 1:][:max(0, vis - len(before))]
        row.append(before, style="bold white")
        row.append(at, style="reverse bold cyan")
        row.append(after, style="bold white")
        lines.append(row)
        lines.append(Text(""))
        lines.append(Text(
            _fit_tail("  Tab completes · Enter opens it · Esc back to the list", inner),
            style="dim",
        ))
    else:
        lines.append(Text("  In: " + _fit_tail(picker.cur_dir, inner - 6), style="dim"))
        lines.append(Text(
            "  Find: " + _fit_tail(picker.filter_text, inner - 8) if picker.filter_text else "",
            style="yellow",
        ))
        use_row = Text("  ✓ " if picker.cursor == 0 else "    ")
        use_row.append(
            _fit_tail("Use this folder", inner - 4),
            style="reverse bold green" if picker.cursor == 0 else "bold green",
        )
        lines.append(use_row)
        start, count = _folder_scroll(picker, height)
        for i in range(start, start + count):
            label, _path = picker.entries[i]
            is_cursor = (i + 1) == picker.cursor
            row = Text("  > " if is_cursor else "    ")
            row.append(
                _fit_tail(label, inner - 4),
                style="reverse bold cyan" if is_cursor else "",
            )
            lines.append(row)
        lines.append(Text(""))
        lines.append(Text(
            _fit_tail("  ↑/↓ move · Enter open · ← up · type to find · Tab = path", inner),
            style="dim",
        ))
    if picker.note:
        lines.append(Text(_fit_tail(f"  {picker.note}", inner), style="dim"))
    if picker.error:
        lines.append(Text(_fit_tail(f"  {picker.error}", inner), style="bold red"))
    return lines


def _expand_path(text: str) -> str:
    return os.path.expanduser(os.path.expandvars(text.strip()))


def _folder_start_typing(picker: FolderPickerState, seed: str = "") -> None:
    """Switch to path entry, pre-filled so the user edits instead of retypes."""
    if seed == "~":
        picker.draft = "~/"
    elif seed == "/":
        picker.draft = "/"
    else:
        picker.draft = picker.cur_dir.rstrip(os.sep) + os.sep
    picker.caret = len(picker.draft)
    picker.typing = True
    picker.error = None
    picker.note = None


def _folder_complete(picker: FolderPickerState) -> None:
    """Tab-completion: extend the draft to the longest matching folder name."""
    raw = picker.draft
    expanded = _expand_path(raw)
    if raw.rstrip().endswith(os.sep):
        head, stem = expanded or os.sep, ""
    else:
        head, stem = os.path.dirname(expanded) or ".", os.path.basename(expanded)
    try:
        with os.scandir(head) as it:
            names = sorted(
                (e.name for e in it
                 if _is_dir(e)
                 and e.name.lower().startswith(stem.lower())
                 and (stem.startswith(".") or not e.name.startswith("."))),
                key=str.lower,
            )
    except OSError:
        names = []
    if not names:
        picker.note = None
        picker.error = "No matching folder"
        return
    if len(names) == 1:
        completed = os.path.join(head, names[0]) + os.sep
        picker.note = None
    else:
        completed = os.path.join(head, os.path.commonprefix(names))
        shown = ", ".join(names[:5]) + ("…" if len(names) > 5 else "")
        picker.note = f"{len(names)} matches: {shown}"
    picker.draft = completed
    picker.caret = len(completed)
    picker.error = None


def _handle_folder_typing(picker: FolderPickerState, key, char, is_char: bool) -> str:
    """Line editing for the typed-path mode. Returns 'continue' or 'choose'."""
    draft, caret = picker.draft, picker.caret
    if key == "esc":
        picker.typing = False
        picker.error = None
        picker.note = None
    elif key == "enter":
        if not draft.strip():
            picker.typing = False
        else:
            target = _expand_path(draft)
            if os.path.isdir(target):
                _folder_cd(picker, target)
                picker.typing = False
            else:
                picker.error = f"Not a folder: {target}"
    elif key == "tab":
        _folder_complete(picker)
    elif key == "backspace":
        if caret > 0:
            picker.draft = draft[:caret - 1] + draft[caret:]
            picker.caret = caret - 1
        picker.error = None
    elif key == "left":
        picker.caret = max(0, caret - 1)
    elif key == "right":
        picker.caret = min(len(draft), caret + 1)
    elif key in ("home", "ctrl_a"):
        picker.caret = 0
    elif key in ("end", "ctrl_e"):
        picker.caret = len(draft)
    elif key == "ctrl_u":
        picker.draft = draft[caret:]
        picker.caret = 0
        picker.error = None
    elif key == "ctrl_w":
        head = draft[:caret].rstrip(os.sep)
        cut = head.rfind(os.sep)
        head = head[:cut + 1] if cut >= 0 else ""
        picker.draft = head + draft[caret:]
        picker.caret = len(head)
        picker.error = None
    elif is_char:
        picker.draft = draft[:caret] + char + draft[caret:]
        picker.caret = caret + len(char)
        picker.error = None
        picker.note = None
    return "continue"


def _handle_folder_key(picker: FolderPickerState, key, height: int) -> str:
    """One key for the chooser. Returns 'continue', 'choose' or 'cancel'."""
    is_char = isinstance(key, tuple) and key[0] == "char"
    char = key[1] if is_char else None

    if picker.typing:
        return _handle_folder_typing(picker, key, char, is_char)

    last = len(picker.entries)  # cursor 0 = "use this folder", 1..last = entries
    _start, count = _folder_scroll(picker, height)
    page = max(1, count)
    if key == "up":
        picker.cursor = max(0, picker.cursor - 1)
    elif key == "down":
        picker.cursor = min(last, picker.cursor + 1)
    elif key == "page_up":
        picker.cursor = max(0, picker.cursor - page)
    elif key == "page_down":
        picker.cursor = min(last, picker.cursor + page)
    elif key == "home":
        picker.cursor = 0
    elif key == "end":
        picker.cursor = last
    elif key in ("enter", "right"):
        if picker.cursor == 0:
            if key == "enter":
                return "choose"
        else:
            _folder_cd(picker, picker.entries[picker.cursor - 1][1])
    elif key in ("left", "backspace"):
        if picker.filter_text:
            picker.filter_text = picker.filter_text[:-1]
            picker.cursor = 0
            _folder_refresh(picker)
        else:
            _folder_cd(picker, os.path.dirname(picker.cur_dir))
    elif key == "tab":
        _folder_start_typing(picker)
    elif key == "esc":
        if picker.filter_text:
            picker.filter_text = ""
            picker.cursor = 0
            _folder_refresh(picker)
        else:
            return "cancel"
    elif is_char:
        if char in ("/", "~") and not picker.filter_text:
            _folder_start_typing(picker, char)
        else:
            picker.filter_text += char
            picker.cursor = 0
            picker.error = None
            _folder_refresh(picker)
    return "continue"


def _folder_wheel(picker: FolderPickerState, delta: int) -> None:
    if not picker.typing:
        picker.cursor = max(0, min(len(picker.entries), picker.cursor + delta))


def _folder_click(picker: FolderPickerState, y: int, first_body_row: int, height: int) -> str:
    """Map a click at screen row `y` onto the list. Returns 'continue'/'choose'."""
    if picker.typing:
        return "continue"
    start, count = _folder_scroll(picker, height)
    row = y - (first_body_row + 2)  # skip the "In:" and filter lines
    if row == 0:
        picker.cursor = 0
        return "choose"
    idx = row - 1
    if 0 <= idx < count:
        picker.cursor = start + idx + 1
        _folder_cd(picker, picker.entries[start + idx][1])
    return "continue"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_left_pane(state: MenuState, pane_height: int):
    pane_focused = state.pane_focus == "files"
    border = "cyan" if pane_focused else "blue"

    table = Table(box=None, show_header=False, padding=0, expand=True)
    table.add_column("Selection", width=3)
    table.add_column("Name", ratio=1, no_wrap=True, overflow="ellipsis")
    table.add_column("Detail", justify="right")

    search_label = f"Search: {state.filter_text}" if state.filter_text else "Search: (type to search)"
    table.add_row("", Text(search_label, style="dim"), "")
    if state.selected_file:
        table.add_row("", Text(f"Selected: {os.path.basename(state.selected_file)}", style="bold green"), "")
    if state.error_msg:
        table.add_row("", Text(state.error_msg, style="yellow"), "")
    if state.status_msg:
        table.add_row("", Text(state.status_msg, style="green"), "")

    header_rows = (1 + (1 if state.selected_file else 0)
                   + (1 if state.error_msg else 0) + (1 if state.status_msg else 0))
    max_visible = max(1, pane_height - 4 - header_rows)
    rows = state.rows
    sel = state.selection_idx
    start = max(0, sel - max_visible + 1) if sel >= max_visible else 0
    start = min(start, max(0, len(rows) - max_visible))

    for i in range(start, min(start + max_visible, len(rows))):
        row = rows[i]
        is_selected = i == sel
        if is_selected:
            style = "reverse bold cyan"
            prefix = ">"
        else:
            style = None
            prefix = " "
        table.add_row(
            prefix,
            Text(row.title, no_wrap=True, overflow="ellipsis"),
            row.detail,
            style=style,
        )

    title_dir = os.path.basename(state.current_dir) or state.current_dir
    return Panel(
        table,
        title=f"[bold cyan]Books - {title_dir}[/bold cyan]",
        border_style=border,
        box=box.ROUNDED,
        padding=(1, 1),
        expand=True,
    )


def render_right_pane(state: MenuState, pane_height: int):
    model = state.models[state.model_idx] if state.models else "none"
    fields = _settings_fields(state)
    cursor = state.field_cursor
    focused = state.pane_focus == "settings"
    vf_idx = _voice_field_idx(state)
    lf_idx = _launch_field_idx(state) or 0
    voice_focused = focused and cursor == vf_idx

    field_table = Table(box=None, show_header=False, padding=0, expand=True)
    field_table.add_column("Field", width=13)
    field_table.add_column("Value", ratio=1, no_wrap=True, overflow="ellipsis")

    def _add(label, value, idx, style=None):
        row_style = None
        if focused and idx == cursor and idx != vf_idx:
            row_style = "reverse bold cyan"
        elif style:
            row_style = style
        field_table.add_row(label, str(value), style=row_style)

    folder_idx = _folder_field_idx(state) or 0
    folder_val = state.default_dir or "not set"
    if focused and cursor == folder_idx:
        folder_val += " [Enter]"
    _add("Folder", folder_val, folder_idx)

    _add("Model", model, fields.index("model"))
    if model in ("kokoro", "edge", "cosyvoice"):
        lang_hint = " [Space]" if (focused and state.field_cursor == fields.index("lang")) else ""
        _add("Language", _lang_summary(state) + lang_hint, fields.index("lang"))
    if vf_idx is not None:
        _add("Voice", _selected_voice_name(state), vf_idx)
    if model in ("edge", "kokoro", "cosyvoice"):
        _add("Speed", f"{state.speed:.1f}x", fields.index("speed"))

    body_parts = [field_table]

    if model != "none":
        names, sel = _current_voice_list(state)
        voice_table = Table(box=None, show_header=False, padding=0, expand=True)
        voice_table.add_column("Sel", width=3)
        voice_table.add_column("Voice", ratio=1, no_wrap=True, overflow="ellipsis")
        voice_table.add_column("Info", justify="right")

        flt_label = f"{model} Voices{(' [' + state.voice_filter + ']') if state.voice_filter else ''}"
        if voice_focused:
            flt_label += " (type to filter)"
        voice_table.add_row("", Text(flt_label, style="dim"), "")

        max_visible = max(1, pane_height - len(fields) - 6)  # matches the mouse mapper
        total = len(names)
        start = max(0, sel - max_visible + 1) if sel >= max_visible else 0
        start = min(start, max(0, total - max_visible))
        for i in range(start, min(start + max_visible, total)):
            name, info = names[i]
            is_sel = i == sel
            if is_sel:
                style = "reverse bold cyan" if voice_focused else "bold cyan"
                prefix = ">"
            else:
                style = None
                prefix = " "
            voice_table.add_row(prefix, Text(name, no_wrap=True, overflow="ellipsis"), info, style=style)
        body_parts.append(voice_table)

    body = Group(*body_parts) if len(body_parts) > 1 else body_parts[0]

    # Full-width primary action button pinned to the bottom of the pane.
    action_table = Table(box=None, show_header=False, padding=0, expand=True)
    action_table.add_column("Action", ratio=1)
    if state.selected_file:
        btn_text = "  ▶  Start Reading  "
        if focused and cursor == lf_idx:
            action_table.add_row(Text(btn_text, justify="center", style="reverse bold green"))
        else:
            action_table.add_row(Text(btn_text, justify="center", style="bold green"))
    else:
        action_table.add_row(Text("  Select a book first  ", justify="center", style="dim"))

    layout = Layout()
    layout.split_column(
        Layout(name="body", ratio=1),
        Layout(name="action", size=1),
    )
    layout["body"].update(body)
    layout["action"].update(action_table)
    return Panel(
        layout,
        title="[bold cyan]TTS Settings[/bold cyan]",
        border_style="cyan" if focused else "blue",
        box=box.ROUNDED,
        padding=(1, 1),
        expand=True,
    )


def render_footer(state: MenuState):
    if state.pane_focus == "settings" and state.field_cursor == (_folder_field_idx(state) or 0):
        hint = "Enter/Space = browse for your books folder · ↑/↓ other settings · Tab files"
    elif state.pane_focus == "files":
        hint = "↑/↓ move · Enter open/select · type to search · s = set default folder · Backspace up · Esc back/quit · q quit"
    else:
        hint = "↑/↓ move · Enter Start Reading · Space on Language = open picker · ←/→ cycle model · Esc back · Tab files · q quit"
    return Panel(Text(hint, style="dim"), border_style="blue", box=box.ROUNDED)


def render_menu(state: MenuState, width: int, height: int) -> str:
    """Render the full-screen menu to a string frame."""
    if state.lang_picker is not None:
        return render_lang_picker(state, width, height)
    if state.folder_picker is not None:
        return render_folder_panel(
            state.folder_picker, width, height,
            title="Default folder",
            header="Where do you keep your books?",
            hint=f"Current default: {state.default_dir or 'not set'}",
        )
    main_h = max(1, height - 3)
    layout = Layout()
    layout.split_column(
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="left", ratio=2),
        Layout(name="right", ratio=1),
    )
    layout["left"].update(render_left_pane(state, main_h))
    layout["right"].update(render_right_pane(state, main_h))
    layout["footer"].update(render_footer(state))

    temp_console = Console(width=width, height=height, force_terminal=True)
    with temp_console.capture() as capture:
        temp_console.print(layout, overflow="crop")
    frame = capture.get()
    lines = frame.split("\n")
    if len(lines) > height:
        lines = lines[:height]
    return "\033[?25l\033[H" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

CTRL_KEYS = {0x01: "ctrl_a", 0x05: "ctrl_e", 0x15: "ctrl_u", 0x17: "ctrl_w"}


def _drain_keys(buf: bytearray) -> list:
    """Consume complete key sequences from the front of the buffer."""
    keys = []
    i = 0
    while i < len(buf):
        b = buf[i]
        if b == 0x1B:
            if i + 1 >= len(buf):
                break  # wait for the next byte
            nxt = buf[i + 1]
            if nxt == ord("["):
                j = i + 2
                while j < len(buf) and not (0x40 <= buf[j] <= 0x7E):
                    j += 1
                if j >= len(buf):
                    break  # incomplete CSI sequence
                seq = bytes(buf[i:j + 1]).decode("ascii", errors="ignore")
                if seq.startswith("\x1b[<") and seq.endswith(("M", "m")):
                    # SGR mouse sequence: \x1b[<button;x;yM (press) / m (release)
                    try:
                        parts = seq[3:-1].split(";")
                        if len(parts) >= 3:
                            button = int(parts[0])
                            x = int(parts[1])
                            y = int(parts[2])
                            if seq.endswith("M"):
                                keys.append(("mouse", (button, x, y)))
                    except ValueError:
                        pass
                elif seq == "\x1b[A":
                    keys.append("up")
                elif seq == "\x1b[B":
                    keys.append("down")
                elif seq == "\x1b[C":
                    keys.append("right")
                elif seq == "\x1b[D":
                    keys.append("left")
                elif seq == "\x1b[5~":
                    keys.append("page_up")
                elif seq == "\x1b[6~":
                    keys.append("page_down")
                elif seq in ("\x1b[1~", "\x1b[H"):
                    keys.append("home")
                elif seq in ("\x1b[4~", "\x1b[F"):
                    keys.append("end")
                elif seq == "\x1b[3~":
                    keys.append("backspace")
                i = j + 1
                continue
            elif nxt == ord("O"):
                if i + 2 >= len(buf):
                    break
                ch = chr(buf[i + 2])
                if ch == "A":
                    keys.append("up")
                elif ch == "B":
                    keys.append("down")
                elif ch == "C":
                    keys.append("right")
                elif ch == "D":
                    keys.append("left")
                elif ch == "H":
                    keys.append("home")
                elif ch == "F":
                    keys.append("end")
                i += 3
                continue
            else:
                keys.append("esc")
                i += 1
                continue
        elif b in (0x0D, 0x0A):
            keys.append("enter")
            i += 1
        elif b == 0x09:
            keys.append("tab")
            i += 1
        elif b in (0x7F, 0x08):
            keys.append("backspace")
            i += 1
        elif b == 0x03:
            keys.append("ctrl_c")
            i += 1
        elif b in CTRL_KEYS:
            # Line-editing keys for the path field (Ctrl+A/E/U/W).
            keys.append(CTRL_KEYS[b])
            i += 1
        elif b < 0x20:
            i += 1  # ignore other control characters
        else:
            if b < 0x80:
                length = 1
            elif b < 0xE0:
                length = 2
            elif b < 0xF0:
                length = 3
            else:
                length = 4
            if i + length > len(buf):
                break
            try:
                ch = bytes(buf[i:i + length]).decode("utf-8")
                keys.append(("char", ch))
            except UnicodeDecodeError:
                pass
            i += length
    if i:
        del buf[:i]
    return keys


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

def _move_voice(state: MenuState, delta: int) -> None:
    names, idx = _current_voice_list(state)
    if not names:
        return
    _set_voice_index(state, max(0, min(len(names) - 1, idx + delta)))


def _cycle_model(state: MenuState, delta: int) -> None:
    if len(state.models) < 2:
        return
    n = len(state.models)
    state.model_idx = (state.model_idx + delta) % n
    state.voice_filter = ""
    _set_voice_index(state, 0)
    _seed_default_voice(state)
    state.field_cursor = min(state.field_cursor, len(_settings_fields(state)))


def _adjust_field(state: MenuState, delta: int) -> None:
    fields = _settings_fields(state)
    if state.field_cursor == fields.index("model"):
        _cycle_model(state, delta)
    elif "speed" in fields and state.field_cursor == fields.index("speed"):
        state.speed = round(min(2.0, max(0.5, state.speed + 0.1 * delta)), 1)


def _open_lang_picker(state: MenuState) -> None:
    """Open the full-screen language multi-select popup for the active model."""
    options = _wizard_lang_options(state)
    if not options:
        return
    sel = set(_selected_langs(state))
    cursor = 0
    for i, (code, _label) in enumerate(options):
        if code and code in sel:
            cursor = i
            break
    state.lang_picker = LangPickerState(options=options, cursor=cursor)


def _handle_mouse(state: MenuState, button: int, x: int, y: int) -> str:
    """Handle a mouse event at (x, y). Returns 'continue' or 'launch'."""
    if button in (64, 65):
        # Wheel up / down: scroll the focused list.
        delta = -3 if button == 64 else 3
        if state.pane_focus == "files":
            _move_selection(state, delta)
        else:
            vf_idx = _voice_field_idx(state)
            if vf_idx is not None and state.field_cursor == vf_idx:
                _move_voice(state, delta)
            else:
                fields = _settings_fields(state)
                state.field_cursor = max(0, min(len(fields), state.field_cursor + delta))
        return "continue"
    if button != 0:
        return "continue"  # only left-click

    width, height = ui.get_terminal_size()
    if y > height - 3:
        return "continue"  # footer area
    left_width = int(width * 2 / 3)

    if x <= left_width:
        # ---- Files pane ----
        state.pane_focus = "files"
        sel_extra = 1 if state.selected_file else 0
        err_extra = 1 if state.error_msg else 0
        status_extra = 1 if state.status_msg else 0
        header_rows = 1 + sel_extra + err_extra + status_extra
        max_visible = max(1, (height - 3) - 4 - header_rows)
        sel = state.selection_idx
        start = max(0, sel - max_visible + 1) if sel >= max_visible else 0
        start = min(start, max(0, len(state.rows) - max_visible))
        row_idx = start + (y - (3 + header_rows))
        if 0 <= row_idx < len(state.rows):
            state.selection_idx = row_idx
            row = state.rows[row_idx]
            if row.kind in ("dir", "parent"):
                cd(state, row.path)
            else:
                state.selected_file = row.path
                state.pane_focus = "settings"
                state.field_cursor = _launch_field_idx(state) or 0
        return "continue"

    # ---- Settings pane ----
    state.pane_focus = "settings"
    fields = _settings_fields(state)
    num_fields = len(fields)
    vf_idx = _voice_field_idx(state)
    lf_idx = _launch_field_idx(state) or 0
    field_row = y - 3
    if 0 <= field_row <= num_fields:
        state.field_cursor = field_row
        if field_row == lf_idx and state.selected_file:
            return "launch"
        lang_idx = fields.index("lang") if "lang" in fields else None
        if lang_idx is not None and field_row == lang_idx:
            _open_lang_picker(state)
        elif field_row == (_folder_field_idx(state) or 0) and "folder" in fields:
            _open_folder_picker(state)
        return "continue"
    # The full-width action button is pinned to the bottom of the settings pane.
    if y == height - 5 and state.selected_file:
        return "launch"
    if vf_idx is not None:
        names, sel = _current_voice_list(state)
        max_visible = max(1, (height - 3) - num_fields - 6)
        start = max(0, sel - max_visible + 1) if sel >= max_visible else 0
        start = min(start, max(0, len(names) - max_visible))
        voice_row = y - (3 + num_fields + 1)
        if 0 <= voice_row < max_visible:
            idx = start + voice_row
            if 0 <= idx < len(names):
                _set_voice_index(state, idx)
                state.field_cursor = vf_idx
    return "continue"


def handle_key(state: MenuState, key) -> str:
    """Handle one key token. Returns 'continue', 'launch', or 'quit'."""
    if key == "ctrl_c":
        return "quit"
    if state.lang_picker is not None:
        return _handle_picker(state, key)
    if state.folder_picker is not None:
        return _handle_folder_popup(state, key)
    if isinstance(key, tuple) and key[0] == "mouse":
        return _handle_mouse(state, key[1][0], key[1][1], key[1][2])

    is_char = isinstance(key, tuple)
    char = key[1] if is_char else None

    if char == "q":
        quitting = True
        if state.pane_focus == "files" and state.filter_text:
            quitting = False
        elif state.pane_focus == "settings":
            vf_idx = _voice_field_idx(state)
            if state.field_cursor == vf_idx and state.voice_filter:
                quitting = False
        if quitting:
            return "quit"

    if state.pane_focus == "files":
        if key == "up" or char == "k":
            _move_selection(state, -1)
        elif key == "down" or char == "j":
            _move_selection(state, 1)
        elif key == "page_up":
            _move_selection(state, -10)
        elif key == "page_down":
            _move_selection(state, 10)
        elif key == "home":
            _move_selection(state, -10**9)
        elif key == "end":
            _move_selection(state, 10**9)
        elif key == "enter":
            if state.rows:
                row = state.rows[state.selection_idx]
                if row.kind in ("dir", "parent"):
                    cd(state, row.path)
                else:
                    state.selected_file = row.path
                    state.pane_focus = "settings"
                    state.field_cursor = _launch_field_idx(state) or 0
        elif key == "backspace":
            parent = os.path.dirname(state.current_dir)
            if parent != state.current_dir:
                cd(state, parent)
        elif key == "esc":
            if state.filter_text:
                state.filter_text = ""
                build_file_rows(state)
                state.selection_idx = 0
            else:
                parent = os.path.dirname(state.current_dir)
                if parent != state.current_dir:
                    cd(state, parent)
                else:
                    return "quit"
        elif key == "tab" or key == "right" or char == "l":
            state.pane_focus = "settings"
        elif char == "s":
            # Make the folder we're browsing the default start folder.
            state.default_dir = state.current_dir
            state.status_msg = f"Default folder set: {state.current_dir}"
            _persist_defaults(state)
        elif is_char:
            state.filter_text += char
            build_file_rows(state)
            state.selection_idx = 0
        return "continue"

    # ---- settings pane ----
    fields = _settings_fields(state)
    vf_idx = _voice_field_idx(state)
    model = state.models[state.model_idx] if state.models else "none"
    folder_idx = _folder_field_idx(state)
    model_idx = fields.index("model")
    lang_idx = fields.index("lang") if "lang" in fields else None
    speed_idx = fields.index("speed") if "speed" in fields else None

    # On the Voice field, Up/Down cycles the voice only while a filter is
    # narrowing the list (typing to search). With no filter the list is far
    # too long to cycle and the arrows must move the field cursor instead —
    # otherwise the Language field is unreachable from the Launch field, and
    # the "change settings without quitting" flow gets stuck.
    voice_cycling = vf_idx is not None and state.field_cursor == vf_idx and bool(state.voice_filter)
    if key == "up" or char == "k":
        if voice_cycling:
            _move_voice(state, -1)
        else:
            state.field_cursor = max(0, state.field_cursor - 1)
    elif key == "down" or char == "j":
        if voice_cycling:
            _move_voice(state, 1)
        else:
            state.field_cursor = min(len(fields), state.field_cursor + 1)
    elif key == "page_up":
        if voice_cycling:
            _move_voice(state, -10)
        else:
            state.field_cursor = 0
    elif key == "page_down":
        if voice_cycling:
            _move_voice(state, 10)
        else:
            state.field_cursor = len(fields)
    elif key == "home":
        state.field_cursor = 0
    elif key == "end":
        state.field_cursor = len(fields)
    elif key == "left" or char == "h":
        if state.field_cursor == model_idx:
            _cycle_model(state, -1)
        elif speed_idx is not None and state.field_cursor == speed_idx:
            state.speed = max(0.5, round(state.speed - 0.1, 1))
        else:
            state.pane_focus = "files"
    elif key == "right" or char == "l":
        if state.field_cursor == model_idx:
            _cycle_model(state, 1)
        elif speed_idx is not None and state.field_cursor == speed_idx:
            state.speed = min(2.0, round(state.speed + 0.1, 1))
        else:
            state.pane_focus = "files"
    elif char in ("-", ","):
        _adjust_field(state, -1)
    elif char in ("+", "=", "."):
        _adjust_field(state, 1)
    elif key == "enter":
        # Primary action: with a book selected, Enter starts reading from any
        # field (the Language/Folder pickers keep Enter for opening their popup).
        if state.selected_file and state.field_cursor != lang_idx and state.field_cursor != folder_idx:
            return "launch"
        if lang_idx is not None and state.field_cursor == lang_idx:
            _open_lang_picker(state)
            return "continue"
        if folder_idx is not None and state.field_cursor == folder_idx:
            _open_folder_picker(state)
            return "continue"
        state.field_cursor = (state.field_cursor + 1) % (len(fields) + 1)
    elif char == " " and lang_idx is not None and state.field_cursor == lang_idx:
        _open_lang_picker(state)
        return "continue"
    elif char == " " and folder_idx is not None and state.field_cursor == folder_idx:
        _open_folder_picker(state)
        return "continue"
    elif key == "tab":
        state.pane_focus = "files"
    elif key == "backspace":
        if vf_idx is not None and state.field_cursor == vf_idx and state.voice_filter:
            state.voice_filter = state.voice_filter[:-1]
            _set_voice_index(state, 0)
    elif key == "esc":
        if vf_idx is not None and state.field_cursor == vf_idx and state.voice_filter:
            state.voice_filter = ""
            _set_voice_index(state, 0)
        elif state.field_cursor > 0:
            state.field_cursor -= 1
        else:
            state.pane_focus = "files"
    elif is_char:
        if vf_idx is not None and state.field_cursor == vf_idx:
            state.voice_filter += char
            _set_voice_index(state, 0)
    return "continue"


def _kokoro_code_for_voice(voice: str) -> str:
    """Return the KOKORO_LANG code whose voice list contains `voice`."""
    for code, voices in KOKORO_VOICES.items():
        if any(name == voice for name, _g in voices):
            return code
    return ""


def _cosyvoice_code_for_voice(voice: str) -> str:
    """Return the COSYVOICE lang code whose voice list contains `voice`."""
    for code, voices in _cosyvoice_voices_by_lang().items():
        if any(name == voice for name, _g in voices):
            return code
    return ""


def _make_result(state: MenuState) -> MenuResult:
    model = state.models[state.model_idx] if state.models else "none"
    voice = None
    lang = None
    if model != "none":
        names, idx = _current_voice_list(state)
        if names and 0 <= idx < len(names):
            voice = names[idx][0]
        if model == "kokoro":
            # The chosen voice pins the language even when several are
            # selected; fall back to the first selected code if unknown.
            lang = _kokoro_code_for_voice(voice) if voice else ""
            if not lang and state.kokoro_sel:
                lang = state.kokoro_sel[0]
        elif model == "cosyvoice":
            # The chosen reference voice (zh/en) pins the language, and the
            # menu-facing name maps back to the prompt id the worker loads.
            lang = _cosyvoice_code_for_voice(voice) if voice else ""
            if not lang and state.cosyvoice_sel:
                lang = state.cosyvoice_sel[0]
            voice = _cosyvoice_prompt_for_name(voice)
    return MenuResult(
        file_path=os.path.abspath(state.selected_file) if state.selected_file else "",
        tts_name=model,
        voice=voice,
        lang=lang,
        speed=state.speed,
    )


# ---------------------------------------------------------------------------
# Language multi-select popup
# ---------------------------------------------------------------------------

PICKER_LANG_ROWS = 10      # max language rows shown at once


def _picker_window(picker: LangPickerState, height: int) -> tuple[int, int]:
    """Return (first visible option index, visible row count) for the picker."""
    total = len(picker.options)
    visible = max(3, min(PICKER_LANG_ROWS, total, height - 12))
    start = max(0, picker.cursor - visible + 1) if picker.cursor >= visible else 0
    start = min(start, max(0, total - visible))
    return start, visible


def _picker_geometry(picker: LangPickerState, height: int) -> tuple[int, int, int]:
    """Return (panel top row, panel height, rows of content) — rows 1-based."""
    _start, visible = _picker_window(picker, height)
    content = 3 + visible + 2
    panel_h = content + 4  # borders (2) + vertical padding (2)
    panel_h = min(panel_h, max(5, height - 2))
    top = max(1, (height - panel_h) // 2 + 1)
    return top, panel_h, content


def _center_frame(panel, top: int, panel_h: int, hint: str, width: int, height: int) -> str:
    """Center `panel` on an otherwise blank screen with a dim hint line below."""
    layout = Layout()
    layout.split_column(
        Layout(name="top", size=max(0, top - 1)),
        Layout(name="mid", size=panel_h),
        Layout(name="hint", size=1),
        Layout(name="bottom", ratio=1),
    )
    layout["top"].update(Text(""))
    layout["mid"].update(Align.center(panel))
    layout["hint"].update(Align.center(Text(hint, style="dim")))
    layout["bottom"].update(Text(""))

    temp_console = Console(width=width, height=height, force_terminal=True)
    with temp_console.capture() as capture:
        temp_console.print(layout, overflow="crop")
    frame = capture.get().split("\n")
    if len(frame) > height:
        frame = frame[:height]
    return "\033[?25l\033[H" + "\n".join(frame)


def render_folder_panel(
    picker: FolderPickerState, width: int, height: int, *,
    title: str, header: str, hint: str,
) -> str:
    """Render the folder chooser centred on screen (wizard step 1 and popup)."""
    top, panel_h, _content = _folder_panel_geometry(picker, height)
    box_width = min(WIZARD_BOX_WIDTH, max(30, width - 4))
    inner = max(10, box_width - 6)
    lines = [Text(header, style="bold"), Text("")]
    lines.extend(_folder_body(picker, inner, height))
    panel = Panel(
        Group(*lines),
        title=f"[bold cyan]{title}[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
        width=box_width,
    )
    return _center_frame(panel, top, panel_h, hint, width, height)


def render_lang_picker(state: MenuState, width: int, height: int) -> str:
    """Render the full-screen language multi-select popup to a string frame."""
    picker = state.lang_picker
    top, panel_h, _content = _picker_geometry(picker, height)
    box_width = min(WIZARD_BOX_WIDTH, max(30, width - 4))
    lines: list[Text] = []
    inner = max(10, box_width - 6)

    lines.append(Text("Which languages should the voices use?  (Space toggles)", style="bold"))
    lines.append(Text(""))
    start, visible = _picker_window(picker, height)
    sel = set(_selected_langs(state))
    for i in range(start, min(start + visible, len(picker.options))):
        code, label = picker.options[i]
        selected = code in sel or (code == "" and not sel)
        is_cursor = i == picker.cursor
        row = Text("✓ " if selected else "  ", style="bold green" if selected else "dim")
        row.append("> " if is_cursor else "  ")
        row.append(
            _fit_tail(label, inner - 5),
            style="reverse bold cyan" if is_cursor else ("bold" if selected else ""),
        )
        lines.append(row)
    lines.append(Text(""))
    lines.append(Text(
        _fit_tail("  ↑/↓ move · Space toggle · Enter done · type to jump · click to toggle", inner),
        style="dim",
    ))

    panel = Panel(
        Group(*lines),
        title="[bold cyan]Languages[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
        width=box_width,
    )
    return _center_frame(
        panel, top, panel_h, f"Selection: {_lang_summary(state)}", width, height,
    )


def _toggle_picker_lang(state: MenuState, code: str) -> None:
    """Toggle one code in/out of the active model's selection ('' = all)."""
    sel = _selected_langs(state)
    if code == "":
        new = []
    elif code in sel:
        new = [c for c in sel if c != code]
    else:
        new = sel + [code]
    _set_selected_langs(state, new)
    _persist_defaults(state)


def _handle_picker(state: MenuState, key) -> str:
    """Handle a key/mouse event while the language popup is open."""
    picker = state.lang_picker
    total = len(picker.options)

    if isinstance(key, tuple) and key[0] == "mouse":
        button, x, y = key[1]
        if button in (64, 65):
            delta = -3 if button == 64 else 3
            picker.cursor = max(0, min(total - 1, picker.cursor + delta))
            return "continue"
        if button != 0:
            return "continue"
        height = ui.get_terminal_size()[1]
        start, visible = _picker_window(picker, height)
        top, _panel_h, _content = _picker_geometry(picker, height)
        first_row = top + 1 + 1 + 2  # border + padding + title + blank line
        row = y - first_row
        if 0 <= row < visible:
            idx = start + row
            if 0 <= idx < total:
                picker.cursor = idx
                _toggle_picker_lang(state, picker.options[idx][0])
        return "continue"

    is_char = isinstance(key, tuple)
    char = key[1] if is_char else None

    if key == "up":
        picker.cursor = max(0, picker.cursor - 1)
    elif key == "down":
        picker.cursor = min(total - 1, picker.cursor + 1)
    elif key == "page_up":
        picker.cursor = max(0, picker.cursor - 10)
    elif key == "page_down":
        picker.cursor = min(total - 1, picker.cursor + 10)
    elif key == "home":
        picker.cursor = 0
    elif key == "end":
        picker.cursor = max(0, total - 1)
    elif char == " ":
        _toggle_picker_lang(state, picker.options[picker.cursor][0])
    elif key in ("enter", "esc", "tab"):
        state.lang_picker = None
        _persist_defaults(state)
        return "continue"
    elif is_char:
        # Type a letter to jump to the first language starting with it.
        needle = char.lower()
        for i, (code, label) in enumerate(picker.options):
            if label.lower().startswith(needle) or code.lower().startswith(needle):
                picker.cursor = i
                break
    return "continue"


# ---------------------------------------------------------------------------
# First-run setup wizard
# ---------------------------------------------------------------------------

WIZARD_BOX_WIDTH = 66      # panel width (clamped to the terminal)
WIZARD_LANG_ROWS = 10      # max language rows shown at once


@dataclass
class WizardState:
    """State for the two-step first-run setup screen (folder, then language)."""

    start_dir: str                                       # folder the chooser opens on
    lang_options: list[tuple[str, str]] = field(default_factory=list)
    step: str = "folder"                                 # "folder" | "lang"
    folder: FolderPickerState | None = None              # built from start_dir
    lang_idx: int = 0
    chosen_dir: str = ""
    chosen_langs: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.folder is None:
            self.folder = new_folder_picker(self.start_dir)


def _order_langs(model: str, codes: list[str]) -> list[str]:
    """Chinese + English first (they are the defaults), then the rest as given."""
    pinned = [c for c in PINNED_LANGS.get(model, []) if c in codes]
    return pinned + [c for c in codes if c not in pinned]


def _default_langs(model: str, available: list[str]) -> list[str]:
    """The out-of-the-box language selection for a model."""
    return [c for c in PINNED_LANGS.get(model, []) if c in available]


def _wizard_lang_options(state: MenuState) -> list[tuple[str, str]]:
    """Language choices (code, label) for the active model; empty when N/A."""
    model = state.models[state.model_idx] if state.models else "none"
    if model == "edge":
        options = [("", "All languages")]
        for code in _order_langs("edge", state.edge_langs):
            count = sum(
                1 for v in state.edge_voices
                if (v.get("Locale", "") or "").split("-")[0].lower() == code
            )
            name = LANG_NAMES.get(code, code)
            options.append((code, f"{name} ({code}) - {count} voices"))
        return options
    if model == "kokoro":
        return [(code, f"{KOKORO_LANG_CODES[code]} ({code})")
                for code in _order_langs("kokoro", list(KOKORO_LANG_CODES))]
    if model == "cosyvoice":
        return [(code, f"{COSYVOICE_LANG_CODES[code]} ({code})")
                for code in _order_langs("cosyvoice", list(COSYVOICE_LANG_CODES))]
    return []


def _apply_wizard_choices(state: MenuState, wiz: WizardState) -> None:
    """Copy the wizard's answers onto the menu state and persist them."""
    model = state.models[state.model_idx] if state.models else "none"
    if wiz.chosen_dir:
        state.default_dir = wiz.chosen_dir
        cd(state, wiz.chosen_dir)
    if model == "edge":
        state.edge_sel = [c for c in wiz.chosen_langs if c in state.edge_langs]
    elif model == "kokoro":
        state.kokoro_sel = [c for c in wiz.chosen_langs if c in KOKORO_LANG_CODES]
    elif model == "cosyvoice":
        state.cosyvoice_sel = [c for c in wiz.chosen_langs if c in COSYVOICE_LANG_CODES]
    state.voice_filter = ""
    _set_voice_index(state, 0)
    _seed_default_voice(state)
    _persist_defaults(state)


def _wizard_lang_window(wiz: WizardState, height: int) -> tuple[int, int]:
    """Return (first visible option index, visible row count) for the lang list."""
    total = len(wiz.lang_options)
    visible = max(3, min(WIZARD_LANG_ROWS, total, height - 12))
    start = max(0, wiz.lang_idx - visible + 1) if wiz.lang_idx >= visible else 0
    start = min(start, max(0, total - visible))
    return start, visible


def _wizard_geometry(wiz: WizardState, height: int) -> tuple[int, int, int]:
    """Return (panel top row, panel height, rows of content) for the current step.

    Rows are 1-based screen rows so mouse coordinates can be mapped directly.
    """
    if wiz.step == "folder":
        return _folder_panel_geometry(wiz.folder, height)
    _start, visible = _wizard_lang_window(wiz, height)
    content = 3 + visible + 2
    panel_h = content + 4  # borders (2) + vertical padding (2)
    panel_h = min(panel_h, max(5, height - 2))
    top = max(1, (height - panel_h) // 2 + 1)
    return top, panel_h, content


def _fit_tail(text: str, width: int) -> str:
    """Trim text to width, keeping the end (paths matter most on the right)."""
    if width <= 1 or len(text) <= width:
        return text
    return "…" + text[-(width - 1):]


def render_wizard(wiz: WizardState, width: int, height: int) -> str:
    """Render the full-screen setup wizard to a string frame."""
    # Every line must fit on one row: a wrapped line would push the panel past
    # the height reserved for it (and shift the rows the mouse maps).
    steps = 2 if wiz.lang_options else 1
    if wiz.step == "folder":
        return render_folder_panel(
            wiz.folder, width, height,
            title=f"Yue setup  (1 of {steps})",
            header="Where do you keep your books?",
            hint="Esc = skip setup  ·  Ctrl+C = quit",
        )

    top, panel_h, _content = _wizard_geometry(wiz, height)
    box_width = min(WIZARD_BOX_WIDTH, max(30, width - 4))
    lines: list[Text] = []
    inner = max(10, box_width - 6)

    lines.append(Text("Which languages should the voices use?  (Space toggles)", style="bold"))
    lines.append(Text(""))
    sel = set(wiz.chosen_langs)
    start, visible = _wizard_lang_window(wiz, height)
    for i in range(start, min(start + visible, len(wiz.lang_options))):
        code, label = wiz.lang_options[i]
        checked = code in sel or (code == "" and not sel)
        is_cursor = i == wiz.lang_idx
        row = Text("✓ " if checked else "  ", style="bold green" if checked else "dim")
        row.append("> " if is_cursor else "  ")
        row.append(
            _fit_tail(label, inner - 5),
            style="reverse bold cyan" if is_cursor else ("bold" if checked else ""),
        )
        lines.append(row)
    lines.append(Text(""))
    lines.append(Text(
        _fit_tail("  ↑/↓ move · Space toggle · Enter done · ← back to the folder", inner),
        style="dim",
    ))

    panel = Panel(
        Group(*lines),
        title=f"[bold cyan]Yue setup  (2 of {steps})[/bold cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(1, 2),
        width=box_width,
    )
    return _center_frame(
        panel, top, panel_h, "Esc = skip setup  ·  Ctrl+C = quit", width, height,
    )


def _wizard_commit_folder(wiz: WizardState) -> str:
    """Take the chooser's folder and advance. Returns 'continue' or 'done'."""
    target = wiz.folder.cur_dir
    if not os.path.isdir(target):
        wiz.folder.error = f"Not a folder: {target}"
        return "continue"
    wiz.chosen_dir = os.path.abspath(target)
    wiz.folder.error = None
    if not wiz.lang_options:
        return "done"
    wiz.step = "lang"
    return "continue"


def _wizard_mouse(wiz: WizardState, button: int, x: int, y: int, height: int) -> str:
    """Handle a mouse event on the wizard. Returns 'continue' or 'done'."""
    if wiz.step == "folder":
        if button in (64, 65):
            _folder_wheel(wiz.folder, -3 if button == 64 else 3)
            return "continue"
        if button != 0:
            return "continue"
        top, _panel_h, _content = _wizard_geometry(wiz, height)
        # panel top border + padding + header + blank line
        if _folder_click(wiz.folder, y, top + 4, height) == "choose":
            outcome = _wizard_commit_folder(wiz)
            return "done" if outcome == "done" else "continue"
        return "continue"
    start, visible = _wizard_lang_window(wiz, height)
    if button in (64, 65):
        delta = -3 if button == 64 else 3
        wiz.lang_idx = max(0, min(len(wiz.lang_options) - 1, wiz.lang_idx + delta))
        return "continue"
    if button != 0:
        return "continue"
    top, _panel_h, _content = _wizard_geometry(wiz, height)
    # panel top border + padding + header + blank line
    first_row = top + 1 + 1 + 2
    row = y - first_row
    if 0 <= row < visible:
        idx = start + row
        if 0 <= idx < len(wiz.lang_options):
            wiz.lang_idx = idx
            code = wiz.lang_options[idx][0]
            if code == "":
                wiz.chosen_langs = []
            elif code in wiz.chosen_langs:
                wiz.chosen_langs = [c for c in wiz.chosen_langs if c != code]
            else:
                wiz.chosen_langs = list(wiz.chosen_langs) + [code]
    return "continue"


def handle_wizard_key(wiz: WizardState, key, height: int) -> str:
    """Handle one key for the wizard. Returns 'continue', 'done', 'skip' or 'quit'."""
    if key == "ctrl_c":
        return "quit"
    if isinstance(key, tuple) and key[0] == "mouse":
        return _wizard_mouse(wiz, key[1][0], key[1][1], key[1][2], height)

    if wiz.step == "folder":
        # The chooser owns every key here: letters filter, Esc backs out of a
        # filter or the path field before it means "skip setup".
        outcome = _handle_folder_key(wiz.folder, key, height)
        if outcome == "choose":
            return _wizard_commit_folder(wiz)
        if outcome == "cancel":
            return "skip"
        return "continue"

    is_char = isinstance(key, tuple)
    char = key[1] if is_char else None

    if key == "esc":
        return "skip"

    # ---- language step ----
    total = len(wiz.lang_options)
    _start, visible = _wizard_lang_window(wiz, height)
    if key == "up":
        wiz.lang_idx = max(0, wiz.lang_idx - 1)
    elif key == "down":
        wiz.lang_idx = min(total - 1, wiz.lang_idx + 1)
    elif key == "page_up":
        wiz.lang_idx = max(0, wiz.lang_idx - visible)
    elif key == "page_down":
        wiz.lang_idx = min(total - 1, wiz.lang_idx + visible)
    elif key == "home":
        wiz.lang_idx = 0
    elif key == "end":
        wiz.lang_idx = max(0, total - 1)
    elif key in ("left", "backspace"):
        # Step back to the folder chooser — the folder is never a one-shot answer.
        wiz.step = "folder"
    elif key == "enter":
        # Confirm the whole selection (Space-built) as-is.
        return "done"
    elif char == " ":
        code = wiz.lang_options[wiz.lang_idx][0] if total else ""
        if code == "":
            wiz.chosen_langs = []
        elif code in wiz.chosen_langs:
            wiz.chosen_langs = [c for c in wiz.chosen_langs if c != code]
        else:
            wiz.chosen_langs = list(wiz.chosen_langs) + [code]
    elif key == "tab":
        return "done"
    elif is_char:
        # Type a letter to jump to the first language starting with it.
        needle = char.lower()
        for i, (code, label) in enumerate(wiz.lang_options):
            if label.lower().startswith(needle) or code.lower().startswith(needle):
                wiz.lang_idx = i
                break
    return "continue"


def _wizard_initial_lang_idx(state: MenuState, options: list[tuple[str, str]]) -> int:
    """Place the cursor on the first already-selected language."""
    sel = set(_selected_langs(state))
    for i, (code, _label) in enumerate(options):
        if code and code in sel:
            return i
    return 0


async def run_setup_wizard(state: MenuState) -> str:
    """Run the first-run setup screen. Returns 'done', 'skip' or 'quit'."""
    options = _wizard_lang_options(state)
    wiz = WizardState(
        start_dir=state.current_dir,
        lang_options=options,
        lang_idx=_wizard_initial_lang_idx(state, options),
        chosen_langs=_selected_langs(state),
    )
    def _handle(key, height):
        outcome = handle_wizard_key(wiz, key, height)
        if wiz.chosen_dir and wiz.chosen_dir != state.default_dir:
            # Persist the folder as soon as step 1 is answered, so it survives
            # even if the language step is abandoned.
            state.default_dir = wiz.chosen_dir
            _persist_defaults(state)
        return outcome

    outcome = await _run_input_loop(lambda w, h: render_wizard(wiz, w, h), _handle)
    if outcome == "quit":
        return "quit"
    # Keep whatever was answered before a skip (e.g. the folder from step 1),
    # and fall back to the browsed folder so the wizard does not nag again.
    _apply_wizard_choices(state, wiz)
    if not state.default_dir:
        state.default_dir = state.current_dir
    if outcome == "done":
        state.status_msg = f"Default folder set: {state.default_dir}"
    else:
        state.status_msg = (
            f"Setup skipped - using {state.default_dir} "
            "(change it any time on the Folder field, or press 's' here)"
        )
    _persist_defaults(state)
    return outcome


# ---------------------------------------------------------------------------
# Key loop and entry point
# ---------------------------------------------------------------------------

async def _run_input_loop(render, handle) -> str:
    """Render frames and dispatch keys until handle() returns a non-'continue'.

    render(width, height) -> frame string; handle(key, height) -> outcome.
    Shared by the setup wizard and the main menu so both get the same key
    parsing, resize handling and split-escape-sequence tolerance.
    """
    fd = sys.stdin.fileno()
    key_event = asyncio.Event()
    buffer = bytearray()
    keys = collections.deque()
    loop = asyncio.get_running_loop()

    def _on_key():
        try:
            chunk = os.read(fd, 1024)
        except (BlockingIOError, OSError):
            return
        if chunk:
            buffer.extend(chunk)
        key_event.set()

    def _on_resize():
        key_event.set()

    loop.add_reader(fd, _on_key)
    try:
        loop.add_signal_handler(signal.SIGWINCH, _on_resize)
    except (NotImplementedError, RuntimeError):
        pass

    esc_deferred = False
    height = ui.get_terminal_size()[1]
    try:
        while True:
            try:
                width, height = ui.get_terminal_size()
                sys.stdout.write(render(width, height))
                sys.stdout.flush()
            except Exception:
                pass

            if esc_deferred:
                # A lone Esc was seen last round. Wait briefly in case a split
                # arrow-key sequence is still arriving, then resolve it.
                await asyncio.sleep(0.03)
                esc_deferred = False
                keys.extend(_drain_keys(buffer))
                if not keys and bytes(buffer) == b"\x1b":
                    del buffer[:]
                    keys.append("esc")
            else:
                key_event.clear()
                await key_event.wait()
                keys.extend(_drain_keys(buffer))
                if not keys and bytes(buffer) == b"\x1b":
                    esc_deferred = True
                    continue

            esc_deferred = False
            while keys:
                key = keys.popleft()
                try:
                    outcome = handle(key, height)
                except Exception:
                    outcome = "continue"
                if outcome != "continue":
                    return outcome
    finally:
        loop.remove_reader(fd)
        try:
            loop.remove_signal_handler(signal.SIGWINCH)
        except (ValueError, NotImplementedError):
            pass


async def run_key_loop(state: MenuState) -> MenuResult | None:
    """Render + read keys until the user launches or quits."""
    outcome = await _run_input_loop(
        lambda w, h: render_menu(state, w, h),
        lambda key, _h: handle_key(state, key),
    )
    return _make_result(state) if outcome == "launch" else None


async def run_start_menu(
    console: Console,
    available_tts: list[str],
    *,
    start_dir: str | None = None,
    preselect_file: str | None = None,
    default_tts: str | None = None,
    default_voice: str | None = None,
    default_lang: str | None = None,
    default_speed: float = 1.0,
    guided: bool = False,
) -> MenuResult | None:
    """Run the interactive start menu, returning MenuResult or None if cancelled.

    With guided=True (the `yue ui` shortcut), a first run with no saved default
    folder shows the two-step setup wizard (folder, then language) first.
    """
    if not sys.stdin.isatty():
        console.print("[red]The interactive start menu requires a terminal.[/red]")
        return None

    width, height = ui.get_terminal_size()
    if width < 40 or height < 12:
        console.print("[red]Terminal too small for the start menu (minimum 40x12).[/red]")
        return None

    models = (list(available_tts) + ["none"]) if available_tts else ["none"]

    edge_voices = []
    if "edge" in models:
        console.print("[cyan]Fetching Edge voice list...[/cyan]")
        try:
            import edge_tts
            edge_voices = await asyncio.wait_for(edge_tts.list_voices(), timeout=8)
            edge_voices = [v for v in edge_voices if isinstance(v, dict)]
            edge_voices.sort(key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
        except Exception:
            edge_voices = []
        if not edge_voices:
            edge_voices = _edge_fallback_dicts()
            console.print("[yellow]Could not fetch the Edge voice list; showing common voices.[/yellow]")

    # Load persisted user settings (default folder + language selection).
    prefs = settings.load_settings()
    saved_dir = prefs.get("default_start_dir", "")
    saved_langs = _parse_lang_value(prefs.get("default_language", ""))
    # An empty saved list means the user chose "All languages"; the untouched
    # default is settings.DEFAULTS' "" (a string), which falls back to the
    # built-in Chinese + English selection.
    has_saved_langs = isinstance(prefs.get("default_language"), list)

    # Available edge language codes (e.g. "en", "es") from the voice list.
    edge_langs = []
    for v in edge_voices:
        loc = (v.get("Locale", "") or "").split("-")[0].lower()
        if loc and loc not in edge_langs:
            edge_langs.append(loc)
    edge_langs.sort()

    # Resolve the starting directory. An explicit --menu file or start_dir
    # wins; otherwise a saved default folder overrides the current directory.
    if start_dir and os.path.isdir(start_dir):
        sdir = start_dir
    elif preselect_file:
        sdir = os.path.dirname(os.path.abspath(preselect_file))
    elif saved_dir and os.path.isdir(saved_dir):
        sdir = saved_dir
    else:
        try:
            sdir = os.getcwd()
        except OSError:
            sdir = os.path.expanduser("~")
    if not os.path.isdir(sdir):
        sdir = os.getcwd()

    state = MenuState(
        current_dir=os.path.abspath(sdir),
        models=models,
        edge_voices=edge_voices,
        edge_langs=edge_langs,
        default_dir=os.path.abspath(saved_dir) if saved_dir and os.path.isdir(saved_dir) else "",
    )

    if default_tts in models:
        state.model_idx = models.index(default_tts)
    if default_speed:
        state.speed = max(0.5, min(2.0, float(default_speed)))
    # Apply the saved language selection (a list; legacy single strings are
    # normalised by _parse_lang_value). Each model keeps only its own codes.
    if "kokoro" in models:
        cli_lang = default_lang if default_lang in KOKORO_LANG_CODES else ""
        langs = [c for c in (saved_langs + [cli_lang] if cli_lang else saved_langs)
                 if c in KOKORO_LANG_CODES]
        if not langs and not has_saved_langs:
            langs = _default_langs("kokoro", list(KOKORO_LANG_CODES))
        state.kokoro_sel = _order_langs("kokoro", langs)
    if "cosyvoice" in models:
        cli_lang = default_lang if default_lang in COSYVOICE_LANG_CODES else ""
        langs = [c for c in (saved_langs + [cli_lang] if cli_lang else saved_langs)
                 if c in COSYVOICE_LANG_CODES]
        if not langs and not has_saved_langs:
            langs = _default_langs("cosyvoice", list(COSYVOICE_LANG_CODES))
        state.cosyvoice_sel = _order_langs("cosyvoice", langs)
    if "edge" in models:
        langs = [c for c in saved_langs if c in edge_langs]
        if not langs and not has_saved_langs:
            langs = _default_langs("edge", edge_langs)
        state.edge_sel = _order_langs("edge", langs)
    _seed_default_voice(state)
    if default_voice:
        _seed_voice_by_name(state, default_voice)

    if preselect_file and os.path.isfile(preselect_file):
        state.selected_file = os.path.abspath(preselect_file)
        state.pane_focus = "settings"
        state.field_cursor = _launch_field_idx(state) or 0

    # First run of `yue ui` with no saved folder: ask for the folder and the
    # language up front, on their own screen, before showing the menu.
    show_wizard = guided and not state.default_dir and not preselect_file

    build_file_rows(state)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # Hide cursor, enable mouse click tracking (SGR), enter alternate screen.
        sys.stdout.write("\033[?25l\033[?1000h\033[?1006h\033[?1049h")
        sys.stdout.flush()
        if show_wizard and await run_setup_wizard(state) == "quit":
            return None
        return await run_key_loop(state)
    finally:
        sys.stdout.write("\033[?1049l\033[?1000l\033[?1006l\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
