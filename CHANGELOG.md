# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Re-bound the documented display keys: `v` cycles UI modes, `w` cycles word
  highlighting, `s` toggles sentence highlighting. The handlers were live but
  the input handler never mapped the keys, so they did nothing.
- Made the exported EPUB `dc:identifier` deterministic (md5 of the title
  instead of Python's salted `hash()`), so the same book exports with the
  same identifier on every run.
- Aligned the start-menu speed cap with the reader: speed now goes up to
  3.0x in the menu, matching the 1x–3x range the reader and README document.

### Removed
- Dead text-selection/copy feature (selection state was never activated, so
  the copy path was unreachable).
- Orphaned functions with no call sites: `save_progress`/`load_progress`,
  `validate_timing_data`, `_is_footnote_reference`,
  `_is_paragraph_near_current_reading`, `_find_char_position_at_click`,
  `_is_click_in_selection`, `_strip_rich_markup`,
  `_should_token_be_highlighted`, `_extract_core_word`.
- Dead `c`/`w` TTS-temperature key bindings (left over from removed TTS
  backends) and the stale developer-note comments in `progress_manager.py`.

### Changed
- Consolidated the triplicated `generate_audio_with_timing` into a shared
  `_finalize_timing_data` helper on `TTSBase`, removing the
  `sys.path.insert(0, ...)` import hacks.
- Moved Kokoro's module-level `warnings`/`HF_HUB_*` environment side effects
  into `initialize()`, so importing the backend is side-effect free.
- Standard install now uses `requirements.lock` for a reproducible dependency
  set.

## [0.5.0] - 2026-09-03

Initial release of Yue (fork of Lue).
