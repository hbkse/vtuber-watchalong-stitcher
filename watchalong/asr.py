"""Japanese speech-to-text for the streamer's voice (faster-whisper, CPU)."""
from __future__ import annotations

import os
from pathlib import Path

from .util import fmt_time, log, read_json, run, write_json

# Bump when the cached transcript format changes, so old caches are redone.
VERSION = 4

GAP = 0.7           # a pause this long (s) after punctuation ends a line
LONG_GAP = 2.0      # a pause this long ends a line anywhere a word can start
STRAY_GAP = 1.0     # an edge word this far from the rest is probably misaligned
STRAY_CHARS = 2     # a piece of a phrase this short is never cut off on its own
MAX_SECONDS = 5.0   # lines longer than this...
MAX_CHARS = 24      # ...or this are split at punctuation (or their biggest pause)
LINGER = 0.4        # keep a line up this long after the last word (s)
MIN_SHOW = 1.0      # never flash a line for less than this (s)
CHUNK = 300         # transcribe in pieces about this long (s)...
CHUNK_SLACK = 20    # ...cut at the quietest moment within this of the mark
PUNCT = tuple("。、？！?!…")
NO_START = tuple("っゃゅょャュョッーんンぁぃぅぇぉ、。？！?!…")  # can't begin a word


