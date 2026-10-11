from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import align, timer
from .download import download_anime, download_stream, stream_id
from .extract import extract_subs
from .jobs import Job, parse_job_file, parse_vocab, resolve_source
from .layout import Layout
from .media import has_filter, probe
from .render import AnimeEpisode, RenderOptions, render
from .subs import write_ass
from .translate import TRANSLATORS, TranslationContext, anime_dialogue, get_translator
from .util import fmt_time, log, parse_time, read_json, require_tools, write_json


class OptionError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """Raises instead of exiting while job files' options are parsed, so one bad job doesn't
    end the batch."""
    strict = False

    def error(self, message):
        if self.strict:
            raise OptionError(message)
        super().error(message)


def build_parser() -> _Parser:
    p = _Parser(
        prog="watchalong",
        description="Combine a VTuber watchalong stream with the anime being watched: anime on the "
                    "left, streamer on the right, both subtitled, synced from the on-screen timer.")
    p.add_argument("stream", nargs="?",
                   help="YouTube URL or local video file of the watchalong stream")
    p.add_argument("anime", nargs="*",
                   help="episodes in watch order: nyaa.si links, .torrent URLs, magnets, local files "
                        "or folders. A batch torrent counts as all of its episodes.")
    p.add_argument("--job", type=Path, metavar="FILE|DIR",
                   help="job file listing one or more streams and their episodes (see README), or a "
                        "folder of them (*.txt, in name order), instead of positional arguments")
    g = p.add_argument_group("batch")
    g.add_argument("--dry-run", action="store_true",
                   help="check every job (sources, options, output names) and exit without running")
    g.add_argument("--force", action="store_true",
                   help="run jobs whose output already exists (they are skipped otherwise)")
    g.add_argument("--name", help="output file name, without .mp4 (default: the stream's video id)")
    g.add_argument("--cleanup", default="none", choices=CLEANUP,
                   help="after a job succeeds, delete: render = render pieces; downloads = those "
                        "and the downloaded stream and episodes; all = the job's whole work folder "
                        "(default none)")
    g = p.add_argument_group("layout")
    g.add_argument("--ratio", type=float, default=0.25,
                   help="share of the width for the streamer strip (default 0.25)")
    g.add_argument("--crop-offset", type=int, default=0,
                   help="shift the streamer strip right (+) or left (-) of center, in pixels")
    g.add_argument("--range", metavar="START-END",
                   help="only render this part of the stream, e.g. 0:30:00-0:33:00 (quick previews)")
    g = p.add_argument_group("subtitles")
    g.add_argument("--no-asr", action="store_true", help="skip speech-to-text (no streamer subs)")
    g.add_argument("--no-render", action="store_true",
                   help="stop after writing the streamer's subtitles (data/work/<id>/vtuber.ass)")
    g.add_argument("--asr-model", default="large-v3-turbo",
                   help="faster-whisper model (default large-v3-turbo; large-v3 is slower, more accurate)")
    g.add_argument("--translator", default="none", choices=sorted(TRANSLATORS),
                   help="translation provider for the streamer's lines (default none = Japanese)")
    g.add_argument("--vocab", action="append", default=[], metavar="TERMS",
                   help="names/terms for the translator, e.g. 'ぺこら=Pekora, 野うさぎ=nousagi' "
                        "(repeatable; adds to the job file's)")
    g.add_argument("--no-auto-vocab", action="store_true",
                   help="don't ask Claude Code to list the anime's names and terms")
    g = p.add_argument_group("sync")
    g.add_argument("--anime-skip", metavar="SECONDS|auto", default="auto",
                   help="seconds of distributor bumper (e.g. Funimation logo) before each episode, "
                        "or auto to detect it by comparing episodes (default)")
    g.add_argument("--timer-roi", metavar="X,Y,W,H",
                   help="timer position in stream pixels, if auto-detection fails")
    g = p.add_argument_group("output")
    g.add_argument("--preset", default="veryfast", help="x264 preset (default veryfast)")
    g.add_argument("--crf", type=int, default=20, help="x264 quality, lower is better (default 20)")
    g.add_argument("--anime-volume", type=float, default=0.8)
    g.add_argument("--voice-volume", type=float, default=1.0)
    g.add_argument("--no-duck", action="store_true",
                   help="don't lower the anime audio while the streamer talks")
    g = p.add_argument_group("misc")
    g.add_argument("--cookies", type=Path, help="cookies.txt for members-only streams")
    g.add_argument("--data", type=Path, default=Path(os.environ.get("WATCHALONG_DATA", "data")),
                   help="where downloads, cache and output go (default ./data, /data in Docker)")
    g.add_argument("--workers", type=int, default=None, help="parallel OCR workers (default: all cores)")
    g.add_argument("--render-jobs", type=int, default=0,
                   help="pieces rendered at once (default: cores / 3)")
    g.add_argument("-v", "--verbose", action="store_true")
    return p


