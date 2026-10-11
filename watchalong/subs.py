"""Write the streamer's subtitles as ASS, positioned for whichever layout is on screen."""
from __future__ import annotations

from bisect import bisect_right
from pathlib import Path

from .align import Interval
from .layout import Layout

FONT = "Noto Sans CJK JP"  # covers Japanese and Latin; installed in the Docker image
COLOR = "&H00FFC58E"       # Pekora blue (#8EC5FF, ASS is &HAABBGGRR)


def _ts(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return text.replace("{", "｛").replace("}", "｝").replace("\n", r"\N")


def write_ass(lines: list[dict], texts: list[str], timeline: list[Interval],
              layout: Layout, path: Path) -> None:
    """`lines` carry timing, `texts` the (possibly translated) text for each line.

    "Watch" style sits in the band above the anime panel; "Full" is a normal bottom
    subtitle for chatting segments where the original stream fills the frame.
    """
    size = round(layout.height * 0.045)
    outline = max(2, size // 16)
    margin = round(layout.width * 0.015)
    styles = [
        ("Watch", 8, margin, layout.strip_w + margin, round(layout.band_h * 0.08)),
        ("Full", 2, margin * 4, margin * 4, round(layout.height * 0.05)),
    ]
    out = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {layout.width}",
        f"PlayResY: {layout.height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for name, align, ml, mr, mv in styles:
        out.append(f"Style: {name},{FONT},{size},{COLOR},&H000000FF,&H00000000,&H80000000,"
                   f"1,0,0,0,100,100,0,0,1,{outline},0,{align},{ml},{mr},{mv},1")
    out += ["", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    for line, text in zip(lines, texts):
        if not text.strip():  # the translator dropped it as recognizer noise
            continue
        for start, end, style in _placements(line["start"], line["end"], timeline):
            out.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},{style},,0,0,0,,{_clean(text)}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _placements(start: float, end: float, timeline: list[Interval]) -> list[tuple[float, float, str]]:
    """Split a line where the layout changes, so it moves to the top band when an episode
    starts mid-sentence and back to the bottom when it ends."""
    if not timeline:
        return [(start, end, "Full")]
    starts = [iv.start for iv in timeline]
    i = max(0, bisect_right(starts, start) - 1)
    parts: list[tuple[float, float, str]] = []
    t = start
    while t < end:
        iv = timeline[i] if i < len(timeline) else None
        style = "Full" if iv is None or iv.kind == "full" else "Watch"
        stop = end if iv is None or i + 1 >= len(timeline) else min(end, timeline[i + 1].start)
        if parts and parts[-1][2] == style:
            parts[-1] = (parts[-1][0], stop, style)
        elif stop - t > 0.05 or not parts:
            parts.append((t, stop, style))
        else:
            parts[-1] = (parts[-1][0], stop, parts[-1][2])
        t, i = stop, i + 1
    return parts
