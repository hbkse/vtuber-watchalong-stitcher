"""Compose the final video.

The timeline is rendered as independent video pieces (each interval, split into <= 5 minute
pieces so an interrupted render resumes where it stopped), joined losslessly, then muxed with
one audio track mixed in a single pass.
"""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .align import Interval
from .layout import Layout
from .media import VideoInfo
from .util import fmt_time, log, run


@dataclass
class AnimeEpisode:
    video: Path
    subs: str | None   # path relative to the work dir (keeps ffmpeg filter escaping trivial)
    fonts: str | None


@dataclass
class RenderOptions:
    preset: str = "veryfast"
    crf: int = 20
    anime_volume: float = 0.8
    voice_volume: float = 1.0
    duck: bool = True       # lower the anime while the streamer talks
    max_piece: float = 300.0


@dataclass
class Piece:
    kind: str
    start_frame: int
    end_frame: int
    episode: int | None
    anime_t: float | None


def _pieces(timeline: list[Interval], fps: float, a: float, b: float, max_piece: float):
    step = int(max_piece * fps)
    fa, fb = round(a * fps), round(b * fps)
    for iv in timeline:
        s, e = max(round(iv.start * fps), fa), min(round(iv.end * fps), fb)
        f = s
        while f < e:
            g = min(e, f + step)
            anime_t = None
            if iv.kind == "play":
                anime_t = iv.anime_t + (f / fps - iv.start)
            elif iv.kind == "pause":
                anime_t = iv.anime_t
            yield Piece(iv.kind, f, g, iv.episode, anime_t)
            f = g


def _subs_filter(ass: str | None, fonts: str | None, at: float) -> str:
    """Burn subtitles into a stream that was input-seeked to `at` (its PTS restart at 0)."""
    if not ass:
        return ""
    sub = f"subtitles=filename={ass}" + (f":fontsdir={fonts}" if fonts else "")
    return f"setpts=PTS-STARTPTS+{at:.6f}/TB,{sub},setpts=PTS-STARTPTS,"


def _piece_cmd(p: Piece, stream: Path, info: VideoInfo, layout: Layout, episodes: list[AnimeEpisode],
               vt_ass: str | None, opts: RenderOptions, out: Path, threads: int = 0) -> list:
    fps = info.fps_float
    t = p.start_frame / fps
    vt_subs = _subs_filter(vt_ass, None, t)
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{t:.6f}", "-i", stream]
    if p.kind == "full":
        graph = f"[0:v]{vt_subs}fps={info.fps},format=yuv420p[out]"
    else:
        ep = episodes[p.episode]
        cmd += ["-ss", f"{p.anime_t:.6f}", "-i", ep.video]
        L = layout
        anime = (f"[1:v]{_subs_filter(ep.subs, ep.fonts, p.anime_t)}"
                 f"scale={L.panel_w}:{L.panel_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                 f"pad={L.panel_w}:{L.panel_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,")
        if p.kind == "pause":
            anime += "trim=end_frame=1,tpad=stop=-1:stop_mode=clone,"
        graph = ";".join([
            anime + f"fps={info.fps}[an]",
            f"[0:v]crop={L.strip_w}:{L.height}:{L.strip_x}:0,fps={info.fps},setsar=1[st]",
            f"color=c=black:s={L.width}x{L.height}:r={info.fps}[bg]",
            f"[bg][an]overlay=x=0:y={L.panel_y}[t1]",
            f"[t1][st]overlay=x={L.panel_w}:y=0[t2]",
            f"[t2]{vt_subs}format=yuv420p[out]",
        ])
    return cmd + [
        "-filter_complex", graph, "-map", "[out]", "-frames:v", p.end_frame - p.start_frame,
        "-c:v", "libx264", "-preset", opts.preset, "-crf", opts.crf, "-pix_fmt", "yuv420p",
        "-g", round(fps * 2), "-threads", threads, "-an", out,
    ]


def _audio_cmd(stream: Path, timeline: list[Interval], episodes: list[AnimeEpisode],
               a: float, b: float, opts: RenderOptions, out: Path) -> list:
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
           "-ss", f"{a:.6f}", "-t", f"{b - a:.6f}", "-i", stream]
    fmt = "aresample=48000,aformat=channel_layouts=stereo"
    graph = [f"[0:a]{fmt},volume={opts.voice_volume}[voice]"]
    labels = []
    for iv in timeline:
        if iv.kind != "play":
            continue
        s, e = max(iv.start, a), min(iv.end, b)
        if e <= s:
            continue
        k = len(labels) + 1
        cmd += ["-ss", f"{iv.anime_t + (s - iv.start):.6f}", "-t", f"{e - s:.6f}",
                "-i", episodes[iv.episode].video]
        graph.append(f"[{k}:a]{fmt},volume={opts.anime_volume},"
                     f"adelay={round((s - a) * 1000)}:all=1[a{k}]")
        labels.append(f"[a{k}]")

    if not labels:
        graph[0] = graph[0].replace("[voice]", "[out]")
    else:
        graph.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[anime]"
                     if len(labels) > 1 else f"{labels[0]}anull[anime]")
        if opts.duck:
            graph[0] = graph[0].replace("[voice]", "[voice_in]")
            graph.append("[voice_in]asplit=2[voice][sc]")
            graph.append("[anime][sc]sidechaincompress=threshold=0.02:ratio=8:attack=10:release=400[ducked]")
        else:
            graph.append("[anime]anull[ducked]")
        graph.append("[voice][ducked]amix=inputs=2:normalize=0:duration=first[out]")
    return cmd + ["-filter_complex", ";".join(graph), "-map", "[out]",
                  "-t", f"{b - a:.6f}", "-c:a", "aac", "-b:a", "192k", out]


