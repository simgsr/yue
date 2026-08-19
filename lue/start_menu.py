"""Interactive in-terminal start menu for the Lue eBook reader.

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

from rich.console import Console, Group
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich import box

from . import ui, config, progress_manager, settings

# Supported book extensions - must match content_parser.extract_content exactly.
START_MENU_EXTENSIONS = (".epub", ".pdf", ".txt", ".docx", ".html", ".rtf", ".md")

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
    kokoro_lang: str = "a"
    kokoro_voice_idx: int = 0
    speed: float = 1.0
    field_cursor: int = 0
    default_dir: str = ""           # persisted default start folder ("" = unset)
    folder_draft: str = ""          # in-progress path text for the Folder field
    edge_lang: str = ""             # selected edge language code ("" = all voices)
    edge_langs: list[str] = field(default_factory=list)  # available edge language codes
    status_msg: str | None = None   # transient feedback line (e.g. "Default folder set")
    guided: bool = False            # first run: guide folder input, then language


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
    return 0


def _set_voice_index(state: MenuState, new_idx: int) -> None:
    model = state.models[state.model_idx] if state.models else "none"
    if model == "edge":
        state.edge_voice_idx = new_idx
    elif model == "kokoro":
        state.kokoro_voice_idx = new_idx


def _current_voice_list(state: MenuState):
    """Return ([(name, info)], current_index) for the active model, applying voice_filter."""
    model = state.models[state.model_idx] if state.models else "none"
    flt = state.voice_filter.strip().lower()
    if model == "kokoro":
        all_v = [(n, g) for n, g in KOKORO_VOICES.get(state.kokoro_lang, [])]
    elif model == "edge":
        voices = state.edge_voices
        if state.edge_lang:
            voices = [v for v in voices
                      if (v.get("Locale", "") or "").split("-")[0].lower() == state.edge_lang]
        all_v = [(v.get("ShortName", ""), v.get("Gender", "")) for v in voices]
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


# ---------------------------------------------------------------------------
# Settings pane field helpers
# ---------------------------------------------------------------------------

def _settings_fields(state: MenuState) -> list[str]:
    model = state.models[state.model_idx] if state.models else "none"
    if model == "kokoro":
        return ["folder", "model", "voice", "lang", "speed", "launch"]
    if model == "edge":
        return ["folder", "model", "lang", "voice", "speed", "launch"]
    return ["folder", "model", "launch"]


def _field_idx(state: MenuState, name: str) -> int | None:
    fields = _settings_fields(state)
    return fields.index(name) if name in fields else None


def _voice_field_idx(state: MenuState) -> int | None:
    return _field_idx(state, "voice")


def _launch_field_idx(state: MenuState) -> int | None:
    return _field_idx(state, "launch")


def _folder_field_idx(state: MenuState) -> int | None:
    return _field_idx(state, "folder")


def _leave_folder_if_idle(state: MenuState) -> None:
    """If the cursor sits on the Folder field with nothing typed, hop past it.

    The Folder field is a path-typing field, so landing on it blocks arrow
    navigation. When the user moves into the settings pane (not guided, not
    mid-typing), skip it so they land on a regular field instead.
    """
    folder_idx = _folder_field_idx(state)
    if folder_idx is not None and state.field_cursor == folder_idx and not state.folder_draft:
        state.field_cursor = min(folder_idx + 1, len(_settings_fields(state)) - 1)


def _current_lang(state: MenuState) -> str:
    """The active model's selected language code ("" when none / all)."""
    model = state.models[state.model_idx] if state.models else "none"
    if model == "kokoro":
        return state.kokoro_lang
    if model == "edge":
        return state.edge_lang
    return ""


def _persist_defaults(state: MenuState) -> None:
    """Save the default start folder + selected language to settings.json."""
    prefs = settings.load_settings()
    prefs["default_start_dir"] = state.default_dir
    prefs["default_language"] = _current_lang(state)
    settings.save_settings(prefs)


