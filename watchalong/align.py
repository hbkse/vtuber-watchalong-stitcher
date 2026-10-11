"""Turn raw timer readings into a timeline of what to show at every moment of the stream.

The on-screen timer counts up from 00:00:00 for each episode. From readings
(stream_time, timer_value) we find:
  - "play" runs: the timer ticks along with the stream, so anime_time = stream_time - offset
  - "hold" runs: the timer is frozen (a pause, or waiting before/after an episode)
A play run that starts near 0 after a later timer value means a new episode; a play run
that picks up where the previous one stopped is the same episode after a pause.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .util import fmt_time

OFFSET_TOL = 1.2      # max jitter of (stream_time - timer) within a play run, seconds
MAX_GAP = 4.0         # unreadable stretch that still counts as the same run, seconds
RESET_MAX = 30        # a new episode's first timer reading is at most this
START_SNAP = 3        # if an episode's first reading is <= this, it started at anime time 0


class AlignmentError(Exception):
    pass


@dataclass
class Run:
    kind: str | None  # "play" | "hold" | None (undecided)
    pts: list[tuple[float, int]] = field(default_factory=list)

    @property
    def t0(self) -> float:
        return self.pts[0][0]

    @property
    def t1(self) -> float:
        return self.pts[-1][0]

    @property
    def v0(self) -> int:
        return self.pts[0][1]

    @property
    def v1(self) -> int:
        return self.pts[-1][1]

    @property
    def anchor(self) -> float:
        return self.t0 - self.v0

    def offset(self) -> float:
        """Stream time at which the timer read 0.

        Each reading constrains the offset to (t - v - 1, t - v]. Intersecting all of them
        (trimming a couple of percent for OCR/frame-timing noise) gives sub-second precision.
        """
        ds = sorted(t - v for t, v in self.pts)
        n = len(ds)
        k = int(n * 0.02)
        hi, lo = ds[k], ds[n - 1 - k] - 1
        return (lo + hi) / 2 if lo <= hi else ds[n // 2] - 0.5

    def accepts(self, t: float, v: int) -> bool:
        if t - self.t1 > MAX_GAP:
            return False
        if self.kind == "hold":
            return v == self.v0
        if self.kind == "play":
            return v >= self.v1 and abs((t - v) - self.anchor) <= OFFSET_TOL
        # undecided: same value again, or ticking forward in step with the stream
        return v == self.v0 or (v > self.v0 and abs((t - v) - self.anchor) <= OFFSET_TOL)

    def add(self, t: float, v: int) -> None:
        self.pts.append((t, v))
        if self.kind is None:
            if v > self.v0:
                self.kind = "play"
            elif t - self.t0 > 1.5:
                self.kind = "hold"


def build_runs(readings) -> list[Run]:
    runs: list[Run] = []
    cur: Run | None = None
    for t, v in readings:
        if v is None:
            continue
        if cur is not None and cur.accepts(t, v):
            cur.add(t, v)
        elif len(runs) >= 2 and len(cur.pts) <= 2 and runs[-2].accepts(t, v):
            # cur was a misread blip; drop it and resume the run it interrupted
            runs.pop()
            cur = runs[-1]
            cur.add(t, v)
        else:
            cur = Run(None, [(t, v)])
            runs.append(cur)

    # Drop OCR noise: runs too short to be real.
    runs = [r for r in runs if r.kind == "hold" or (r.kind == "play" and r.v1 - r.v0 >= 2)]

    # A misread in the middle of a run splits it; glue the halves back together.
    merged: list[Run] = []
    for r in runs:
        prev = merged[-1] if merged else None
        if prev and prev.kind == r.kind == "play" and abs(prev.anchor - r.anchor) <= OFFSET_TOL \
                and r.v0 >= prev.v1:
            prev.pts += r.pts
        elif prev and prev.kind == r.kind == "hold" and prev.v0 == r.v0:
            prev.pts += r.pts
        else:
            merged.append(r)
    return merged


@dataclass
class Segment:
    episode: int        # 0-based index into the anime list
    start: float        # stream time
    end: float          # stream time
    anime_start: float  # anime time at `start`


@dataclass
class Interval:
    kind: str                     # "full" | "play" | "pause"
    start: float                  # stream time
    end: float                    # stream time
    episode: int | None = None
    anime_t: float | None = None  # play: anime time at `start`; pause: frozen frame time

    def to_dict(self):
        return asdict(self)


def find_segments(readings, episode_durations: list[float]) -> list[list[Segment]]:
    runs = build_runs(readings)
    episodes: list[list[Segment]] = []
    prev_v1 = None
    prev_end_anime = 0.0
    for i, r in enumerate(runs):
        if r.kind != "play":
            continue
        new = prev_v1 is None or (r.v0 <= RESET_MAX and r.v0 < prev_v1 - RESET_MAX)
        if new:
            episodes.append([])
        o = r.offset()
        if new:
            anime_start = 0.0 if r.v0 <= START_SNAP else float(r.v0)
        elif abs(r.v0 - prev_end_anime) <= 2:
            anime_start = prev_end_anime  # resumed after a pause: continue exactly where it stopped
        else:
            anime_start = float(r.v0)     # she seeked
        # Timer frozen on its last value means playback stopped right as it ticked over;
        # otherwise (reset / overlay hidden) it ran to the end of that second.
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        end_anime = r.v1 if nxt and nxt.kind == "hold" and nxt.v0 == r.v1 else r.v1 + 1
        seg = Segment(len(episodes) - 1, o + anime_start, o + end_anime, anime_start)
        prev_end_anime = float(end_anime)
        ep = seg.episode
        if ep < len(episode_durations):
            seg.end = min(seg.end, o + episode_durations[ep])
        prev = episodes[-1][-1] if episodes[-1] else (episodes[-2][-1] if len(episodes) > 1 else None)
        if prev and seg.start < prev.end:  # never overlap the previous segment
            seg.anime_start += prev.end - seg.start
            seg.start = prev.end
        if seg.end > seg.start:
            episodes[-1].append(seg)
        prev_v1 = r.v1

    if len(episodes) != len(episode_durations):
        raise AlignmentError(
            f"found {len(episodes)} episodes in the stream but got {len(episode_durations)} anime files.\n"
            + describe(episodes))
    return episodes


def build_timeline(episodes: list[list[Segment]], stream_duration: float, fps: float) -> list[Interval]:
    def snap(x: float) -> float:
        return round(x * fps) / fps

    out: list[Interval] = []
    cursor = 0.0

    def push(kind, start, end, episode=None, anime_t=None):
        start, end = snap(start), snap(end)
        if end > start:
            out.append(Interval(kind, start, end, episode, anime_t))

    for segs in episodes:
        for j, seg in enumerate(segs):
            if j == 0:
                push("full", cursor, seg.start)
            else:
                prev = segs[j - 1]
                push("pause", prev.end, seg.start, seg.episode,
                     prev.anime_start + (prev.end - prev.start))
            push("play", seg.start, seg.end, seg.episode, seg.anime_start)
        cursor = segs[-1].end
    push("full", cursor, stream_duration)
    if out and out[0].start > 0:
        out.insert(0, Interval("full", 0.0, out[0].start))

    # Snapping can leave sub-frame gaps/overlaps; make intervals exactly contiguous.
    for a, b in zip(out, out[1:]):
        if b.kind == "play":
            b.anime_t += a.end - b.start
        b.start = a.end
    return out


def describe(episodes: list[list[Segment]]) -> str:
    lines = []
    for i, segs in enumerate(episodes):
        if not segs:
            continue
        pauses = len(segs) - 1
        watched = sum(s.end - s.start for s in segs)
        lines.append(
            f"  episode {i + 1}: stream {fmt_time(segs[0].start)} -> {fmt_time(segs[-1].end)}"
            f"  (watched {fmt_time(watched)}, {pauses} pause{'s' * (pauses != 1)})")
    return "\n".join(lines)
