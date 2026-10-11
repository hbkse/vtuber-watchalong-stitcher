from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from .util import run


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: str  # exact rational, e.g. "60/1" or "24000/1001"
    duration: float

    @property
    def fps_float(self) -> float:
        return float(Fraction(self.fps))


def _ffprobe(path: Path) -> dict:
    out = run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def probe(path: Path) -> VideoInfo:
    data = _ffprobe(path)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    return VideoInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        fps=video.get("r_frame_rate") or video["avg_frame_rate"],
        duration=float(data["format"]["duration"]),
    )


def subtitle_streams(path: Path) -> list[dict]:
    return [s for s in _ffprobe(path)["streams"] if s["codec_type"] == "subtitle"]


def has_filter(name: str) -> bool:
    out = run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def _darkest_channel(rgb: np.ndarray) -> np.ndarray:
    """Collapse RGB to the per-pixel minimum. Unlike grayscale this keeps colored text
    (e.g. light blue digits on a white box) dark against white, which OCR needs."""
    return rgb.min(axis=2)


def grab_frame(path: Path, t: float, width: int, height: int) -> np.ndarray:
    """One frame at time t, as a single OCR-friendly channel."""
    raw = run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    ).stdout
    return _darkest_channel(np.frombuffer(raw, np.uint8)[: width * height * 3].reshape(height, width, 3))


def iter_frames(path: Path, start: float, duration: float, rate: float,
                crop: tuple[int, int, int, int], scale_to: tuple[int, int] | None = None,
                threads: int = 0):
    """Yield (stream_time, single-channel crop) sampled at `rate` fps, optionally rescaled."""
    # ffmpeg rounds crops of 4:2:0 video to even sizes; an odd size here would make every
    # frame we slice off the pipe one row off from the last.
    x, y, w, h = (v // 2 * 2 for v in crop)
    vf = f"fps={rate},crop={w}:{h}:{x}:{y}"
    if scale_to:
        w, h = scale_to
        vf += f",scale={w}:{h}:flags=area"
    proc = subprocess.Popen(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-threads", str(threads),
         "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path), "-vf", vf,
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    size = w * h * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield start + i / rate, _darkest_channel(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
            i += 1
    finally:
        proc.stdout.close()
        proc.wait()
