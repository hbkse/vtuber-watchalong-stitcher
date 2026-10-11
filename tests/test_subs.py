import unittest

from watchalong.align import Interval
from watchalong.subs import _placements


class PlacementTest(unittest.TestCase):
    timeline = [Interval("full", 0, 10, None, None), Interval("play", 10, 20, 0, 0),
                Interval("pause", 20, 25, 0, 10), Interval("full", 25, 40, None, None)]

    def test_line_moves_with_layout(self):
        self.assertEqual(_placements(2, 5, self.timeline), [(2, 5, "Full")])
        self.assertEqual(_placements(8, 12, self.timeline), [(8, 10, "Full"), (10, 12, "Watch")])
        # play -> pause keeps the split layout, so the line stays in one piece
        self.assertEqual(_placements(18, 27, self.timeline), [(18, 25, "Watch"), (25, 27, "Full")])
        self.assertEqual(_placements(38, 45, self.timeline), [(38, 45, "Full")])


if __name__ == "__main__":
    unittest.main()