def _fingerprint(workdir: Path, *parts) -> str:
    h = hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode())
    for rel in [p for p in parts[-1] if p]:  # file contents of every subtitle file
        h.update((workdir / rel).read_bytes())
    return h.hexdigest()[:10]


def render(workdir: Path, stream: Path, info: VideoInfo, layout: Layout, timeline: list[Interval],
           episodes: list[AnimeEpisode], vt_ass: str | None, out: Path,
           span: tuple[float, float], opts: RenderOptions, parallel: int = 0) -> Path:
    fps = info.fps_float
    a, b = span
    sub_files = [vt_ass] + [e.subs for e in episodes]
    key = _fingerprint(workdir, asdict(layout), asdict(opts), [iv.to_dict() for iv in timeline],
                       [str(e.video) for e in episodes], sub_files)
    chunks = workdir / f"chunks-{key}"
    chunks.mkdir(exist_ok=True)

    pieces = list(_pieces(timeline, fps, a, b, opts.max_piece))
    names = [f"{p.start_frame:09d}-{p.end_frame:09d}-{p.kind}.mp4" for p in pieces]
    todo = [(i, p, n) for i, (p, n) in enumerate(zip(pieces, names), 1) if not (chunks / n).exists()]
    # Decoding, scaling and libass are mostly single-threaded, so one ffmpeg at a time
    # leaves most cores idle; run a few pieces side by side and split the encoder threads.
    jobs = max(1, min(parallel or (os.cpu_count() or 4) // 3, len(todo)))
    threads = max(1, (os.cpu_count() or 4) // jobs)

    def render_piece(item):
        i, p, name = item
        what = p.kind if p.episode is None else f"{p.kind} ep{p.episode + 1}"
        log.info("rendering %d/%d  %s -> %s  (%s)", i, len(pieces),
                 fmt_time(p.start_frame / fps), fmt_time(p.end_frame / fps), what)
        tmp = chunks / f"tmp-{name}"
        run(_piece_cmd(p, stream, info, layout, episodes, vt_ass, opts, tmp, threads), cwd=workdir)
        tmp.rename(chunks / name)

    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(render_piece, todo))

    log.info("mixing audio")
    audio = workdir / f"audio-{key}-{round(a * fps)}-{round(b * fps)}.m4a"
    if not audio.exists():
        run(_audio_cmd(stream, timeline, episodes, a, b, opts, audio.with_suffix(".tmp.m4a")))
        audio.with_suffix(".tmp.m4a").rename(audio)

    listing = workdir / "concat.txt"
    listing.write_text("".join(f"file '{chunks.name}/{n}'\n" for n in names))
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
         "-i", audio, "-map", "0:v", "-map", "1:a", "-map_chapters", "-1", "-c", "copy", "-movflags", "+faststart", out])
    return out