def extract_audio(stream: Path, start: float = 0.0, duration: float | None = None):
    """16 kHz mono float32 samples. Decoded here rather than by faster-whisper, whose PyAV
    decoding breaks across PyAV releases."""
    import numpy as np

    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    raw = run(cmd + ["-i", stream, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
              capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def _chunk_bounds(audio, rate: int = 16000) -> list[int]:
    """Sample offsets splitting the audio into ~CHUNK-second pieces, each cut at the quietest
    half second near the boundary, so no word is cut in two."""
    import numpy as np

    win = rate // 2
    bounds = [0]
    while len(audio) - bounds[-1] > (CHUNK + CHUNK_SLACK) * rate:
        lo = bounds[-1] + (CHUNK - CHUNK_SLACK) * rate
        hi = bounds[-1] + (CHUNK + CHUNK_SLACK) * rate
        energy = np.square(audio[lo:hi]).reshape(-1, win).mean(axis=1)
        bounds.append(lo + int(energy.argmin()) * win + win // 2)
    return bounds + [len(audio)]


def transcribe(stream: Path, model_name: str, language: str = "ja", start: float = 0.0,
               duration: float | None = None, cache_dir: Path | None = None) -> list[dict]:
    """Whisper's segments with word timings, in stream time:
    [{"start", "end", "text", "words": [[start, end, word], ...]}]. Turn them into subtitle
    lines with lines_from_segments (kept separate so line timing can be tuned without
    transcribing again).

    The audio is transcribed in independent ~5 minute chunks: decoding hours in one pass
    lets one confused stretch (music, overlapping anime dialogue) swallow minutes of speech
    after it. Chunks are cached in cache_dir, so an interrupted run picks up where it was."""
    from faster_whisper import WhisperModel

    audio = extract_audio(stream, start, duration)
    bounds = _chunk_bounds(audio)
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    log.info("loading whisper model %s (first run downloads it)", model_name)
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         cpu_threads=os.cpu_count() or 4)
    out = []
    for a, b in zip(bounds, bounds[1:]):
        t0, t1 = start + a / 16000, start + b / 16000
        cache = cache_dir / f"{t0:.2f}-{t1:.2f}.json" if cache_dir else None
        if cache and cache.exists():
            out += read_json(cache)
            continue
        segments, _ = model.transcribe(
            audio[a:b],
            language=language,
            vad_filter=True,                   # skip silence, where whisper hallucinates
            condition_on_previous_text=False,  # avoids repetition loops
            beam_size=5,
            word_timestamps=True,              # segment times include surrounding silence
        )
        chunk = [{"start": round(t0 + seg.start, 3), "end": round(t0 + seg.end, 3),
                  "text": seg.text.strip(),
                  "words": [[round(t0 + w.start, 3), round(t0 + w.end, 3), w.word]
                            for w in seg.words or []]} for seg in segments]
        if cache:
            write_json(cache, chunk)
        out += chunk
        log.info("transcribed %s / %s", fmt_time(t1), fmt_time(start + len(audio) / 16000))
    log.info("transcribed %d segments", len(out))
    return out


def _max_dur(word: str) -> float:
    """A generous speaking time for a word; Whisper sometimes stretches a word's alignment
    over the silence next to it."""
    return 0.5 + 0.3 * len(word.strip())


def _chars(words) -> int:
    return sum(len(w[2].strip()) for w in words)


def _cuts(words: list, seg_ends: set[int]) -> list[int]:
    """Indices where a new line starts."""
    cuts = []
    seg_start = 0
    bounds = sorted(seg_ends) + [len(words)]
    for i in range(1, len(words)):
        if i in seg_ends:
            seg_start = i
            if not words[i][2].strip().startswith(NO_START):
                cuts.append(i)
            continue
        if words[i][2].strip().startswith(NO_START):
            continue
        gap = words[i][0] - words[i - 1][1]
        punct = words[i - 1][2].strip().endswith(PUNCT)
        if gap >= LONG_GAP or (gap >= GAP and punct):
            # Whisper sometimes places a word or two of a phrase seconds away from the rest
            # (なん … の魔法これ, さっきのや … つ). Don't cut a stray few characters off their
            # phrase; _span trims the misplaced edge instead.
            seg_end = next(b for b in bounds if b > i)
            if min(_chars(words[seg_start:i]), _chars(words[i:seg_end])) <= STRAY_CHARS and not punct:
                continue
            cuts.append(i)
    return cuts


def _move_fragments(words: list, cuts: list[int]) -> list[int]:
    """Whisper's silence filter joins speech chunks, and a word on a chunk boundary can be
    mapped to the wrong side of the pause, leaving its first character dangling at the end
    of the line before (気 | になる, 言 | 葉で, 百 | キロ). Move such a character across
    the pause, onto the speech it belongs to."""
    out = []
    for k, i in enumerate(cuts):
        prev = out[-1] if out else 0
        nxt = cuts[k + 1] if k + 1 < len(cuts) else len(words)
        last = words[i - 1][2].strip()
        if (words[i][0] - words[i - 1][1] >= STRAY_GAP and len(last) == 1
                and not last.endswith(PUNCT) and (i - 1 == prev or words[i - 2][2].strip().endswith(PUNCT)
                                                   or words[i - 1][0] - words[i - 2][1] >= STRAY_GAP)):
            t = words[i][0]
            words[i - 1] = [t - 0.2, t - 0.05, words[i - 1][2]]
            if i - 1 > prev:
                out.append(i - 1)
            continue  # (if the fragment was a whole line, it simply joins the next)
        out.append(i)
    return out


def _split_long(part: list) -> list[list]:
    """Split a long line at the punctuation nearest its middle (or its biggest pause), so
    subtitles stay readable."""
    start, end = _span(part)  # not counting misplaced edge words
    if len(part) < 2 or (end - start <= MAX_SECONDS and _chars(part) <= MAX_CHARS):
        return [part]
    cands = [i for i in range(1, len(part)) if part[i - 1][2].strip().endswith(PUNCT)
             and not part[i][2].strip().startswith(NO_START)]
    if cands:
        mid = (start + end) / 2
        i = min(cands, key=lambda i: abs(part[i][0] - mid))
    elif end - start > 2 * MAX_SECONDS:
        gaps = [(part[i][0] - part[i - 1][1], i) for i in range(1, len(part))
                if not part[i][2].strip().startswith(NO_START)]
        if not gaps:
            return [part]
        i = max(gaps)[1]
    else:
        return [part]
    return _split_long(part[:i]) + _split_long(part[i:])


def _span(part: list) -> tuple[float, float]:
    """When the speech in these words starts and ends."""
    first, last = part[0], part[-1]
    if len(part) > 1 and part[1][0] - first[1] > STRAY_GAP:   # first word misaligned early
        start = part[1][0] - min(_max_dur(first[2]), 0.15 * len(first[2].strip()) + 0.1)
    else:
        start = max(first[0], first[1] - _max_dur(first[2]))
    if len(part) > 1 and last[0] - part[-2][1] > STRAY_GAP:   # last word misaligned late
        end = part[-2][1] + min(_max_dur(last[2]), 0.15 * len(last[2].strip()) + 0.1)
    else:
        end = min(last[1], last[0] + _max_dur(last[2]))
    return start, max(end, start + 0.1)


def lines_from_segments(segments: list[dict]) -> list[dict]:
    """Subtitle lines [{"start", "end", "text"}] that appear when she starts talking and
    disappear shortly after she stops."""
    words: list[list] = []
    seg_ends: set[int] = set()
    for seg in segments:
        ws = [list(w) for w in seg["words"] if w[2].strip()]
        if not ws and seg["text"]:
            ws = [[seg["start"], seg["end"], seg["text"]]]
        if ws:
            if words:
                seg_ends.add(len(words))
            words += ws
    if not words:
        return []
    cuts = _move_fragments(words, _cuts(words, seg_ends))
    out = []
    for a, b in zip([0] + cuts, cuts + [len(words)]):
        for part in _split_long(words[a:b]):
            text = "".join(w[2] for w in part).strip()
            if text:
                start, end = _span(part)
                out.append({"start": start, "end": end, "text": text})
    out.sort(key=lambda x: x["start"])
    for a, b in zip(out, out[1:] + [None]):
        end = max(a["end"] + LINGER, a["start"] + MIN_SHOW)
        if b is not None:
            end = min(end, b["start"])
        a["start"] = round(a["start"], 2)
        a["end"] = round(max(end, a["start"] + 0.3), 2)
    return out
