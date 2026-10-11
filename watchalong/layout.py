from __future__ import annotations

from dataclasses import dataclass


def _even(x: float) -> int:
    return int(round(x / 2)) * 2


@dataclass
class Layout:
    """Output frame while an episode is on screen (sizes for a 1920x1080 stream at ratio 0.25):

        +---------------------------+------+
        |  streamer subs (top band) |      |
        +---------------------------+ VTu- |
        |                           | ber  |
        |   anime 1440x810          | strip|
        |   (its own subs inside)   | 480  |
        +---------------------------+------+
    """
    width: int
    height: int
    ratio: float = 0.25        # share of the width given to the streamer strip
    crop_offset: int = 0       # shift the strip horizontally from the stream's center

    @property
    def strip_w(self) -> int:
        return _even(self.width * self.ratio)

    @property
    def panel_w(self) -> int:
        return self.width - self.strip_w

    @property
    def panel_h(self) -> int:
        return min(self.height, _even(self.panel_w * 9 / 16))

    @property
    def panel_y(self) -> int:
        return self.height - self.panel_h

    @property
    def band_h(self) -> int:
        return self.panel_y

    @property
    def strip_x(self) -> int:
        x = (self.width - self.strip_w) // 2 + self.crop_offset
        return max(0, min(self.width - self.strip_w, x))