CLEANUP = ("none", "render", "downloads", "all")
_NOT_IN_JOB_FILES = {"help", "job", "dry-run", "vocab"}  # vocab has its own job file syntax


def job_file_options(parser: argparse.ArgumentParser) -> dict[str, bool]:
    """Option keys a job file may use (long names without dashes) -> whether they take a value."""
    out = {}
    for action in parser._actions:
        for flag in action.option_strings:
            if flag.startswith("--") and flag[2:] not in _NOT_IN_JOB_FILES:
                out[flag[2:]] = action.nargs != 0
    return out


@dataclass
class Plan:
    job: Job | None
    args: argparse.Namespace | None
    out: Path | None
    label: str
    status: str = "pending"    # pending, done, skipped, failed
    reason: str = ""
    seconds: float = 0.0


def output_path(args, job: Job) -> Path:
    name = args.name or stream_id(job.stream)
    if args.range:
        a, b = args.range.split("-")
        name += f"_{round(parse_time(a))}-{round(parse_time(b))}"
    return (args.data / "output" / f"{name}.mp4").resolve()


def plan_jobs(parser, argv, args) -> list[Plan]:
    """Every job with its own options resolved; problems become failed plans."""
    if not args.job:
        cwd = Path.cwd()
        job = Job(resolve_source(args.stream, cwd), [resolve_source(a, cwd) for a in args.anime])
        return [Plan(job, args, output_path(args, job), job.stream)]

    files = sorted(args.job.glob("*.txt")) if args.job.is_dir() else [args.job]
    if not files:
        raise SystemExit(f"no *.txt job files in {args.job}")
    allowed = job_file_options(parser)
    plans = []
    for f in files:
        try:
            jobs = parse_job_file(f, allowed)
        except SystemExit as e:
            plans.append(Plan(None, None, None, str(f), "failed", str(e)))
            continue
        for job in jobs:
            # Later options win: command line, then the file's defaults, then the job's own.
            extra = [f"--{k}={v}" if v is not None else f"--{k}" for k, v in job.options]
            parser.strict = True
            try:
                jargs = parser.parse_args(argv + extra)
            except OptionError as e:
                plans.append(Plan(job, None, None, job.where, "failed", str(e)))
                continue
            finally:
                parser.strict = False
            plans.append(Plan(job, jargs, output_path(jargs, job), job.where))

    seen: dict[Path, str] = {}
    for p in plans:
        if p.out is None:
            continue
        if p.out in seen:
            p.status, p.reason = "failed", f"same output as {seen[p.out]}: {p.out.name} (set name)"
        else:
            seen[p.out] = p.label
        if p.status == "pending" and p.args.translator == "claude-code" and not shutil.which("claude"):
            p.status, p.reason = "failed", "translator claude-code needs the claude CLI"
    return plans


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.job and args.stream:
        parser.error("give either --job FILE or STREAM ANIME..., not both")
    if not args.job and not (args.stream and args.anime):
        parser.error("need STREAM and at least one ANIME source, or --job FILE")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    plans = plan_jobs(parser, argv, args)
    for p in plans:
        if p.status == "pending" and p.out.exists() and not p.args.force:
            p.status, p.reason = "skipped", f"{p.out.name} exists (--force to redo)"
    if args.dry_run:
        for p in plans:
            if p.status == "pending":
                p.status, p.reason = "ok", f"-> {p.out.name}, {len(p.job.episodes)} episode source(s)"
        return summary(plans)

    if any(p.status == "pending" for p in plans):
        require_tools("ffmpeg", "ffprobe", "yt-dlp", "aria2c", "tesseract")
        if not has_filter("subtitles"):
            raise SystemExit("this ffmpeg build has no 'subtitles' filter (libass). Use the Docker image.")
    todo = [p for p in plans if p.status == "pending"]
    for i, p in enumerate(todo, 1):
        if len(plans) > 1:
            log.info("######## job %d/%d: %s (%s)", i, len(todo), p.out.stem, p.label)
        logs = p.args.data / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(logs / f"{p.out.stem}.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(handler)
        t = time.monotonic()
        try:
            run_job(p.job, p.args, p.out)
            p.status = "done"
        except KeyboardInterrupt:
            p.status, p.reason = "failed", "interrupted"
            raise
        except SystemExit as e:
            p.status, p.reason = "failed", str(e).strip().splitlines()[-1] if str(e).strip() else "exited"
            log.error("job failed: %s", e)
        except Exception as e:
            p.status, p.reason = "failed", f"{type(e).__name__}: {e}"
            log.exception("job failed")
        finally:
            p.seconds = time.monotonic() - t
            logging.getLogger().removeHandler(handler)
            handler.close()
    return summary(plans)


def summary(plans: list[Plan]) -> int:
    """Log one line per job; exit status 1 if any failed."""
    if len(plans) > 1 or any(p.status != "done" for p in plans):
        log.info("summary:")
        for p in plans:
            took = f" in {fmt_time(p.seconds).split('.')[0]}" if p.seconds else ""
            name = p.out.stem if p.out else "-"
            msg = p.reason.replace("\n", "\n      ")
            log.info("  %-7s %-30s %s%s%s", p.status, name, p.label, took, f"\n      {msg}" if msg else "")
        counts = {s: sum(p.status == s for p in plans) for s in ("done", "ok", "skipped", "failed")}
        log.info("  %s", ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    return 1 if any(p.status == "failed" for p in plans) else 0


def run_job(job: Job, args, out: Path) -> None:
    sid = stream_id(job.stream)
    work = (args.data / "work" / sid).resolve()
    work.mkdir(parents=True, exist_ok=True)

    log.info("== 1/6 downloads")
    stream = download_stream(job.stream, work / "stream", args.cookies).resolve()
    groups = download_anime(job.episodes, work / "anime-src")
    videos = [v.resolve() for g in groups for v in g]
    info = probe(stream)
    log.info("stream %dx%d @ %s fps, %s; %d anime files", info.width, info.height, info.fps,
             fmt_time(info.duration), len(videos))

    log.info("== 2/6 anime subtitles")
    episodes = []
    for i, v in enumerate(videos, 1):
        rel = Path("anime") / f"{i:02d}"
        subs = extract_subs(v, work / rel)
        episodes.append(AnimeEpisode(
            video=v,
            subs=str(rel / "subs.ass") if subs.ass else None,
            fonts=str(rel / "fonts"),
        ))
    durations = [probe(v).duration for v in videos]
    skips = anime_skips(job, args, groups, videos, work)

    log.info("== 3/6 reading the on-screen timer")
    readings_file = work / "timer_readings.json"
    if readings_file.exists():
        readings = [tuple(r) for r in read_json(readings_file)]
    else:
        if args.timer_roi:
            roi = tuple(int(x) for x in args.timer_roi.split(","))
        else:
            roi = timer.detect_roi(stream, info, workers=args.workers)
        readings = timer.scan(stream, info, roi, workers=args.workers)
        write_json(readings_file, readings)

    try:
        segs = align.find_segments(readings, [d - s for d, s in zip(durations, skips)])
    except align.AlignmentError as e:
        raise SystemExit(f"sync failed: {e}")
    for ep in segs:  # the timer counts from the episode's first frame, after any bumper
        for seg in ep:
            seg.anime_start += skips[seg.episode]
    timeline = align.build_timeline(segs, info.duration, info.fps_float)
    write_json(work / "timeline.json", [iv.to_dict() for iv in timeline])
    log.info("synced:\n%s", align.describe(segs))

    span = (0.0, info.duration)
    if args.range:
        a, b = args.range.split("-")
        span = (max(0.0, parse_time(a)), min(info.duration, parse_time(b)))

    layout = Layout(info.width, info.height, args.ratio, args.crop_offset)

    vt_ass = None
    if not args.no_asr:
        vt_ass = streamer_subs(work, stream, job, args, span, timeline, episodes, layout)
    else:
        log.info("== 4/6, 5/6 skipped (--no-asr)")

    if args.no_render:
        log.info("== 6/6 skipped (--no-render)")
        return
    log.info("== 6/6 rendering %s -> %s", fmt_time(span[0]), fmt_time(span[1]))
    opts = RenderOptions(preset=args.preset, crf=args.crf, anime_volume=args.anime_volume,
                         voice_volume=args.voice_volume, duck=not args.no_duck)
    out.parent.mkdir(parents=True, exist_ok=True)
    render(work, stream, info, layout, timeline, episodes, vt_ass, out, span, opts, args.render_jobs)
    log.info("done: %s", out)
    cleanup(work, args.cleanup)


def cleanup(work: Path, level: str) -> None:
    """Free disk after a job: render pieces, then downloads, then everything. Only ever
    touches the job's work folder, never local source files."""
    if level == "none":
        return
    if level == "all":
        shutil.rmtree(work, ignore_errors=True)
    else:
        doomed = [*work.glob("chunks-*"), *work.glob("audio-*.m4a"), work / "concat.txt"]
        if level == "downloads":
            doomed += [work / "stream", work / "anime-src"]
        for path in doomed:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
    log.info("cleaned up (%s): %s", level, work)


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, default=str).encode()).hexdigest()[:10]


