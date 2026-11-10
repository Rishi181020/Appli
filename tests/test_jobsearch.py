"""Offline checks for Find jobs: ranking, title families, filters, feedback, the digest and H-1B counts."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from runner import config, jobsearch
from runner.jobsearch import Found, Prefs


def found(key="k1", company="Acme", title="Software Engineer", location="San Francisco, CA", age=0, **kw):
    posted = (date.today() - timedelta(days=age)).isoformat()
    return Found(key, company, title, location, f"https://x/{key}", "greenhouse", "Company board", posted, **kw)


class JobSearchTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.enterContext(patch.object(config, "HOME", Path(d.name)))

    def test_freshness_multiplies_so_an_old_perfect_match_loses_to_a_new_good_one(self):
        self.assertAlmostEqual(jobsearch.freshness(0), 2.3)
        self.assertAlmostEqual(jobsearch.freshness(7), 1.65)
        prefs = Prefs(titles=["Software Engineer"])
        old = found("old", age=30, match={"breakdown": {"pct": 1.0}})
        new = found("new", age=0, match={"breakdown": {"pct": 0.75}})
        self.assertGreater(jobsearch.rank(new, prefs), jobsearch.rank(old, prefs))
        self.assertEqual(jobsearch.rank(found(screened="no sponsorship"), prefs), 0.0)

    def test_not_interested_down_weights_company_and_title(self):
        prefs = Prefs(titles=["Software Engineer"])
        fb = {"ignored": {"x": {"company": "Acme Inc.", "title": "Data Engineer"}}}
        self.assertAlmostEqual(jobsearch.rank(found(), prefs, fb), jobsearch.rank(found(), prefs) * 0.5, places=3)
        other = found(company="Other", title="Data Engineer")
        self.assertAlmostEqual(jobsearch.rank(other, prefs, fb), jobsearch.rank(other, prefs) * 0.7, places=3)

    def test_title_families(self):
        titles = jobsearch.expand_titles(["Software Engineer"])
        self.assertIn("Backend Engineer", titles)
        self.assertNotIn("Data Engineer", titles)
        self.assertTrue(jobsearch.title_matches("Back-End Engineer, Payments", titles))
        self.assertEqual(jobsearch.expand_titles(["Astronaut"]), ["Astronaut"])  # no family: unchanged
        prefs = Prefs(titles=["Machine Learning Engineer"], levels=[])
        self.assertIsNone(jobsearch.keep(found(title="Applied Scientist"), prefs, "", "", frozenset()))

    def test_filters_before_and_after_reading(self):
        prefs = Prefs(titles=["Software Engineer"], levels=[], exclude_companies=["acme"], work_models=["onsite"],
                      seniorities=["entry"], company_sizes=["startup"], sponsor_only=True)
        self.assertEqual(jobsearch.keep(found(), prefs, "", "", frozenset()), "excluded company")
        self.assertEqual(jobsearch.keep(found(company="B", location="Remote"), prefs, "", "", frozenset()), "work model")
        q = {"work_model": "onsite", "seniority": "entry", "company_size": "startup"}
        self.assertEqual(jobsearch.post_filter(found(company="B", match={"quals": q}), prefs), "no sponsorship")
        self.assertIsNone(jobsearch.post_filter(found(company="B", h1b=12, match={"quals": q}), prefs))
        self.assertEqual(jobsearch.post_filter(found(h1b=1, match={"quals": {**q, "seniority": "senior"}}), prefs), "seniority")
        self.assertEqual(jobsearch.post_filter(found(h1b=1, match={"quals": {**q, "company_size": "large"}}), prefs), "company size")
        self.assertIsNone(jobsearch.post_filter(found(h1b=1), prefs))  # not read yet: nothing known, it stays

    def test_simplify_list_first_and_board_openings_past_new_grad_dropped(self):
        prefs = Prefs(titles=["Software Engineer"])  # default levels: new_grad, entry
        listed = found("s")
        listed.source = "New-grad list"
        self.assertGreater(jobsearch.rank(listed, prefs), jobsearch.rank(found("b", title="Software Engineer, New Grad"), prefs))
        self.assertEqual(jobsearch.keep(found(title="Software Engineer II"), prefs, "", "", frozenset()), "level")
        self.assertEqual(jobsearch.keep(found(title="Mid-Level Software Engineer"), prefs, "", "", frozenset()), "level")
        self.assertIsNone(jobsearch.keep(found(title="Software Engineer I"), prefs, "", "", frozenset()))
        self.assertEqual(jobsearch.post_filter(found(match={"quals": {"seniority": "mid"}}), prefs), "seniority")
        self.assertEqual(jobsearch.post_filter(found(match={"quals": {"min_yoe": 4}}), prefs), "seniority")
        self.assertIsNone(jobsearch.post_filter(listed, prefs))
        self.assertIsNone(jobsearch.keep(listed, prefs, "", "", frozenset()))
        listed.title = "FPGA Design Engineer"  # the list holds every field: titles still apply
        self.assertEqual(jobsearch.keep(listed, prefs, "", "", frozenset()), "title")
        listed.title = "Software Engineer"
        listed.match = {"quals": {"seniority": "mid"}}  # the curated list is trusted over the posting's wording
        self.assertIsNone(jobsearch.post_filter(listed, prefs))
        prefs.seniorities = ["entry"]  # ticked explicitly: the list is still trusted, board openings aren't
        self.assertIsNone(jobsearch.post_filter(listed, prefs))
        self.assertEqual(jobsearch.post_filter(found(match={"quals": {"seniority": "mid"}}), prefs), "seniority")

    def test_staff_exclude_spares_member_of_technical_staff(self):
        prefs = Prefs(titles=["Software Engineer"], levels=[], exclude=["staff"])
        self.assertIsNone(jobsearch.keep(found(title="Member of Technical Staff - Platform"), prefs, "", "", frozenset()))
        self.assertEqual(jobsearch.keep(found(title="Staff Software Engineer"), prefs, "", "", frozenset()), "excluded word")

    def test_prefs_round_trip_keeps_booleans(self):
        p = jobsearch.save_prefs({"titles": ["SWE"], "sponsor_only": True, "score_top": "500"})
        self.assertIs(p.sponsor_only, True)
        self.assertEqual(p.score_top, 150)
        self.assertIs(jobsearch.save_prefs({"sponsor_only": "yes"}).sponsor_only, False)

    def test_feedback_and_digest(self):
        r = {"key": "k1", "company": "Acme", "title": "SWE", "url": "u", "match": {"terms": [{"term": "Python"}]}}
        jobsearch.save_found({"results": [r]})
        out = jobsearch.set_feedback("k1", "ignore")
        self.assertEqual(out["feedback"]["ignored"]["k1"]["terms"], ["Python"])
        self.assertEqual(out["results"][0]["feedback"], "ignored")
        out = jobsearch.set_feedback("k1", "like")  # one bucket at a time
        self.assertEqual((list(out["feedback"]["ignored"]), list(out["feedback"]["liked"])), ([], ["k1"]))
        self.assertIsNone(jobsearch.set_feedback("k1", "clear")["results"][0]["feedback"])
        with self.assertRaises(ValueError):
            jobsearch.set_feedback("gone", "like")
        strong = {"key": "n", "company": "C", "title": "T", "match": {"breakdown": {"band": "strong"}}}
        d = jobsearch.digest({"results": [r]}, {"searched_at": "now", "results": [r, strong, {**strong, "key": "s", "screened": "x"}]})
        self.assertEqual((d["count"], d["jobs"][0]["key"]), (1, "n"))

    def test_h1b_csv(self):
        csv = config.HOME / "lca.csv"
        csv.write_text("CASE_STATUS,VISA_CLASS,EMPLOYER_NAME\nCertified,H-1B,Acme Inc.\nCertified,H-1B,ACME INC\n"
                       "Denied,H-1B,Acme Inc.\nCertified,E-3 Australian,Acme Inc.\nCertified,H-1B,Other LLC\n", encoding="utf-8")
        self.assertEqual(jobsearch.load_h1b(str(csv), log=lambda s: None), 2)
        self.assertEqual(jobsearch.h1b_counts(), {"acme": 2, "other": 1})
        self.assertEqual(json.loads((config.HOME / "h1b.json").read_text())["source"], str(csv))


if __name__ == "__main__":
    unittest.main()
