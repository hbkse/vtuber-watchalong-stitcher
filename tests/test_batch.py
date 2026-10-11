import tempfile
import unittest
from pathlib import Path

from watchalong.cli import build_parser, main, plan_jobs


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "ep01.mkv").touch()
        (self.dir / "vod.mp4").touch()
        (self.dir / "jobs").mkdir()
        self.data = self.dir / "data"

    def plans(self, *argv):
        argv = list(argv) + ["--data", str(self.data)]
        parser = build_parser()
        return plan_jobs(parser, argv, parser.parse_args(argv))

    def test_option_precedence_and_outputs(self):
        (self.dir / "jobs" / "a.txt").write_text(
            "ratio 0.3\ncrf 18\n"
            "stream ../vod.mp4\nepisode ../ep01.mkv\nname one\n"
            "stream ../vod.mp4\nepisode ../ep01.mkv\nname two\nratio 0.2\ncrop-offset -40\n"
            "range 0:10:00-0:12:00\n")
        (self.dir / "jobs" / "b.txt").write_text("stream ../vod.mp4\nepisode ../ep01.mkv\n")
        one, two, three = self.plans("--job", str(self.dir / "jobs"), "--crf", "22", "--preset", "slow")
        self.assertEqual((one.args.ratio, one.args.crf, one.args.preset), (0.3, 18, "slow"))
        self.assertEqual((two.args.ratio, two.args.crop_offset), (0.2, -40))
        self.assertEqual(one.out.name, "one.mp4")
        self.assertEqual(two.out.name, "two_600-720.mp4")
        self.assertEqual(three.out.name, "vod.mp4")      # default: the stream's id
        self.assertEqual(three.args.ratio, 0.25)         # a.txt's defaults don't leak into b.txt
        self.assertTrue(all(p.status == "pending" for p in (one, two, three)))

    def test_bad_jobs_fail_alone(self):
        (self.dir / "jobs" / "a.txt").write_text(
            "stream ../vod.mp4\nepisode ../ep01.mkv\nratio lots\n"
            "stream ../vod.mp4\nepisode ../ep01.mkv\n"
            "stream ../vod.mp4\nepisode ../ep01.mkv\n")       # same output as the one before
        (self.dir / "jobs" / "b.txt").write_text("stream ../vod.mp4\nbogus\n")
        bad_opt, good, dup, bad_file = self.plans("--job", str(self.dir / "jobs"))
        self.assertEqual(bad_opt.status, "failed")
        self.assertIn("invalid float", bad_opt.reason)
        self.assertEqual(good.status, "pending")
        self.assertEqual(dup.status, "failed")
        self.assertIn("same output", dup.reason)
        self.assertEqual(bad_file.status, "failed")
        self.assertIn("unknown key 'bogus'", bad_file.reason)

    def test_dry_run_and_skip_existing(self):
        job = self.dir / "jobs" / "a.txt"
        job.write_text("stream ../vod.mp4\nepisode ../ep01.mkv\n")
        argv = ["--job", str(job), "--data", str(self.data), "--dry-run"]
        self.assertEqual(main(argv), 0)
        (self.data / "output").mkdir(parents=True)
        (self.data / "output" / "vod.mp4").touch()
        self.assertEqual(main(argv), 0)                 # skipped, not failed
        job.write_text("stream ../vod.mp4\n")
        self.assertEqual(main(argv), 1)


if __name__ == "__main__":
    unittest.main()