def streamer_subs(work, stream, job, args, span, timeline, episodes, layout) -> str:
    """Transcribe, translate and write the streamer's subtitles; returns the .ass path
    relative to the work dir. Every step is cached on everything that affects it."""
    from . import asr

    title = ""
    info_json = work / "stream" / "stream.info.json"
    if info_json.exists():
        title = read_json(info_json).get("title", "")
    ctx = TranslationContext(stream_title=title, anime_dialogue=anime_dialogue(
        timeline, [work / e.subs if e.subs else None for e in episodes]))
    translator = get_translator(args.translator)

    vocab = list(job.vocab) + [v for text in args.vocab for v in parse_vocab(text)]
    if not args.no_auto_vocab:
        vocab += [v for v in auto_vocab(work, title, episodes, ctx) if v[0] not in {ja for ja, _ in vocab}]
    ctx.glossary = vocab
    if vocab:
        log.info("vocabulary: %s", ", ".join(f"{ja}={en}" if en else ja for ja, en in vocab))

    log.info("== 4/6 transcribing the streamer")
    tag = "" if not args.range else f"-{round(span[0])}-{round(span[1])}"
    asr_file = work / f"asr-v{asr.VERSION}-{args.asr_model}{tag}.json"
    if asr_file.exists():
        segments = read_json(asr_file)
    else:
        segments = asr.transcribe(stream, args.asr_model, start=span[0], duration=span[1] - span[0],
                                  cache_dir=work / f"asr-v{asr.VERSION}-{args.asr_model}-chunks")
        write_json(asr_file, segments)
    lines = asr.lines_from_segments(segments)

    log.info("== 5/6 translating (%s)", args.translator)
    tr_file = work / f"translation-{translator.name}-{_key(lines, translator.cache_key(), vocab)}.json"
    if tr_file.exists():
        texts = read_json(tr_file)
    else:
        texts = translator.translate(lines, ctx)
        write_json(tr_file, texts)
    write_ass(lines, texts, timeline, layout, work / "vtuber.ass")
    return "vtuber.ass"


