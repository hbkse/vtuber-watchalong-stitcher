import tempfile
import unittest
from pathlib import Path

from watchalong.jobs import parse_job_file


class JobFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "ep01.mkv").touch()
        (self.dir / "batch").mkdir()

    def write(self, text):
        path = self.dir / "job.txt"
        path.write_text(text)
        return path

    def test_mixed_sources_and_multiple_jobs(self):
        jobs = parse_job_file(self.write(
            "# comment\n"
            "stream https://www.youtube.com/live/eFYByyw5AJc\n"
            "episode ep01.mkv   # relative to the job file\n"
            "episode https://nyaa.si/view/1329078  skip=7.5\n"
            "\n"
            "stream ./ep01.mkv\n"
            "episode magnet:?xt=urn:btih:abc\n"
            "episode batch\n"
            "vocab ぺこら=Pekora, 野うさぎ=nousagi、ぺこーら\n"))
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].stream, "https://www.youtube.com/live/eFYByyw5AJc")
        self.assertEqual(jobs[0].episodes, [str((self.dir / "ep01.mkv").resolve()),
                                            "https://nyaa.si/view/1329078"])
        self.assertEqual(jobs[1].episodes[0], "magnet:?xt=urn:btih:abc")
        self.assertEqual(jobs[1].episodes[1], str((self.dir / "batch").resolve()))
        self.assertEqual(jobs[0].skips, [None, 7.5])
        self.assertEqual(jobs[0].vocab, [])
        self.assertEqual(jobs[1].vocab, [("ぺこら", "Pekora"), ("野うさぎ", "nousagi"), ("ぺこーら", "")])

    def test_options_and_defaults(self):
        opts = {"ratio": True, "no-duck": False, "name": True}
        jobs = parse_job_file(self.write(
            "ratio 0.3\n"
            "vocab ぺこら=Pekora\n"
            "stream ./ep01.mkv\n"
            "episode ep01.mkv\n"
            "name first\n"
            "stream ./ep01.mkv\n"
            "episode ep01.mkv\n"
            "ratio 0.2\n"
            "no-duck\n"
            "vocab 野うさぎ=nousagi\n"), opts)
        self.assertEqual(jobs[0].options, [("ratio", "0.3"), ("name", "first")])
        self.assertEqual(jobs[1].options, [("ratio", "0.3"), ("ratio", "0.2"), ("no-duck", None)])
        self.assertEqual(jobs[1].vocab, [("ぺこら", "Pekora"), ("野うさぎ", "nousagi")])
        self.assertTrue(jobs[1].where.endswith("job.txt:6"))

    def test_all_errors_reported_at_once(self):
        with self.assertRaises(SystemExit) as cm:
            parse_job_file(self.write("stream ./nope.mkv\nepisode ep01.mkv\nratio\nno-duck yes\nbogus 1\n"),
                           {"ratio": True, "no-duck": False})
        msg = str(cm.exception)
        for part in ("local file not found", "'ratio' needs a value", "'no-duck' takes no value",
                     "unknown key 'bogus'"):
            self.assertIn(part, msg)

    def test_errors(self):
        for text in ("episode x.mkv\n", "stream https://x.y/z\n", "stream https://x.y/z\nepisode missing.mkv\n",
                     "stream https://x.y/z\nfoo bar\n"):
            with self.assertRaises(SystemExit, msg=text):
                parse_job_file(self.write(text))


if __name__ == "__main__":
    unittest.main()
