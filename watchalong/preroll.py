"""Find distributor bumpers (Funimation, Crunchyroll, ...) at the start of anime files.

The streamer's timer starts at the first frame of the episode itself, but many releases
open with a few seconds of distributor logo first. That bumper is the same in every episode
of a release, while episodes differ from their first real frame, so: frames at the start of
an episode that also appear at the start of another episode are bumper, and the episode
begins after the last of them.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .media import probe
from .util import log, read_json, write_json

FPS = 10            # analysis frame rate
SCAN = 30.0         # how far into each file to look (s)
MAX_PREROLL = 20.0  # bumpers are short; a longer shared start is probably an OP (see below)
SIZE = (32, 18)     # fingerprint resolution
MATCH = 12.0        # mean abs difference (0-255) below which two fingerprints match
FLAT = 12.0         # fingerprints with less contrast than this (black, fades) match anything


def _fingerprints(path: Path) -> np.ndarray:
    from .media import iter_frames

    info = probe(path)
    frames = [f.astype(np.float32) for _, f in iter_frames(
        path, 0.0, min(SCAN, info.duration), FPS, (0, 0, info.width, info.height), scale_to=SIZE)]
    return np.stack(frames)


def detect(videos: list[Path], cache_dir: Path) -> list[float]:
    """Seconds to skip at the start of each video. Needs at least two episodes of the same
    release to tell a bumper from the episode; with one, returns 0."""
    cache = cache_dir / "preroll.json"
    key = [str(v) for v in videos]
    if cache.exists():
        data = read_json(cache)
        if data.get("videos") == key:
            return data["preroll"]
    if len(videos) < 2:
        return [0.0] * len(videos)

    prints = [_fingerprints(v) for v in videos]
    flat = [p.std(axis=(1, 2)) < FLAT for p in prints]
    out = []
    for i, fp in enumerate(prints):
        others = np.concatenate([p[~f] for j, (p, f) in enumerate(zip(prints, flat)) if j != i])
        # Re-encodes of the same bumper differ a lot during fast animation, so a few frames
        # inside it won't match. Put the cut where "matches another episode" gives way to
        # "unique to this episode": the point that maximizes matched minus unmatched frames
        # before it (black and flat frames count for neither).
        score, best, cut = 0, 0, 0
        for k in range(len(fp)):
            if flat[i][k]:
                continue
            matched = np.abs(others - fp[k]).mean(axis=(1, 2)).min() < MATCH
            score += 1 if matched else -1
            if score > best:
                best, cut = score, k + 1
        preroll = cut / FPS if best >= FPS else 0.0  # need at least a second's worth of evidence
        if preroll > MAX_PREROLL:
            # Episodes that all open straight into the same OP share far more than a bumper.
            log.warning("episode %d: shared opening is %.1fs, too long for a bumper; not skipping "
                        "anything (set skip= in the job file if needed)", i + 1, preroll)
            preroll = 0.0
        out.append(preroll)
    write_json(cache, {"videos": key, "preroll": out})
    return out
