import sys
import subprocess
import json
import os
import re
import time

# Default keyboard shortcuts
DEFAULT_KEYBOARD_SHORTCUTS = {
    "navigation": {
        "move_to_top_visible": "t",
        "move_to_beginning": "y",
        "move_to_end": "b"
    },
    "tts_controls": {
        "play_pause": " ",
        "decrease_speed": ",",
        "increase_speed": ".",
        "decrease_temperature": "c",
        "increase_temperature": "w"
    },
    "display_controls": {},
    "application": {
        "quit": "q",
        "select_menu_item": "\n"
    }
}

# Global variable to store loaded keyboard shortcuts
KEYBOARD_SHORTCUTS = DEFAULT_KEYBOARD_SHORTCUTS

def _matches_shortcut(data, shortcut):
    """Check if input data matches a shortcut (string or list)."""
    if isinstance(shortcut, list):
        return data in shortcut
    return data == shortcut

def load_keyboard_shortcuts(file_path=None):
    """Load keyboard shortcuts from a JSON file or use defaults.
    
    If file_path is None, the function will attempt to load from the default locations.
    """
    global KEYBOARD_SHORTCUTS
    
    # If no file path provided, use the default file
    if not file_path:
        file_path = os.path.join(os.path.dirname(__file__), 'keys_default.json')
    
    try:
        with open(file_path, 'r') as f:
            KEYBOARD_SHORTCUTS = json.load(f)
    except Exception:
        # Fallback to default shortcuts if file cannot be loaded
        KEYBOARD_SHORTCUTS = DEFAULT_KEYBOARD_SHORTCUTS

def _process_escape_sequence(reader, seq):
    """Process a parsed CSI or escape sequence (such as arrow keys)."""
    if not seq:
        return

    last_char = seq[-1]
    cmd = None

    if last_char == 'A':
        reader.left_arrow_chapter_start = False
        cmd = 'scroll_up' if (reader.show_recent_menu or reader.show_chapter_index) else 'prev_paragraph'
    elif last_char == 'B':
        reader.left_arrow_chapter_start = False
        cmd = 'scroll_down' if (reader.show_recent_menu or reader.show_chapter_index) else 'next_paragraph'
    elif last_char == 'C':
        reader.left_arrow_chapter_start = False
        if not (reader.show_recent_menu or reader.show_chapter_index):
            cmd = 'next_chapter'
    elif last_char == 'D':
        if not (reader.show_recent_menu or reader.show_chapter_index):
            # First press jumps to the start of the current chapter; a second
            # press moves back to the previous chapter.
            if reader.left_arrow_chapter_start:
                reader.left_arrow_chapter_start = False
                cmd = 'prev_chapter'
            else:
                reader.left_arrow_chapter_start = True
                cmd = 'move_to_chapter_start'
    elif seq == '\x1b[5~':
        cmd = 'scroll_page_up'
    elif seq == '\x1b[6~':
        cmd = 'scroll_page_down'
    elif seq in ('\x1b[1~', '\x1b[H'):
        cmd = 'move_to_beginning'
    elif seq in ('\x1b[4~', '\x1b[F'):
        cmd = 'move_to_end'

    if cmd:
        if cmd in ('prev_paragraph', 'next_paragraph', 'prev_sentence', 'next_sentence'):
            _kill_audio_immediately(reader)
        reader.post_command(cmd)


def _process_mouse_sequence(reader, sequence):
    """Process a mouse escape sequence."""
    if not sequence or len(sequence) <= 3:
        return

    reader.left_arrow_chapter_start = False

    mouse_part = sequence[3:]
    if mouse_part.endswith('M') or mouse_part.endswith('m'):
        try:
            parts = mouse_part[:-1].split(';')
            if len(parts) >= 3:
                button = int(parts[0])
                x_pos = int(parts[1])
                y_pos = int(parts[2])

                if mouse_part.endswith('M'):
                    if button == 0:
                        if reader.show_chapter_index:
                            reader.post_command(('chapter_click', (x_pos, y_pos)))
                            return

                        if reader._is_click_on_subtitle(x_pos, y_pos):
                            if reader._handle_subtitle_click(x_pos, y_pos):
                                return

                        if reader._is_click_on_progress_bar(x_pos, y_pos):
                            if reader._handle_progress_bar_click(x_pos, y_pos):
                                return

                        if not reader._is_click_on_text(x_pos, y_pos):
                            return

                        if hasattr(reader, 'pending_restart_task') and reader.pending_restart_task and not reader.pending_restart_task.done():
                            reader.pending_restart_task.cancel()

                        _kill_audio_immediately(reader)
                        reader.post_command(('click_jump', (x_pos, y_pos)))
                    elif button == 64:
                        if reader.auto_scroll_enabled:
                            reader.auto_scroll_enabled = False
                        reader.post_command('wheel_scroll_up')
                    elif button == 65:
                        if reader.auto_scroll_enabled:
                            reader.auto_scroll_enabled = False
                        reader.post_command('wheel_scroll_down')
        except (ValueError, IndexError):
            pass


