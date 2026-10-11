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
        # the command line beats the file (crf), the file beats the defaults (ratio)
        self.assertEqual((one.args.ratio, one.args.crf, one.args.preset), (0.3, 22, "slow"))
        self.assertEqual((two.args.ratio, two.args.crop_offset), (0.2, -40))
        self.assertEqual(one.out.name, "one.mp4")
        self.assertEqual(two.out.name, "two_600-720.mp4")
        self.assertEqual(three.out.name, "vod.mp4")      # default: the stream's id
        self.assertEqual(three.args.ratio, 0.25)         # a.txt's defaults don't leak into b.txt
        self.assertTrue(all(p.status == "pending" for p in (one, two, three)))

    def test_example_job_file_is_valid(self):
        example = Path(__file__).resolve().parent.parent / "example-job.txt"
        (plan,) = self.plans("--job", str(example))
        self.assertTrue(plan.status == "pending" or "claude CLI" in plan.reason, plan.reason)
        self.assertEqual(plan.args.subs, "english")
        self.assertEqual(len(plan.job.episodes), 6)
        self.assertEqual(plan.job.vocab[0], ("ぺこら", "Pekora"))
        # every default it lists matches the program's real default
        defaults = build_parser().parse_args(["--job", "x"])
        for key, value in plan.job.options:
            self.assertEqual(str(getattr(defaults, key.replace("-", "_"))), value, key)

    def test_wrapper_knows_which_options_take_no_value(self):
        # watchalong.sh skips the value after an option to find local paths among the
        # arguments, so its list of value-less flags must match the program's.
        import re
        script = (Path(__file__).resolve().parent.parent / "watchalong.sh").read_text()
        line = next(l for l in script.splitlines() if l.strip().startswith("--*=* |"))
        listed = set(re.findall(r"--?[a-z-]+", line.split(")")[0])) - {"--"}
        flags = {f for a in build_parser()._actions if a.nargs == 0 for f in a.option_strings}
        self.assertEqual(listed, flags)

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
