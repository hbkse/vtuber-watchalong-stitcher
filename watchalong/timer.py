"""Find and read the on-screen watchalong timer (HH:MM:SS that counts up per episode)."""
from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .media import VideoInfo, grab_frame, iter_frames
from .util import log

TIME_RE = re.compile(r"(?:(\d{1,2})[:.])?(\d{1,2})[:.](\d{2})")

# Deliberately not a divisor of 1 Hz so samples land at varying phases within each timer
# second; that lets align.py pin each episode's offset well below one second.
SAMPLE_RATE = 1.9


def parse_timer(text: str) -> int | None:
    text = text.replace(" ", "").replace("O", "0").replace("o", "0")
    m = TIME_RE.search(text)
    if not m:
        return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if h > 9 or mm > 59 or ss > 59:  # an episode timer never reaches 10 hours
        return None
    return h * 3600 + mm * 60 + ss


DETECT_W = 480      # analysis resolution for finding the timer
DETECT_FPS = 10     # samples per second while looking for 1 Hz ticks
DETECT_SECONDS = 8


def _ticking_box(path: Path, t: float, info: VideoInfo) -> tuple[int, int, int, int] | None:
    """Find the timer's seconds digit near stream time t, in full-res pixels.

    Full-frame OCR is unreliable on stylized overlays, so look for the timer's motion
    signature instead: its seconds digit changes exactly once per second and is static in
    between, while the background never changes and the model moves all the time.
    """
    scale = info.width / DETECT_W
    h = int(round(info.height / scale / 2)) * 2
    frames = np.stack([f for _, f in iter_frames(
        path, t, DETECT_SECONDS, DETECT_FPS, (0, 0, info.width, info.height), scale_to=(DETECT_W, h))])
    if len(frames) < DETECT_FPS * 4:
        return None
    changed = np.abs(np.diff(frames.astype(np.int16), axis=0)) > 40   # (transitions, h, w)
    counts = changed.sum(axis=0)
    n_ticks = len(changed) / DETECT_FPS
    cand = (counts >= 3) & (counts <= n_ticks + 2)

    # The tick lands on the same sample phase every second; find it.
    per_transition = (changed & cand).sum(axis=(1, 2))
    phase_strength = [per_transition[p::DETECT_FPS].sum() for p in range(DETECT_FPS)]
    phase = int(np.argmax(phase_strength))
    on_phase = np.zeros(len(changed), bool)
    for d in (-1, 0, 1):
        on_phase[(phase + d) % DETECT_FPS::DETECT_FPS] = True
    off_phase_changes = changed[~on_phase].sum(axis=0)
    good = cand & (off_phase_changes == 0)

    ys, xs = np.nonzero(good)
    if len(xs) < 10:
        return None
    cx, cy = np.median(xs), np.median(ys)
    near = (np.abs(xs - cx) < 30) & (np.abs(ys - cy) < 20)
    if near.sum() < 10:
        return None
    xs, ys = xs[near], ys[near]
    x0, x1 = xs.min() * scale, (xs.max() + 1) * scale
    y0, y1 = ys.min() * scale, (ys.max() + 1) * scale
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0)


