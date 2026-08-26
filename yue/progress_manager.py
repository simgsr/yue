"""Reading progress management for the Yue eBook reader."""

import os
import json
import re
import glob
import hashlib
from . import config


def get_progress_file_path(book_title):
    """
    Generate the file path for storing reading progress.
    
    Args:
        book_title: Title of the book
        
    Returns:
        str: Full path to the progress file
    """
    safe_title = re.sub(r'[^A-Za-z0-9]+', '', book_title)
    if not safe_title:
        # All non-ASCII titles (e.g. Chinese) sanitize to nothing, which would
        # produce a hidden ".progress.json" that the recent-books glob misses
        # and that collides across every such book. Fall back to a short hash
        # of the title so the file is non-empty, unique, and visible.
        safe_title = hashlib.md5(book_title.encode('utf-8')).hexdigest()[:12]
    return os.path.join(config.PROGRESS_FILE_DIR, f"{safe_title}.progress.json")

def migrate_legacy_progress():
    """Rename the legacy hidden '.progress.json' to its proper per-book file.

    Before the hash-based naming, every all-non-ASCII (e.g. Chinese) title
    sanitized to an empty string, so all such books shared a single hidden
    '.progress.json' that the recent-books glob never matched. If that file
    still exists, move it to the correct name for the book it records so the
    progress is kept and the book shows up in the recent list.
    """
    legacy = os.path.join(config.PROGRESS_FILE_DIR, ".progress.json")
    if not os.path.exists(legacy):
        return
    try:
        with open(legacy, 'r', encoding='utf-8') as f:
            data = json.load(f)
        original_path = data.get("original_file_path")
        if not original_path:
            return
        title = os.path.splitext(os.path.basename(original_path))[0]
        new_path = get_progress_file_path(title)
        if new_path != legacy and not os.path.exists(new_path):
            os.rename(legacy, new_path)
    except (json.JSONDecodeError, IOError, OSError):
        pass

