from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from .util import log, run

# Prefer H.264: it decodes much faster than VP9/AV1, and we decode the stream several times.
YTDLP_FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
VIDEO_EXTS = {".mkv", ".mp4", ".webm", ".avi", ".m4v"}

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")
_NYAA = re.compile(r"https?://(?:www\.)?nyaa\.si/(?:view|download)/(\d+)")


def stream_id(src: str) -> str:
    """Stable, filesystem-safe name for the work directory."""
    path = Path(src)
    if path.exists():
        return re.sub(r"[^A-Za-z0-9_-]", "_", path.stem)
    m = _YT_ID.search(src)
    return m.group(1) if m else hashlib.sha1(src.encode()).hexdigest()[:12]


def download_stream(src: str, dest: Path, cookies: Path | None = None) -> Path:
    path = Path(src)
    if path.is_file():
        return path
    out = dest / "stream.mp4"
    if out.exists():
        return out
    dest.mkdir(parents=True, exist_ok=True)
    log.info("downloading stream %s", src)
    cmd = ["yt-dlp", "--no-playlist", "-f", YTDLP_FORMAT, "--merge-output-format", "mp4",
           "--write-info-json", "-o", dest / "stream.%(ext)s"]
    if cookies:
        cmd += ["--cookies", cookies]
    run(cmd + [src])
    return out


def _natural_key(p: Path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r"(\d+)", p.name)]


def _videos_in(d: Path) -> list[Path]:
    return sorted(
        (p for p in d.rglob("*") if p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(".")),
        key=_natural_key,
    )


def _torrent_source(src: str) -> str | None:
    m = _NYAA.match(src)
    if m:
        return f"https://nyaa.si/download/{m.group(1)}.torrent"
    if src.startswith("magnet:") or src.endswith(".torrent"):
        return src
    return None


def download_anime(sources: list[str], dest: Path) -> list[list[Path]]:
    """Resolve each source (Nyaa page, .torrent, magnet, local file/dir) to its video files.

    Returns one list per source: a source that contains several videos (a batch torrent, a
    folder) contributes all of them, naturally sorted.
    """
    files: list[list[Path]] = []
    for i, src in enumerate(sources, 1):
        path = Path(src)
        if path.is_file():
            files.append([path])
            continue
        if path.is_dir():
            files.append(_videos_in(path))
            continue
        torrent = _torrent_source(src)
        if torrent is None:
            raise SystemExit(f"don't know how to download anime source: {src}")
        d = dest / f"{i:02d}"
        done = d / ".done"
        if done.exists() and done.read_text().strip() not in ("", src):
            log.info("episode %d source changed, discarding old download", i)
            shutil.rmtree(d)
        if not done.exists():
            if d.exists() and not (d / ".source").exists():
                shutil.rmtree(d)  # partial download from an unknown source
            d.mkdir(parents=True, exist_ok=True)
            (d / ".source").write_text(src)
            log.info("downloading anime source %d/%d: %s", i, len(sources), src)
            run(["aria2c", "--dir", d, "--seed-time=0", "--follow-torrent=mem",
                 "--file-allocation=none", "--bt-stop-timeout=1800", "--disable-ipv6", "--console-log-level=warn",
                 "--summary-interval=30", "--user-agent=Mozilla/5.0", torrent])
            done.write_text(src)
        videos = _videos_in(d)
        if not videos:
            raise SystemExit(f"no video files found in download of {src}")
        files.append(videos)
    return files
