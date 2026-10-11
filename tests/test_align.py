import math
import random
import unittest

from watchalong.align import AlignmentError, build_timeline, find_segments
from watchalong.timer import SAMPLE_RATE, parse_timer

EP = 1427.0  # 23:47


def simulate(events, duration, misread_rate=0.03, seed=1):
    """events: list of (stream_start, stream_end, timer_at_start, ticking)."""
    rng = random.Random(seed)
    readings = []
    i = 0
    while (t := i / SAMPLE_RATE) < duration:
        i += 1
        v = 0
        for s, e, v0, ticking in events:
            if s <= t < e:
                v = v0 + (math.floor(t - s) if ticking else 0)
        r = rng.random()
        if r < misread_rate:
            readings.append((t, rng.randrange(0, 5000)))
        elif r < misread_rate * 2:
            readings.append((t, None))
        else:
            readings.append((t, v))
    return readings


class AlignTest(unittest.TestCase):
    def test_two_episodes_with_pause(self):
        o1, o2 = 609.37, 2200.81
        events = [
            (0, o1, 0, False),                 # waiting at 00:00:00
            (o1, o1 + 500, 0, True),           # ep1 plays
            (o1 + 500, o1 + 540, 500, False),  # paused 40s
            (o1 + 540, o1 + 540 + EP - 500, 500, True),
            (o1 + 540 + EP - 500, o2, int(EP), False),  # finished, chatting
            (o2, o2 + EP, 0, True),            # ep2
        ]
        duration = o2 + EP + 300
        readings = simulate(events, duration)
        eps = find_segments(readings, [EP, EP])

        self.assertEqual(len(eps), 2)
        self.assertEqual(len(eps[0]), 2)  # one pause
        self.assertAlmostEqual(eps[0][0].start, o1, delta=0.1)
        self.assertAlmostEqual(eps[0][1].start - eps[0][1].anime_start, o1 + 40, delta=0.1)
        self.assertAlmostEqual(eps[1][0].start, o2, delta=0.1)
        self.assertAlmostEqual(eps[1][-1].end, o2 + EP, delta=0.1)

        tl = build_timeline(eps, duration, 60.0)
        self.assertEqual([i.kind for i in tl], ["full", "play", "pause", "play", "full", "play", "full"])
        self.assertEqual(tl[0].start, 0)
        self.assertEqual(tl[-1].end, round(duration * 60) / 60)
        for a, b in zip(tl, tl[1:]):
            self.assertEqual(a.end, b.start)
        self.assertAlmostEqual(tl[2].anime_t, 500, delta=0.1)

    def test_episode_count_mismatch(self):
        events = [(100, 100 + EP, 0, True)]
        readings = simulate(events, 100 + EP + 60)
        with self.assertRaises(AlignmentError):
            find_segments(readings, [EP, EP])

    def test_parse_timer(self):
        self.assertEqual(parse_timer("00:19:51\n"), 1191)
        self.assertEqual(parse_timer("1:02:03"), 3723)
        self.assertEqual(parse_timer("19:51"), 1191)
        self.assertIsNone(parse_timer("00:79:51"))
        self.assertIsNone(parse_timer(""))


if __name__ == "__main__":
    unittest.main()
