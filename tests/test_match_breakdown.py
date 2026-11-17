"""Offline checks for the four-part match, the structured qualifications and the extraction cache."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from runner.profile import Education
from runner.resume import match

PROFILE = """# Ada Example

## Work Experience

### Software Engineer — Paylane

2023-01 to 2024-01

- Built payments APIs in Python.

### Software Engineering Intern — Bigco

2022-06 to 2022-09

## Skills
"""
TERMS = [
    {"term": "Python", "aliases": [], "required": True, "category": "language"},
    {"term": "Kubernetes", "aliases": ["K8s"], "required": True, "category": "tool"},
    {"term": "PostgreSQL", "aliases": ["Postgres"], "required": False, "category": "database"},
    {"term": "REST APIs", "aliases": [], "required": True, "category": "concept"},
]
QUALS = {"seniority": "mid", "min_yoe": 3, "education": {"level": "master", "fields": []},
         "industry": ["payments", "healthcare"]}


class BreakdownTests(unittest.TestCase):
    def profile(self):
        return SimpleNamespace(text=PROFILE, education=[Education("U", "Bachelor of Science", "CS", 2018, 8, 2022, 5)])

    def test_each_part_and_total(self):
        b = match.breakdown(self.profile(), "Python and Postgres services with REST APIs", TERMS, QUALS)
        # skills: Python 2x1.0 + Postgres 1x1.0 + REST APIs 2x0.6 = 4.2 of 6.2 (Kubernetes 2.0 missing)
        self.assertAlmostEqual(b["parts"]["skills"], 4.2 / 6.2, places=3)
        self.assertEqual(b["yoe"], 1.0)  # the internship doesn't count
        self.assertAlmostEqual(b["parts"]["seniority"], 1 - 2 / 4)  # 3 required, 1 held
        self.assertEqual(b["parts"]["education"], 0.5)  # one degree below
        self.assertEqual(b["parts"]["industry"], 0.5)  # payments shown, healthcare not
        total = 0.5 * 4.2 / 6.2 + 0.2 * 0.5 + 0.15 * 0.5 + 0.15 * 0.5
        self.assertAlmostEqual(b["pct"], total, places=3)
        self.assertEqual(b["band"], "weak")
        self.assertEqual(b["reasons"][0], "Missing: Kubernetes")
        self.assertIn("3 yrs required, you have 1", b["reasons"])

    def test_no_requirements_is_a_full_match_on_everything_but_skills(self):
        b = match.breakdown(self.profile(), "Python Kubernetes Postgres REST APIs", TERMS, {})
        self.assertEqual(b["parts"], {"skills": 1.0, "seniority": 1.0, "education": 1.0, "industry": 1.0})
        self.assertEqual((b["pct"], b["band"]), (1.0, "strong"))

    def test_bands(self):
        self.assertEqual([match.band(p) for p in (None, 0.7, 0.71, 0.85, 0.86)], ["none", "weak", "good", "good", "strong"])

    def test_years_overlap_and_present(self):
        text = ("## Work Experience\n\n### Engineer — A\n\n2020-01 to 2022-01\n\n### Engineer — B\n\n2021-01 to Present\n\n"
                "## Skills\n")
        self.assertEqual(match.years_experience(text, today=date(2024, 1, 15)), 4.0)

    def test_extract_cleans_quals_and_caches(self):
        reply = {"terms": [{"term": "Python", "aliases": [], "required": True, "category": "language"},
                           {"term": "Terraform", "aliases": [], "required": False, "category": "tool"}],
                 "quals": {"seniority": "senior", "min_yoe": "5", "education": {"level": "doctorate"},
                           "industry": ["fintech", "space"], "work_model": "remote", "company_size": "huge"}}
        posting = "Senior engineer, 5+ years, Python and Terraform, fintech experience. Remote. " * 5
        with tempfile.TemporaryDirectory() as d, patch.object(match.llm, "chat_json", return_value=reply) as llm:
            first = match.extract(posting, "Engineer", "Acme", cache_dir=Path(d))
            again = match.extract(posting, "Engineer", "Acme", cache_dir=Path(d))
        self.assertEqual(llm.call_count, 1)
        self.assertEqual(first, again)
        q = first["quals"]
        self.assertEqual((q["seniority"], q["min_yoe"], q["work_model"], q["company_size"]), ("senior", 5, "remote", None))
        self.assertEqual(q["education"]["level"], "none")  # not one of the levels
        self.assertEqual(q["industry"], ["fintech"])  # "space" isn't in the posting
        self.assertEqual([t["kind"] for t in first["terms"]], ["hard", "tool"])


if __name__ == "__main__":
    unittest.main()