def _cycle_edge_lang(state: MenuState, delta: int) -> None:
    """Cycle the edge language filter. Options are "" (all) then the codes."""
    if not state.edge_langs:
        return
    opts = [""] + state.edge_langs
    idx = opts.index(state.edge_lang) if state.edge_lang in opts else 0
    state.edge_lang = opts[(idx + delta) % len(opts)]
    state.voice_filter = ""
    _set_voice_index(state, 0)


def _commit_folder(state: MenuState) -> None:
    """Validate + persist the Folder field, then advance to the next field."""
    draft = os.path.expanduser(state.folder_draft.strip())
    if draft:
        if not os.path.isdir(draft):
            state.error_msg = f"Not a directory: {draft}"
            state.folder_draft = draft
            return
        state.default_dir = os.path.abspath(draft)
        state.folder_draft = ""
        state.status_msg = f"Default folder set: {state.default_dir}"
    elif not state.default_dir:
        # Nothing typed: fall back to the directory the browser is showing.
        state.default_dir = state.current_dir
        state.status_msg = f"Default folder set: {state.default_dir}"
    _persist_defaults(state)
    fields = _settings_fields(state)
    if state.guided and "lang" in fields:
        # First run: after the folder, ask for a language next.
        state.field_cursor = fields.index("lang")
        state.guided = False
    else:
        state.field_cursor = min(state.field_cursor + 1, len(fields) - 1)




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
    if state.folder_draft:
        folder_val = state.folder_draft
    elif state.default_dir:
        folder_val = state.default_dir
    else:
        folder_val = "not set — type a path"
    _add("Folder", folder_val, folder_idx)

    _add("Model", model, fields.index("model"))
    if vf_idx is not None:
        _add("Voice", _selected_voice_name(state), vf_idx)
    if model == "kokoro":
        lang_val = f"{KOKORO_LANG_CODES.get(state.kokoro_lang, state.kokoro_lang)} ({state.kokoro_lang})"
        _add("Language", lang_val, fields.index("lang"))
    elif model == "edge":
        if state.edge_lang:
            count = sum(
                1 for v in state.edge_voices
                if (v.get("Locale", "") or "").split("-")[0].lower() == state.edge_lang
            )
            lang_val = f"{state.edge_lang} ({count} voices)"
        else:
            lang_val = "All"
        _add("Language", lang_val, fields.index("lang"))
    if model in ("edge", "kokoro"):
        _add("Speed", f"{state.speed:.1f}x", fields.index("speed"))
    if state.selected_file:
        _add("Launch", "Start Reading", lf_idx, style="bold green")
    else:
        _add("Launch", "Select a book first", lf_idx, style="dim")

    parts = [field_table]
    if model != "none":
        names, sel = _current_voice_list(state)
        voice_table = Table(box=None, show_header=False, padding=0, expand=True)
        voice_table.add_column("Sel", width=3)
        voice_table.add_column("Voice", ratio=1, no_wrap=True, overflow="ellipsis")
        voice_table.add_column("Info", justify="right")

        flt_label = f"Voices{(' [' + state.voice_filter + ']') if state.voice_filter else ''}"
        if voice_focused:
            flt_label += " (type to filter)"
        voice_table.add_row("", Text(flt_label, style="dim"), "")

        max_visible = max(1, pane_height - len(fields) - 5)
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
        parts.append(voice_table)

    content = Group(*parts) if len(parts) > 1 else parts[0]
    return Panel(
        content,
        title="[bold cyan]TTS Settings[/bold cyan]",
        border_style="cyan" if focused else "blue",
        box=box.ROUNDED,
        padding=(1, 1),
        expand=True,
    )


def render_footer(state: MenuState):
    if state.guided and state.pane_focus == "settings":
        hint = "Type a folder path, Enter to save · then pick a language · Esc/Tab to browse instead · q quit"
    elif state.pane_focus == "files":
        hint = "↑/↓ move · Enter open/select · type to search · s = set default folder · Backspace up · Esc back/quit · q quit"
    else:
        hint = "↑/↓ move · Enter next/launch · ←/→ or +/- adjust · Esc back · Tab/← files · q quit"
    return Panel(Text(hint, style="dim"), border_style="blue", box=box.ROUNDED)


