from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("watchalong")


def run(cmd, **kwargs) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    log.debug("$ %s", " ".join(cmd))
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, **kwargs)


def require_tools(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        raise SystemExit(
            f"missing required tools: {', '.join(missing)}. "
            "Run through ./watchalong (Docker) or install them, see README."
        )


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text())


def fmt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def parse_time(text: str) -> float:
    """'1:02:03.5', '62:03', '3723' -> seconds."""
    total = 0.0
    for part in text.strip().split(":"):
        total = total * 60 + float(part)
    return total
