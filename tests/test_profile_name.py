"""The profile's '# Name' line -> first / last name for forms (middle names stay with the first name)."""
import tempfile
import unittest
from pathlib import Path

from runner.profile import load_profile


def names(header: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "profile.md"
        p.write_text(f"# {header}\n\nme@x.com\n", encoding="utf-8")
        prof = load_profile(p)
        return prof.first_name, prof.last_name


class ProfileNameTests(unittest.TestCase):
    def test_last_word_is_the_surname(self):
        self.assertEqual(names("Rishi Rugweda Dixit"), ("Rishi Rugweda", "Dixit"))
        self.assertEqual(names("Ada Lovelace"), ("Ada", "Lovelace"))
        self.assertEqual(names("Cher"), ("Cher", ""))


if __name__ == "__main__":
    unittest.main()
