"""Offline coverage for dashboard progress and overlapping background resume drafts."""
import contextlib
import io
import unittest

from runner.activity import PREFIX, read_activity, report


class ActivityTests(unittest.TestCase):
    def test_background_draft_does_not_replace_current_application(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report("matching", "Scanning resumes", {"id": 2, "company": "Example"})
            report("resume_review", "Draft ready", {"id": 1}, background=True)
        activity = read_activity(output.getvalue().splitlines())
        self.assertEqual(activity["current"]["job_id"], 2)
        self.assertEqual(activity["events"][-1]["stage"], "resume_review")

    def test_ignores_regular_and_malformed_output_and_bounds_history(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            for i in range(12):
                report("matching", str(i))
        activity = read_activity(output.getvalue().splitlines() +
                                 ["ordinary terminal log", PREFIX + "{", PREFIX + "[]"])
        self.assertEqual(len(activity["events"]), 8)
        self.assertEqual(activity["current"]["message"], "11")
        self.assertEqual(read_activity([]), {"current": None, "events": []})


if __name__ == "__main__":
    unittest.main()
