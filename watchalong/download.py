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
    if m:
        return m.group(1)
    m = _NYAA.match(src)
    return f"nyaa-{m.group(1)}" if m else hashlib.sha1(src.encode()).hexdigest()[:12]


def download_stream(src: str, dest: Path, cookies: Path | None = None) -> Path:
    """The stream as a local video: a local file as-is, a torrent's single video, or a
    YouTube (or other yt-dlp supported) download."""
    path = Path(src)
    if path.is_file():
        return path
    if _torrent_source(src):
        videos = _download(src, dest, "stream")
        if len(videos) != 1:
            raise SystemExit(f"the stream torrent has {len(videos)} videos, expected one: {src}")
        return videos[0]
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


def _is_url(src: str) -> bool:
    return re.match(r"https?://", src) is not None


def _download(src: str, d: Path, what: str, cookies: Path | None = None) -> list[Path]:
    """Download a torrent (aria2) or a video link (yt-dlp) into d, once; the videos in it.
    A `.done` file records the source, so a changed source is downloaded again."""
    done = d / ".done"
    if done.exists() and done.read_text().strip() not in ("", src):
        log.info("%s source changed, discarding old download", what)
        shutil.rmtree(d)
    if not done.exists():
        if d.exists() and not (d / ".source").exists():
            shutil.rmtree(d)  # partial download from an unknown source
        d.mkdir(parents=True, exist_ok=True)
        (d / ".source").write_text(src)
        log.info("downloading %s: %s", what, src)
        torrent = _torrent_source(src)
        if torrent:
            run(["aria2c", "--dir", d, "--seed-time=0", "--follow-torrent=mem",
                 "--file-allocation=none", "--bt-stop-timeout=1800", "--disable-ipv6",
                 "--console-log-level=warn", "--summary-interval=30", "--user-agent=Mozilla/5.0",
                 torrent])
        else:
            # A playlist link downloads the whole playlist; the index prefix keeps its order.
            cmd = ["yt-dlp", "-f", YTDLP_FORMAT, "--merge-output-format", "mp4",
                   "-o", d / "%(playlist_index|0)03d %(title).80B [%(id)s].%(ext)s"]
            if cookies:
                cmd += ["--cookies", cookies]
            run(cmd + [src])
        done.write_text(src)
    videos = _videos_in(d)
    if not videos:
        raise SystemExit(f"no video files found in download of {src}")
    return videos


def download_anime(sources: list[str], dest: Path, cookies: Path | None = None) -> list[list[Path]]:
    """Resolve each source (nyaa page, .torrent, magnet, YouTube or other video link, local
    file or folder) to its video files.

    Returns one list per source: a source that contains several videos (a batch torrent, a
    folder, a playlist) contributes all of them, naturally sorted.
    """
    files: list[list[Path]] = []
    for i, src in enumerate(sources, 1):
        path = Path(src)
        if path.is_file():
            files.append([path])
        elif path.is_dir():
            files.append(_videos_in(path))
        elif _torrent_source(src) or _is_url(src):
            files.append(_download(src, dest / f"{i:02d}", f"episode source {i}/{len(sources)}", cookies))
        else:
            raise SystemExit(f"don't know how to get episode source: {src}")
    return files
