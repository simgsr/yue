"""Split a synthesized paragraph audio into per-sentence segments.

CosyVoice2 produces natural, continuous prosody when given a whole paragraph,
but the reader plays one audio file per sentence. This module splits a
paragraph's audio into ``n`` segments at the largest silence gaps, so each
sentence can be played back individually while keeping the paragraph's natural
flow and pauses.
"""

import os

import numpy as np
import soundfile as sf

_FRAME_S = 0.02  # 20ms RMS frames


def _rms(x: np.ndarray, sample_rate: int) -> np.ndarray:
    """Per-frame RMS of a mono float array."""
    frame = int(_FRAME_S * sample_rate)
    n = len(x) // frame
    if n == 0:
        return np.array([0.0])
    x = x[: n * frame].reshape(n, frame)
    return np.sqrt((x ** 2).mean(axis=1))


def measure_segment_speech(
    audio_path: str,
    silence_db: float = -35.0,
) -> tuple[float, float]:
    """Return (lead_silence_s, speech_duration_s) for one audio file.

    The splitter deliberately keeps the sentence-pause silence in each segment
    (so the natural pause is preserved for playback), but that padding inflates
    the file's total duration. The reader highlights words against the actual
    spoken portion, so this measures where the speech starts and how long it
    lasts, ignoring the leading/trailing silence.
    """
    data, sr = sf.read(audio_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    threshold = 10 ** (silence_db / 20.0)
    rms = _rms(data, sr)
    silent = rms < threshold
    nonsil = np.nonzero(~silent)[0]
    total = len(data) / sr
    if len(nonsil) == 0:
        return 0.0, 0.0
    first = nonsil[0] * _FRAME_S
    last = (nonsil[-1] + 1) * _FRAME_S
    lead = first
    speech = last - first
    if speech < 0:
        speech = 0.0
    if speech > total:
        speech = total
    return lead, speech


def split_audio_by_silence(
    audio_path: str,
    n_segments: int,
    out_dir: str,
    sample_rate: int = 24000,
    silence_db: float = -35.0,
    min_gap_s: float = 0.25,
    out_prefix: str = "seg",
) -> list[str]:
    """Split ``audio_path`` into ``n_segments`` files at the largest silences.

    Returns a list of output file paths (one per segment). If the audio cannot
    be split into exactly ``n_segments`` (e.g. too few silences), it falls back
    to proportional splitting by duration.
    """
    data, sr = sf.read(audio_path)
    if data.ndim > 1:
        data = data.mean(axis=1)
    data = data.astype(np.float32)
    frame = int(_FRAME_S * sr)

    threshold = 10 ** (silence_db / 20.0)
    rms = _rms(data, sr)
    silent = rms < threshold

    min_frames = int(min_gap_s / _FRAME_S)
    gaps = []  # (start_frame, end_frame, duration_frames)
    i = 0
    n = len(silent)
    while i < n:
        if silent[i]:
            j = i
            while j < n and silent[j]:
                j += 1
            if j - i >= min_frames:
                gaps.append((i, j, j - i))
            i = j
        else:
            i += 1

    cuts = []
    if len(gaps) >= n_segments - 1:
        gaps_sorted = sorted(gaps, key=lambda g: g[2], reverse=True)
        chosen = gaps_sorted[: n_segments - 1]
        chosen.sort(key=lambda g: g[0])
        for start, end, _ in chosen:
            cuts.append(int((start + end) / 2) * frame)  # frame -> sample
    else:
        cuts = [int((i + 1) * len(data) / n_segments) for i in range(n_segments - 1)]

    bounds = [0] + cuts + [len(data)]
    paths = []
    for k in range(n_segments):
        seg = data[bounds[k]: bounds[k + 1]]
        if len(seg) == 0:
            continue
        out = os.path.join(out_dir, f"{out_prefix}_seg_{k}.wav")
        sf.write(out, seg, sr)
        paths.append(out)
    return paths