def remove_progress_for_path(file_path):
    """Delete any progress file whose original_file_path matches `file_path`.

    Used when a book can no longer be read (e.g. no extractable text), so it
    stops appearing in the recent-books list. Returns the number of files
    removed.
    """
    target = os.path.abspath(file_path)
    removed = 0
    for pf in glob.glob(os.path.join(config.PROGRESS_FILE_DIR, "*.progress.json")):
        try:
            with open(pf, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if os.path.abspath(data.get("original_file_path", "")) == target:
                os.remove(pf)
                removed += 1
        except (json.JSONDecodeError, IOError, OSError):
            continue
    return removed

def load_progress(progress_file):
    """
    Load basic reading progress from file.
    
    Args:
        progress_file: Path to the progress file
        
    Returns:
        tuple: (chapter_idx, paragraph_idx, sentence_idx)
    """
    if os.path.exists(progress_file):
        with open(progress_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                return data.get("c", 0), data.get("p", 0), data.get("s", 0)
            except json.JSONDecodeError:
                return 0, 0, 0
    return 0, 0, 0

def load_extended_progress(progress_file):
    """
    Load extended reading progress including UI state.
    
    Args:
        progress_file: Path to the progress file
        
    Returns:
        dict: Progress data with reading position and UI state
    """
    default_progress = {
        "c": 0, "p": 0, "s": 0,
        "scroll_offset": 0,
        "tts_enabled": True,
        "auto_scroll_enabled": True,
        "speed_reading_enabled": False,
        "manual_scroll_anchor": None,
        "playback_speed": 1.0
    }
    
    if not os.path.exists(progress_file):
        return default_progress
        
    try:
        with open(progress_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return {
                "c": data.get("c", 0),
                "p": data.get("p", 0), 
                "s": data.get("s", 0),
                "scroll_offset": data.get("scroll_offset", 0),
                "tts_enabled": data.get("tts_enabled", True),
                "auto_scroll_enabled": data.get("auto_scroll_enabled", True),
                "speed_reading_enabled": data.get("speed_reading_enabled", False),
                "manual_scroll_anchor": data.get("manual_scroll_anchor", None),
                "playback_speed": data.get("playback_speed", 1.0)
            }
    except (json.JSONDecodeError, IOError):
        return default_progress

def save_progress(progress_file, chapter_idx, paragraph_idx, sentence_idx):
    """
    Save basic reading progress to file.
    
    Args:
        progress_file: Path to the progress file
        chapter_idx: Current chapter index
        paragraph_idx: Current paragraph index
        sentence_idx: Current sentence index
    """
    progress = {"c": chapter_idx, "p": paragraph_idx, "s": sentence_idx}
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def save_extended_progress(progress_file, chapter_idx, paragraph_idx, sentence_idx, 
                          scroll_offset, tts_enabled, auto_scroll_enabled, manual_scroll_anchor=None, original_file_path=None, playback_speed=1.0, percentage=0.0, speed_reading_enabled=False):
    """
    Save extended reading progress including UI state.
    
    Args:
        progress_file: Path to the progress file
        chapter_idx: Current chapter index
        paragraph_idx: Current paragraph index
        sentence_idx: Current sentence index
        scroll_offset: Current scroll position
        tts_enabled: Whether TTS is enabled
        auto_scroll_enabled: Whether auto-scroll is enabled
        manual_scroll_anchor: Manual scroll anchor position (optional)
        original_file_path: Original path to the eBook file (optional)
        playback_speed: Audio playback speed
        percentage: Completion percentage (0.0 to 100.0)
        speed_reading_enabled: Whether speed reading mode is enabled
    """
    progress = {
        "c": chapter_idx,
        "p": paragraph_idx, 
        "s": sentence_idx,
        "scroll_offset": float(scroll_offset),
        "tts_enabled": bool(tts_enabled),
        "auto_scroll_enabled": bool(auto_scroll_enabled),
        "speed_reading_enabled": bool(speed_reading_enabled),
        "playback_speed": float(playback_speed),
        "completion_percentage": float(percentage)
    }
    if manual_scroll_anchor:
        progress["manual_scroll_anchor"] = manual_scroll_anchor
    if original_file_path:
        progress["original_file_path"] = original_file_path
    
    # Save percentage if provided (default to 0.0 if not in args, but we will add it to args)
    # Note: The function signature will be updated in the next step to include percentage.
    # For now, we'll just add it if passed in kwargs or update the signature.
    # Actually, I should update the signature in the same edit.
        
    with open(progress_file, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)

def get_recent_books(limit=5):
    """
    Get a list of recently read books.
    
    Args:
        limit: Maximum number of books to return
        
    Returns:
        list: List of dicts containing title, path, and percentage
    """
    migrate_legacy_progress()
    progress_files = glob.glob(os.path.join(config.PROGRESS_FILE_DIR, "*.progress.json"))
    
    # Sort by modification time (newest first)
    progress_files.sort(key=os.path.getmtime, reverse=True)
    
    recent_books = []
    for pf in progress_files:
        if len(recent_books) >= limit:
            break
            
        try:
            with open(pf, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            original_path = data.get("original_file_path")
            if not original_path or not os.path.exists(original_path):
                continue
                
            # Derive title from filename if not stored (we don't store title currently, so use filename)
            title = os.path.basename(original_path)
            # Remove extension
            title = os.path.splitext(title)[0]
            
            percentage = data.get("completion_percentage", 0.0)
            
            recent_books.append({
                "title": title,
                "path": original_path,
                "percentage": percentage
            })
            
        except (json.JSONDecodeError, IOError):
            continue
            
    return recent_books

def validate_and_set_progress(chapters, progress_file, c, p, s):
    """
    Validate reading progress against document structure.
    
    Args:
        chapters: Document chapters structure
        progress_file: Path to progress file (for cleanup if invalid)
        c: Chapter index to validate
        p: Paragraph index to validate
        s: Sentence index to validate
        
    Returns:
        tuple: Valid (chapter_idx, paragraph_idx, sentence_idx)
    """
    try:
        paragraph = chapters[c][p]
        sentences = re.split(r'(?<=[.!?])\s+', paragraph)
        _ = sentences[s]  # Test if sentence exists
        return c, p, s
    except IndexError:
        # Invalid progress, reset to beginning
        if os.path.exists(progress_file):
            os.remove(progress_file)
        return 0, 0, 0

def find_most_recent_book():
    """
    Find the most recently updated progress file and return the original file path.
    
    Returns:
        str or None: Path to the most recently read book, or None if no books found
    """
    migrate_legacy_progress()
    progress_files = glob.glob(os.path.join(config.PROGRESS_FILE_DIR, "*.progress.json"))
    
    if not progress_files:
        return None
    
    # Find the most recently modified progress file
    most_recent_file = max(progress_files, key=os.path.getmtime)
    
    try:
        with open(most_recent_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            original_path = data.get("original_file_path")
            
            # Check if the original file still exists
            if original_path and os.path.exists(original_path):
                return original_path
                
    except (json.JSONDecodeError, IOError):
        pass
    
    return None
