"""Job files: which stream goes with which episodes, from URLs, local files, or a mix.

    # Lines before the first stream are defaults for every job in the file.
    translator  claude-code
    cleanup     render

    stream  https://www.youtube.com/live/eFYByyw5AJc
    name    pekora-mushoku-s1
    episode https://nyaa.si/view/1325663
    episode ~/Downloads/[EMBER] Mushoku Tensei - 02.mkv
    episode ./mushoku/             # a folder: all videos in it, in filename order
    episode https://nyaa.si/view/1342865  skip=7.0   # seconds of bumper before the episode
    vocab   ぺこら=Pekora, 野うさぎ=nousagi, ぺこーら

    stream  ~/vods/another-watchalong.mp4
    episode magnet:?xt=urn:btih:...
    ratio   0.22                   # any command-line option, for this job only
    no-duck

Each `stream` line starts a new job; the `episode` lines after it belong to it, in watch
order. Distributor bumpers before the episode are detected automatically; `skip=SECONDS` at
the end of an episode line sets it by hand. `vocab` lines add names and terms (see
parse_vocab). Any other key is a command-line option by its long name, without the dashes:
before the first `stream` it applies to every job, after it to that job only. Relative paths
are relative to the job file. Lines starting with # are comments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*://|magnet:)", re.I)


@dataclass
class Job:
    stream: str
    episodes: list[str] = field(default_factory=list)
    vocab: list[tuple[str, str]] = field(default_factory=list)
    skips: list[float | None] = field(default_factory=list)  # per episode source; None = auto
    options: list[tuple[str, str | None]] = field(default_factory=list)  # file defaults, then its own
    where: str = ""  # "file:line" of the stream line, for messages


def parse_vocab(text: str) -> list[tuple[str, str]]:
    """"ぺこら=Pekora, 野うさぎ=nousagi、ぺこーら" -> [("ぺこら", "Pekora"), ...].
    The Japanese side is what the transcript may contain; the optional English side tells the
    translator how to render it."""
    out = []
    for item in re.split(r"[,、\n]", text):
        ja, _, en = item.partition("=")
        if ja.strip():
            out.append((ja.strip(), en.strip()))
    return out


def resolve_source(value: str, base: Path) -> str:
    """URLs and magnets pass through; local paths get ~ expanded and are made absolute."""
    if _REMOTE.match(value):
        return value
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    if not path.exists():
        raise SystemExit(f"local file not found: {path}")
    return str(path.resolve())


def parse_job_file(path: Path, options: dict[str, bool] | None = None) -> list[Job]:
    """Jobs in the file. `options` maps the option keys allowed in it to whether they take a
    value. Every problem in the file is reported at once, as a SystemExit."""
    options = options or {}
    jobs: list[Job] = []
    defaults: list[tuple[str, str | None]] = []
    default_vocab: list[tuple[str, str]] = []
    errors: list[str] = []
    base = path.resolve().parent
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = re.sub(r"\s+#.*$", "", raw).strip()  # trailing comments; URLs never contain " #"
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition(" ")
        key, value = key.lower(), value.strip()
        where = f"{path}:{n}"
        if key in ("stream", "episode", "anime"):
            if not value:
                errors.append(f"{where}: '{key}' needs a URL or path")
                continue
            if key != "stream" and not jobs:
                errors.append(f"{where}: 'episode' before any 'stream' line")
                continue
            m = re.search(r"\s+skip=(\d+(?:\.\d+)?)$", value) if key != "stream" else None
            if m:
                value = value[:m.start()]
            try:
                value = resolve_source(value, base)
            except SystemExit as e:
                errors.append(f"{where}: {e}")
            if key == "stream":
                jobs.append(Job(value, vocab=list(default_vocab), options=list(defaults), where=where))
            else:
                jobs[-1].episodes.append(value)
                jobs[-1].skips.append(float(m.group(1)) if m else None)
        elif key in ("vocab", "names"):
            (jobs[-1].vocab if jobs else default_vocab).extend(parse_vocab(value))
        elif key in options:
            if options[key] and not value:
                errors.append(f"{where}: '{key}' needs a value")
            elif not options[key] and value:
                errors.append(f"{where}: '{key}' takes no value")
            else:
                (jobs[-1].options if jobs else defaults).append((key, value or None))
        else:
            errors.append(f"{where}: unknown key '{key}' (expected stream, episode, vocab, "
                          "or a command-line option without the dashes)")
    if not jobs and not errors:
        errors.append(f"{path}: no 'stream' lines")
    for job in jobs:
        if not job.episodes:
            errors.append(f"{job.where}: stream {job.stream} has no episodes")
    if errors:
        raise SystemExit("\n".join(errors))
    return jobs
