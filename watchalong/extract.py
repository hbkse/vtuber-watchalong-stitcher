from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .media import subtitle_streams
from .util import log, run

TEXT_SUB_CODECS = ("ass", "ssa", "subrip", "webvtt", "mov_text")


@dataclass
class AnimeSubs:
    ass: Path | None
    fonts: Path


def _pick_stream(streams: list[dict]) -> dict | None:
    text = [s for s in streams if s.get("codec_name") in TEXT_SUB_CODECS]
    if not text:
        return None

    def score(s):
        tags = s.get("tags", {})
        lang = tags.get("language", "")
        title = tags.get("title", "").lower()
        return (
            lang in ("eng", "en"),
            "sign" not in title and "song" not in title,  # skip signs-only tracks
            s.get("codec_name") in ("ass", "ssa"),
            s.get("disposition", {}).get("default", 0),
        )

    return max(text, key=score)


def extract_subs(video: Path, dest: Path) -> AnimeSubs:
    """Pull the subtitle track (as ASS) and attached fonts out of an anime file."""
    dest.mkdir(parents=True, exist_ok=True)
    fonts = dest / "fonts"
    ass = dest / "subs.ass"
    done = dest / ".done"
    if done.exists():
        return AnimeSubs(ass if ass.exists() else None, fonts)

    sidecar = next((video.with_suffix(ext) for ext in (".ass", ".ssa", ".srt", ".vtt")
                    if video.with_suffix(ext).is_file()), None)
    stream = None if sidecar else _pick_stream(subtitle_streams(video))
    if sidecar:
        log.info("using subtitle file %s", sidecar.name)
        run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", sidecar, ass])
    elif stream is None:
        log.warning("%s has no text subtitle track; anime will be rendered without subs", video.name)
    else:
        run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", video,
             "-map", f"0:{stream['index']}", ass])

    # Fansub ASS relies on embedded fonts. ffmpeg exits non-zero here ("no output file")
    # even though the attachments were written, so don't check the return code.
    fonts.mkdir(exist_ok=True)
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-dump_attachment:t", "",
         "-i", video.resolve()], cwd=fonts, check=False, capture_output=True)

    done.touch()
    return AnimeSubs(ass if ass.exists() else None, fonts)