def render_menu(state: MenuState, width: int, height: int) -> str:
    """Render the full-screen menu to a string frame."""
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
    state.field_cursor = min(state.field_cursor, len(_settings_fields(state)) - 1)


def _cycle_kokoro_lang(state: MenuState, delta: int) -> None:
    codes = list(KOKORO_LANG_CODES.keys())
    try:
        idx = codes.index(state.kokoro_lang)
    except ValueError:
        idx = 0
    state.kokoro_lang = codes[(idx + delta) % len(codes)]
    state.voice_filter = ""
    _set_voice_index(state, 0)


def _adjust_field(state: MenuState, delta: int) -> None:
    fields = _settings_fields(state)
    model = state.models[state.model_idx] if state.models else "none"
    if state.field_cursor == fields.index("model"):
        _cycle_model(state, delta)
    elif "lang" in fields and state.field_cursor == fields.index("lang"):
        if model == "kokoro":
            _cycle_kokoro_lang(state, delta)
        elif model == "edge":
            _cycle_edge_lang(state, delta)
        _persist_defaults(state)
    elif "speed" in fields and state.field_cursor == fields.index("speed"):
        state.speed = round(min(2.0, max(0.5, state.speed + 0.1 * delta)), 1)


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
                state.field_cursor = max(0, min(len(fields) - 1, state.field_cursor + delta))
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
    if 0 <= field_row < num_fields:
        state.field_cursor = field_row
        if field_row == lf_idx and state.selected_file:
            return "launch"
        return "continue"
    if vf_idx is not None:
        names, sel = _current_voice_list(state)
        max_visible = max(1, (height - 3) - num_fields - 5)
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
    if isinstance(key, tuple) and key[0] == "mouse":
        return _handle_mouse(state, key[1][0], key[1][1], key[1][2])

    is_char = isinstance(key, tuple)
    char = key[1] if is_char else None

    # The Folder field is a path-typing field: while it is focused, letters
    # are path text and must not be read as navigation or 'q'-to-quit.
    if state.pane_focus == "settings":
        folder_idx = _folder_field_idx(state)
        if folder_idx is not None and state.field_cursor == folder_idx:
            if key == "esc":
                state.folder_draft = ""
                state.error_msg = None
                if state.field_cursor > 0:
                    state.field_cursor -= 1
                else:
                    state.pane_focus = "files"
            elif key == "backspace":
                state.folder_draft = state.folder_draft[:-1]
            elif key == "enter":
                _commit_folder(state)
            elif key == "tab":
                state.folder_draft = ""
                state.error_msg = None
                state.pane_focus = "files"
            elif is_char:
                state.folder_draft += char
                state.error_msg = None
            return "continue"

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
            _leave_folder_if_idle(state)
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
    lf_idx = _launch_field_idx(state)
    model = state.models[state.model_idx] if state.models else "none"
    folder_idx = _folder_field_idx(state)
    model_idx = fields.index("model")
    lang_idx = fields.index("lang") if "lang" in fields else None
    speed_idx = fields.index("speed") if "speed" in fields else None

    if key == "up" or char == "k":
        if vf_idx is not None and state.field_cursor == vf_idx:
            _move_voice(state, -1)
        else:
            state.field_cursor = max(0, state.field_cursor - 1)
    elif key == "down" or char == "j":
        if vf_idx is not None and state.field_cursor == vf_idx:
            _move_voice(state, 1)
        else:
            state.field_cursor = min(len(fields) - 1, state.field_cursor + 1)
    elif key == "page_up":
        if vf_idx is not None and state.field_cursor == vf_idx:
            _move_voice(state, -10)
        else:
            state.field_cursor = 0
    elif key == "page_down":
        if vf_idx is not None and state.field_cursor == vf_idx:
            _move_voice(state, 10)
        else:
            state.field_cursor = len(fields) - 1
    elif key == "home":
        state.field_cursor = 0
    elif key == "end":
        state.field_cursor = len(fields) - 1
    elif key == "left" or char == "h":
        if state.field_cursor == model_idx:
            _cycle_model(state, -1)
        elif lang_idx is not None and state.field_cursor == lang_idx:
            if model == "kokoro":
                _cycle_kokoro_lang(state, -1)
            else:
                _cycle_edge_lang(state, -1)
            _persist_defaults(state)
        elif speed_idx is not None and state.field_cursor == speed_idx:
            state.speed = max(0.5, round(state.speed - 0.1, 1))
        else:
            state.pane_focus = "files"
    elif key == "right" or char == "l":
        if state.field_cursor == model_idx:
            _cycle_model(state, 1)
        elif lang_idx is not None and state.field_cursor == lang_idx:
            if model == "kokoro":
                _cycle_kokoro_lang(state, 1)
            else:
                _cycle_edge_lang(state, 1)
            _persist_defaults(state)
        elif speed_idx is not None and state.field_cursor == speed_idx:
            state.speed = min(2.0, round(state.speed + 0.1, 1))
        else:
            state.pane_focus = "files"
    elif char in ("-", ","):
        _adjust_field(state, -1)
    elif char in ("+", "=", "."):
        _adjust_field(state, 1)
    elif key == "enter":
        if state.field_cursor == lf_idx and state.selected_file:
            return "launch"
        state.field_cursor = (state.field_cursor + 1) % len(fields)
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