def _process_normal_key(reader, data):
    """Process a standard non-escape key press."""
    nav_shortcuts = KEYBOARD_SHORTCUTS.get("navigation", {})
    tts_shortcuts = KEYBOARD_SHORTCUTS.get("tts_controls", {})
    app_shortcuts = KEYBOARD_SHORTCUTS.get("application", {})

    if _matches_shortcut(data, app_shortcuts.get("quit", "q")):
        reader.running = False
        reader.command_received_event.set()
        return

    # Any non-arrow key cancels the left-arrow's two-stage chapter behaviour.
    reader.left_arrow_chapter_start = False

    cmd = None
    if _matches_shortcut(data, app_shortcuts.get("select_menu_item", "\n")) or data == '\r':
        cmd = 'select_menu_item'
    elif _matches_shortcut(data, tts_shortcuts.get("play_pause", " ")):
        cmd = 'pause'
    elif _matches_shortcut(data, nav_shortcuts.get("move_to_top_visible", "t")):
        cmd = 'move_to_top_visible'
    elif _matches_shortcut(data, nav_shortcuts.get("move_to_beginning", "y")):
        cmd = 'move_to_beginning'
    elif _matches_shortcut(data, nav_shortcuts.get("move_to_end", "b")):
        cmd = 'move_to_end'
    elif _matches_shortcut(data, tts_shortcuts.get("decrease_speed", ",")):
        cmd = 'decrease_speed'
    elif _matches_shortcut(data, tts_shortcuts.get("increase_speed", ".")):
        cmd = 'increase_speed'
    elif _matches_shortcut(data, tts_shortcuts.get("decrease_temperature", "c")):
        cmd = 'decrease_temperature'
    elif _matches_shortcut(data, tts_shortcuts.get("increase_temperature", "w")):
        cmd = 'increase_temperature'

    if cmd:
        reader.post_command(cmd)


def process_input(reader):
    """Process user input from stdin."""
    try:
        data_bytes = os.read(sys.stdin.fileno(), 1024)
        if not data_bytes:
            return

        chars = data_bytes.decode('utf-8', errors='ignore')
        now = time.time()

        if getattr(reader, 'esc_start_time', None) is not None:
            if now - reader.esc_start_time > 0.05:
                reader.mouse_sequence_buffer = ""
                reader.esc_start_time = None

        if not hasattr(reader, 'mouse_sequence_buffer'):
            reader.mouse_sequence_buffer = ""

        if not reader.mouse_sequence_buffer and chars.startswith('\x1b'):
            reader.esc_start_time = now
        elif reader.mouse_sequence_buffer.startswith('\x1b') and getattr(reader, 'esc_start_time', None) is None:
            reader.esc_start_time = now

        reader.mouse_sequence_buffer += chars

        while reader.mouse_sequence_buffer:
            buf = reader.mouse_sequence_buffer

            if not buf.startswith('\x1b'):
                reader.mouse_sequence_buffer = buf[1:]
                _process_normal_key(reader, buf[0])
                continue

            if len(buf) == 1:
                # A lone ESC could be the ESC key or the start of an arrow /
                # mouse sequence arriving in later reads. Give it a short grace
                # period, then treat it as ESC if nothing followed.
                loop = getattr(reader, 'loop', None)
                if loop is not None:
                    try:
                        loop.call_later(0.04, _process_lone_escape, reader)
                    except Exception:
                        pass
                break

            second_char = buf[1]
            if second_char not in ('[', 'O'):
                reader.mouse_sequence_buffer = buf[1:]
                reader.esc_start_time = None
                continue

            if buf.startswith('\x1b[<'):
                match = re.search(r'[Mm]', buf)
                if match:
                    end_idx = match.end()
                    seq = buf[:end_idx]
                    reader.mouse_sequence_buffer = buf[end_idx:]
                    reader.esc_start_time = None
                    _process_mouse_sequence(reader, seq)
                    continue
                if len(buf) > 64:
                    reader.mouse_sequence_buffer = ""
                    reader.esc_start_time = None
                break

            if second_char == 'O':
                if len(buf) >= 3:
                    seq = buf[:3]
                    reader.mouse_sequence_buffer = buf[3:]
                    reader.esc_start_time = None
                    _process_escape_sequence(reader, seq)
                    continue
                break

            match = re.search(r'[\x40-\x7E]', buf[2:])
            if match:
                end_idx = match.start() + 3
                seq = buf[:end_idx]
                reader.mouse_sequence_buffer = buf[end_idx:]
                reader.esc_start_time = None
                _process_escape_sequence(reader, seq)
                continue

            if len(buf) > 32:
                reader.mouse_sequence_buffer = ""
                reader.esc_start_time = None
            break
    except Exception:
        pass


def _process_lone_escape(reader):
    """Resolve a standalone ESC key (no arrow/mouse sequence followed)."""
    try:
        if reader.mouse_sequence_buffer == '\x1b':
            reader.mouse_sequence_buffer = ""
            reader.esc_start_time = None
            reader.left_arrow_chapter_start = False
            # ESC re-opens the start menu (book/TTS picker). Any open overlay
            # is closed by that command.
            reader.post_command('open_start_menu')
    except Exception:
        pass


def _kill_audio_immediately(reader):
    """Kill audio playback immediately."""
    for process in reader.playback_processes[:]:
        try:
            process.kill()
        except (ProcessLookupError, AttributeError):
            pass
    try:
        subprocess.run(['pkill', '-f', 'ffplay'], check=False, 
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
