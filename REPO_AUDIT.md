# REPO_AUDIT — yue

Onboard audit for `github.com/simgsr/yue` (terminal eBook reader + TTS).
Date: 2026-09-03 · Host: Apple Silicon (arm64, 18 cores, 128 GiB RAM) · macOS Darwin 25.6.0

## Gate 0 — Scope & isolation

- Repo: `https://github.com/simgsr/yue` (user's own, actively developed, no release tags).
- Audited the existing checkout at `/Users/simgsr/Documents/git_project/yue` **read-only**
  (grep/read/glob only; nothing executed before approval). No isolated clone was made
  because the repo is user-owned and the end state is a `.venv` at the repo root, which
  the existing `.envrc` already expects.

## Gate 1 — Static security audit

**VERDICT: PASS**

Checked and clean:
- No `eval`/`exec`/`compile`, no base64/hex obfuscation, no `.pyc`-instead-of-source.
- No exfiltration flow: no secret collection, no `.ssh`/`.aws` access. The only
  `os.environ` writes are HF_HUB telemetry-disable + MPS fallback (`yue/__main__.py:95-102`).
- Network usage is the app's core function: `edge-tts` → Microsoft Edge TTS service;
  optional `kokoro` → HuggingFace model weights (weights, not code; no download-and-execute).
- `subprocess` calls all legitimate: ffplay audio playback, `pkill` stale players,
  `pbcopy` clipboard, ffmpeg presence check. No `sh -c`/`bash -c` with remote URLs.
- All `os.remove` targets scoped to app data dirs (progress files, audio buffers, temp
  guide files) — no system-path destruction.
- Dependencies: all well-known packages, no typosquatting, no active `--index-url`
  overrides, no VCS deps.
- Supply chain: no release tags (own active repo), GPL-3.0 LICENSE present, authors
  consistent (simgsr, superstarryeyes, paulilaaso = upstream Lue).

Minor findings (addressed):
- [LOW] Unpinned deps (`>=X`) — fixed by `requirements.lock` (see Gate 4).
- [LOW] `requirements.txt` carried a commented-out CUDA index URL for the optional
  Kokoro path — replaced with an Apple Silicon (MPS) note.
- [INFO] `.direnv/` was not in `.gitignore` — added (defensive; unused with Template A).

## Gate 2 — direnv + auto-activating venv

- `direnv` 2.37.1 installed; shell hook present in `~/.zprofile`.
- `.envrc` (Template A: `source .venv/bin/activate`) existed and was re-allowed after
  the venv was created.
- Verified in a child shell: `$VIRTUAL_ENV` → `/Users/simgsr/Documents/git_project/yue/.venv`,
  `sys.prefix` resolves into the venv, `import yue` OK.
- `.gitignore` guards `.venv/`, `.env`, `.direnv/`.

## Gate 3 — Apple Silicon dependency audit

Hardware detected at runtime: arm64 · 18 cores · 128 GiB RAM · Homebrew Python 3.14.7.

All core deps installed as **arm64 wheels** (no source builds). No CUDA index URLs in use.
PyTorch is **not** installed (Kokoro is opt-in); MPS availability therefore not verified —
do not assume MPS support. If Kokoro is enabled later, install `torch` from PyPI (arm64
wheels are MPS-capable) and verify `torch.backends.mps.is_available()`.

| package | installed | notes |
|---|---|---|
| python-docx | 1.2.0 | pure Python |
| striprtf | 0.0.33 | pure Python |
| rich | 15.0.0 | pure Python |
| PyMuPDF | 1.28.2 | arm64 wheel |
| Markdown | 3.10.3 | pure Python |
| platformdirs | 4.11.7 | pure Python |
| edge-tts | 7.2.8 | pure Python (network TTS) |
| yue-reader | 0.5.0 | editable install |

## Gate 4 — Controlled install + verification

- Tooling: `uv` 0.12.9 installed via Homebrew.
- Venv: `.venv` created with CPython 3.14.7; `uv pip install -e . -r requirements.txt`
  (bytecode compiled).
- Lockfile: `requirements.lock` generated via `uv pip compile` (56 lines). Dry-run
  reinstall from the lock reports "Would make no changes" — env matches lock exactly.
- Tests: `python -m unittest discover tests` → **5/5 OK** (0.42s).
- Files changed: `requirements.txt` (MPS note), `.gitignore` (`.direnv/`),
  `requirements.lock` (new), `REPO_AUDIT.md` (this file).

## How the env activates

`cd /Users/simgsr/Documents/git_project/yue` → direnv loads `.envrc` → activates `.venv`.
First `cd` after a fresh clone is slow (venv creation); subsequent `cd`s are instant.

## Risks left open

- **Kokoro/TTS extras not installed** — opt-in by design. Enabling pulls in `torch`
  (~2 GB+); verify MPS at that point.
- **Python 3.14** — deps resolved cleanly today, but 3.14 is newer than the
  `>=3.10` floor; if a future dep lags, pin a managed 3.12/3.13 via `uv python install`.
- **Unpinned source files** — `pyproject.toml`/`requirements.txt` still use `>=X`;
  the lockfile pins the resolved set, but a future `uv pip install -e .` re-resolves
  unless the lock is used.
- **No release tags** — nothing to verify a published release against; not a risk for
  local dev, but worth tagging before any distribution.
