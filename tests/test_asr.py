import unittest

from watchalong.asr import _chunk_bounds, lines_from_segments


def seg(words):
    return {"start": words[0][0], "end": words[-1][1], "text": "".join(w[2] for w in words),
            "words": [list(w) for w in words]}


class LinesFromSegmentsTest(unittest.TestCase):
    def test_lines_start_and_end_with_the_speech(self):
        lines = lines_from_segments([
            seg([(10.0, 14.0, "母"), (14.0, 14.3, "ちゃん"), (14.3, 14.6, "じゃない?")]),
            seg([(20.0, 20.4, "かわいい"), (20.4, 25.0, "ね")]),
        ])
        a, b = lines
        self.assertEqual(a["text"], "母ちゃんじゃない?")
        self.assertGreater(a["start"], 13.0)      # the stretched first word is trimmed
        self.assertAlmostEqual(a["end"], 15.0, 2)  # gone 0.4 s after she stops
        self.assertEqual(b["start"], 20.0)
        self.assertLess(b["end"], 22.0)

    def test_misaligned_first_word_stays_with_its_line(self):
        # Whisper put お 3 s early; it belongs to おっぱい, not to the previous line.
        (line,) = lines_from_segments([seg([(11.2, 11.5, "お"), (14.76, 15.2, "っぱい"), (15.2, 16.2, "でけー")])])
        self.assertEqual(line["text"], "おっぱいでけー")
        self.assertGreater(line["start"], 14.3)

    def test_pause_after_punctuation_splits(self):
        lines = lines_from_segments([seg([(0, 1, "はい。"), (2, 3, "行きましょう")])])
        self.assertEqual([x["text"] for x in lines], ["はい。", "行きましょう"])
        # a short pause without punctuation keeps the sentence together
        lines = lines_from_segments([seg([(0, 1, "100"), (2.5, 3, "キロ語?")])])
        self.assertEqual([x["text"] for x in lines], ["100キロ語?"])
        # a split never leaves a fragment like っぱい starting a line
        lines = lines_from_segments([seg([(0, 1, "お"), (2.5, 3, "っぱい")])])
        self.assertEqual([x["text"] for x in lines], ["おっぱい"])


    def test_fragment_at_segment_end_moves_to_next_line(self):
        lines = lines_from_segments([
            seg([(100.0, 100.5, "よいしょ"), (100.5, 100.7, "、"), (100.7, 101.0, "気")]),
            seg([(106.0, 106.5, "になる"), (106.5, 106.8, "、")]),
            seg([(110.0, 110.3, "百")]),
            seg([(114.0, 114.5, "キロ"), (114.5, 115.0, "語?")]),
        ])
        self.assertEqual([x["text"] for x in lines], ["よいしょ、", "気になる、", "百キロ語?"])
        self.assertGreater(lines[1]["start"], 105.0)
        self.assertGreater(lines[2]["start"], 113.0)

    def test_long_line_splits_at_punctuation(self):
        words = [(0, 2, "ここから始まる無職転生、"), (2, 4, "ぺこらの第1話、"), (4, 5, "行きましょう。"),
                 (5, 7, "それではいきます。"), (7, 9, "無職転生第1話まで!")]
        lines = lines_from_segments([seg(words)])
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(x["text"] for x in lines), "".join(w[2] for w in words))
        self.assertTrue(all(x["end"] - x["start"] <= 5.5 for x in lines))


if __name__ == "__main__":
    unittest.main()


def test_chunks_cut_at_quiet_moments():
    import numpy as np

    rate = 16000
    audio = np.ones(700 * rate, np.float32)
    audio[290 * rate:291 * rate] = 0      # a pause near the first 5-minute mark
    bounds = _chunk_bounds(audio)
    assert bounds[0] == 0 and bounds[-1] == len(audio)
    assert 290 * rate <= bounds[1] <= 291 * rate
    assert len(bounds) == 4               # ~0-290, 290-~590, ~590-700


class StrayWordsTest(unittest.TestCase):
    def test_misplaced_words_stay_with_their_phrase(self):
        # Real Whisper output: "なん" 5 s before the rest, "つ" 7 s after it.
        lines = lines_from_segments([
            seg([(7505.5, 7505.86, "なん"), (7510.63, 7510.89, "の"), (7510.89, 7511.09, "魔"),
                 (7511.09, 7511.21, "法"), (7511.21, 7511.55, "これ")]),
            seg([(7513.82, 7514.18, "あ、"), (7514.26, 7514.32, "さ"), (7514.32, 7514.44, "っ"),
                 (7514.44, 7514.5, "き"), (7514.5, 7514.62, "の"), (7514.62, 7514.68, "や"),
                 (7521.91, 7521.99, "つ")]),
        ])
        self.assertEqual([x["text"] for x in lines], ["なんの魔法これ", "あ、さっきのやつ"])
        self.assertGreater(lines[0]["start"], 7509.5)
        self.assertLess(lines[1]["end"], 7516.0)

    def test_long_pause_between_real_phrases_still_splits(self):
        lines = lines_from_segments([seg([(0, 1, "すごい"), (5, 6, "やばいね")])])
        self.assertEqual([x["text"] for x in lines], ["すごい", "やばいね"])
