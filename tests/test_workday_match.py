"""Workday search-box picks for your field of study: a telling word must match, never only 'Engineering'."""
import unittest

from runner.workday import _differs, _study_match

CSE = "Computer Science and Engineering"


class StudyMatchTests(unittest.TestCase):
    def test_picks_computer_science_never_aerospace(self):
        self.assertEqual(_study_match(CSE, ["Aerospace Engineering", "Computer Science", "Computer Engineering"]), "Computer Science")
        self.assertIsNone(_study_match(CSE, ["Aerospace Engineering", "Civil Engineering"]))

    def test_a_wrong_value_from_the_resume_reading_is_replaced(self):
        self.assertTrue(_differs("Aerospace Engineering", CSE))
        self.assertFalse(_differs("Computer Science", CSE))


if __name__ == "__main__":
    unittest.main()