def _make_result(state: MenuState) -> MenuResult:
    model = state.models[state.model_idx] if state.models else "none"
    voice = None
    lang = None
    if model != "none":
        names, idx = _current_voice_list(state)
        if names and 0 <= idx < len(names):
            voice = names[idx][0]
        if model == "kokoro":
            lang = state.kokoro_lang
    return MenuResult(
        file_path=os.path.abspath(state.selected_file) if state.selected_file else "",
        tts_name=model,
        voice=voice,
        lang=lang,
        speed=state.speed,
    )


# ---------------------------------------------------------------------------
# Key loop and entry point
# ---------------------------------------------------------------------------

async def run_key_loop(state: MenuState) -> MenuResult | None:
    """Render + read keys until the user launches or quits."""
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
    try:
        while True:
            try:
                width, height = ui.get_terminal_size()
                sys.stdout.write(render_menu(state, width, height))
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
                    outcome = handle_key(state, key)
                except Exception:
                    outcome = "continue"
                if outcome == "launch":
                    return _make_result(state)
                if outcome == "quit":
                    return None
    finally:
        loop.remove_reader(fd)
        try:
            loop.remove_signal_handler(signal.SIGWINCH)
        except (ValueError, NotImplementedError):
            pass


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

    With guided=True (the `lue ui` shortcut), a first run with no saved default
    folder opens on the Folder field to invite folder + language setup.
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

    # Load persisted user settings (default folder + language).
    prefs = settings.load_settings()
    saved_dir = prefs.get("default_start_dir", "")
    saved_lang = prefs.get("default_language", "")

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
    if "kokoro" in models:
        lang = saved_lang or default_lang or config.TTS_LANGUAGE_CODES.get("kokoro")
        if lang in KOKORO_LANG_CODES:
            state.kokoro_lang = lang
    if "edge" in models and saved_lang in edge_langs:
        state.edge_lang = saved_lang
    _seed_default_voice(state)
    if default_voice:
        _seed_voice_by_name(state, default_voice)

    if preselect_file and os.path.isfile(preselect_file):
        state.selected_file = os.path.abspath(preselect_file)
        state.pane_focus = "settings"
        state.field_cursor = _launch_field_idx(state) or 0
    elif guided and not state.default_dir:
        # First run of `lue ui` with no saved folder: invite the user to set
        # one (then a language). They can Tab to the browser at any time.
        state.guided = True
        state.pane_focus = "settings"
        state.field_cursor = _folder_field_idx(state) or 0

    build_file_rows(state)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # Hide cursor, enable mouse click tracking (SGR), enter alternate screen.
        sys.stdout.write("\033[?25l\033[?1000h\033[?1006h\033[?1049h")
        sys.stdout.flush()
        return await run_key_loop(state)
    finally:
        sys.stdout.write("\033[?1049l\033[?1000l\033[?1006l\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