def auto_vocab(work, title, episodes, ctx) -> list[tuple[str, str]]:
    """The anime's names and terms, listed by Claude Code from its English subs. Optional:
    without a working `claude` login this logs a warning and returns nothing."""
    from .translate import ClaudeCodeTranslator

    path = work / f"vocab-auto-{_key(title, [e.subs for e in episodes])}.json"
    if not path.exists():
        if not shutil.which("claude"):
            return []
        log.info("asking Claude Code for the anime's names and terms")
        try:
            write_json(path, ClaudeCodeTranslator().suggest_vocab(ctx))
        except SystemExit as e:
            log.warning("automatic vocabulary skipped: %s", str(e).splitlines()[0])
            return []
    return [tuple(v) for v in read_json(path)]


def anime_skips(job, args, groups, videos, work) -> list[float]:
    """Seconds of bumper before each episode: job file skip=, else --anime-skip, else detected."""
    from . import preroll

    # A batch source expands to several videos; its skip= applies to all of them.
    per_video: list[float | None] = []
    for group, skip in zip(groups, job.skips or [None] * len(groups)):
        per_video += [skip] * len(group)
    if args.anime_skip != "auto":
        per_video = [float(args.anime_skip) if s is None else s for s in per_video]
    detected = preroll.detect(videos, work / "anime") if None in per_video else [0.0] * len(videos)
    skips = [d if s is None else s for s, d in zip(per_video, detected)]
    for i, (s, d) in enumerate(zip(per_video, skips), 1):
        if d:
            log.info("episode %d: skipping %.1fs of bumper%s", i, d, "" if s is None else " (set by hand)")
    return skips


if __name__ == "__main__":
    raise SystemExit(main())