def _read_box(frame: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """OCR around a seconds-digit box; return the tight box of the whole HH:MM:SS text."""
    import pytesseract

    x, y, w, h = box
    H, W = frame.shape
    # The ticking box is the last digit; "HH:MM:SS" is ~8 digit widths wide, to its left.
    # Stay tight vertically: overlays often frame the timer (film strips, borders) and
    # anything extra on the line derails single-line OCR.
    x0, x1 = max(0, int(x - 7.5 * w)), min(W, int(x + w + 0.3 * w))
    y0, y1 = max(0, int(y - 0.12 * h)), min(H, int(y + h + 0.12 * h))
    crop = frame[y0:y1, x0:x1]
    d = pytesseract.image_to_data(crop, config="--psm 7 -c tessedit_char_whitelist=0123456789:",
                                  output_type=pytesseract.Output.DICT)
    for i, text in enumerate(d["text"]):
        if TIME_RE.fullmatch(text.strip()) and parse_timer(text) is not None:
            bx, by, bw, bh = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
            pad = bh // 8 + 2
            gx0, gy0 = max(0, x0 + bx - pad), max(0, y0 + by - pad)
            gx1, gy1 = min(W, x0 + bx + bw + pad), min(H, y0 + by + bh + pad)
            return gx0, gy0, gx1 - gx0, gy1 - gy0
    if read_timer(crop) is not None:  # tesseract split the words, but the crop reads fine
        return x0, y0, x1 - x0, y1 - y0
    return None


def detect_roi(path: Path, info: VideoInfo, attempts: int = 12,
               workers: int | None = None) -> tuple[int, int, int, int]:
    """Locate the on-screen timer. Tries windows spread across the stream (some will be
    chatting with a frozen timer) and needs two windows to agree."""
    times = [float(t) for t in np.linspace(info.duration * 0.1, info.duration * 0.9, attempts)]
    found = []
    for t in times:
        box = _ticking_box(path, t, info)
        if box is None:
            continue
        roi = _read_box(grab_frame(path, t + 0.5, info.width, info.height), box)
        if roi is None:
            continue
        log.debug("timer candidate at %.0fs: %s", t, roi)
        for other in found:
            if abs(other[0] - roi[0]) < 40 and abs(other[1] - roi[1]) < 40:
                roi = (min(roi[0], other[0]), min(roi[1], other[1]),
                       max(roi[0] + roi[2], other[0] + other[2]) - min(roi[0], other[0]),
                       max(roi[1] + roi[3], other[1] + other[3]) - min(roi[1], other[1]))
                log.info("timer found at x=%d y=%d w=%d h=%d", *roi)
                return roi
        found.append(roi)
    raise SystemExit("couldn't find an on-screen timer; pass --timer-roi X,Y,W,H")


def read_timer(crop: np.ndarray) -> int | None:
    import pytesseract
    from PIL import Image

    img = Image.fromarray(crop)
    if img.height < 64:  # tesseract likes text ~30px+ tall
        scale = 64 / img.height
        img = img.resize((int(img.width * scale), 64), Image.LANCZOS)
    text = pytesseract.image_to_string(
        img, config="--psm 7 -c tessedit_char_whitelist=0123456789:")
    return parse_timer(text)


def _scan_chunk(args) -> list[tuple[float, int | None]]:
    path, start, duration, roi = args
    # The scan already runs one OCR process per core; tesseract's own OpenMP threading on top
    # of that oversubscribes the CPU badly (measured ~1.5x slower). Set only in the worker
    # processes: in the main process it would also throttle Whisper to one core.
    os.environ["OMP_THREAD_LIMIT"] = "1"
    return [(round(t, 3), read_timer(crop))
            for t, crop in iter_frames(path, start, duration, SAMPLE_RATE, roi, threads=1)]


def scan(path: Path, info: VideoInfo, roi: tuple[int, int, int, int],
         workers: int | None = None, chunk: float = 300.0) -> list[tuple[float, int | None]]:
    """Read the timer across the whole stream. Returns [(stream_time, timer_seconds | None)]."""
    workers = workers or os.cpu_count()
    tasks = []
    t = 0.0
    while t < info.duration:
        tasks.append((path, t, min(chunk, info.duration - t), roi))
        t += chunk
    readings: list[tuple[float, int | None]] = []
    with ProcessPoolExecutor(workers) as ex:
        for i, part in enumerate(ex.map(_scan_chunk, tasks), 1):
            readings += part
            if i % 5 == 0 or i == len(tasks):
                log.info("timer scan %d/%d chunks", i, len(tasks))
    ok = sum(v is not None for _, v in readings)
    log.info("timer readable in %d/%d sampled frames", ok, len(readings))
    return readings
