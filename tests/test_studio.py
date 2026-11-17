"""Offline checks for Tailor resume past the first match: the bullet model, keyword checks, chat honesty, versions,
export styles and diagnose. No model, LaTeX or database: those are replaced."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from runner import bank, config, userconfig
from runner.resume import history, select, studio, tex, word
from runner.resume.tex import Unit

ENTRY = ("Built a payments API in Python serving 2,000 merchants. Cut checkout latency by 30% with Redis caching. "
         "Helped the team with on-call.")


def fake_rebuild(job, edits, profile):
    r = copy.deepcopy(job["resume_edits"])
    for eid, texts in edits["bullets"].items():
        r["draft"]["entries"][eid]["bullets"] = [{"text": t, "edited": True} for t in texts]
    if "skills" in edits:
        r["draft"]["skills"] = edits["skills"]
    return r


class StudioTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        home = self.root / "person"
        home.mkdir()
        (home / "profile.md").write_text("# Ada\n\n## Skills\n- **Languages:** Python\n", encoding="utf-8")
        user = userconfig.UserConfig(home, "owner", "a@x", "Ada", [])
        for k, v in {"ROOT": self.root, "HOME": home, "USER": user, "PROFILE_PATH": home / "profile.md",
                     "TAILORED_DIR": home / "tailored"}.items():
            self.enterContext(patch.object(config, k, v))
        bank.save({"entries": [{"id": "e1", "kind": "experience", "title": "Engineer", "org": "Paylane", "text": ENTRY,
                                "tags": [], "start": "2023", "end": "2024"}], "files": []})
        docs = lambda: {"base": {"units": [Unit("b1", "bullet", 0, 0, "Built a payments API in Python.", "")],  # noqa: E731
                                 "text": "Built a payments API in Python. Skills: Python", "skills_text": "Python"}}
        docs.cache_clear = lambda: None
        self.enterContext(patch.object(select, "base_docs", docs))
        self.enterContext(patch.object(studio.bankbuild, "rebuild", side_effect=fake_rebuild))
        self.enterContext(patch.object(studio, "_score", return_value=None))
        self.enterContext(patch.object(studio, "_profile", return_value=SimpleNamespace(text="# Ada", education=[])))
        terms = [{"term": t, "aliases": [], "required": True, "category": c}
                 for t, c in (("Python", "language"), ("Redis", "database"), ("Kubernetes", "tool"))]
        r = {"plan": {"sections": [{"type": "experience", "heading": "Experience", "entries": [{"id": "e1", "bullets": 2}]},
                                   {"type": "skills", "heading": "Skills", "entries": []}]},
             "draft": {"entries": {"e1": {"bullets": [{"text": "Built a **payments API** in Python for 2,000 merchants"},
                                                      {"text": "Cut checkout latency by 30% using Redis and Kubernetes"}]}},
                       "skills": [{"group": "Tools", "items": "Python, Redis, Kubernetes"}]},
             "entries": {"e1": {"title": "Engineer", "org": "Paylane", "start": "2023", "end": "2024"}},
             "file": "person/resume-studio/x/export/r.pdf", "pages": 1,
             "skills_added": [{"skill": "Kubernetes", "group": "Tools", "backed": False}]}
        self.id = "a" * 32
        (home / "resume-studio" / self.id).mkdir(parents=True)
        d = {"draft_id": self.id, "job": {"company": "Acme", "role": "Engineer"}, "version": "v", "mode": "bank",
             "match": {"chosen": "base", "terms": terms, "missing": ["Redis", "Kubernetes"], "equivalent": [], "quals": {}},
             "bank": r, "bullets": {}, "order": {}, "chat": [], "reviews": []}
        studio._sync(d)
        d["reviews"] = studio._verify_cards(d, SimpleNamespace(text="# Ada"))
        studio.save(d)

    def bullets(self, v):
        return [b for s in v["doc"]["sections"] for e in s["entries"] for b in e["bullets"]]

    def test_compare_paths_and_base_lines(self):
        v = studio.open_draft(self.id)
        first, second = self.bullets(v)
        self.assertEqual((first["path"], second["path"]), ("experience[0].bullet[0]", "experience[0].bullet[1]"))
        self.assertEqual(first["base"], "Built a payments API in Python.")  # rewrites a line of the base resume
        self.assertIsNone(second["base"])  # new material from the bank

    def test_accept_edit_revert(self):
        first, second = self.bullets(studio.open_draft(self.id))
        v = studio.change(self.id, first["bid"], "edit", "Shipped a payments API used by 2,000 merchants")
        self.assertEqual(self.bullets(v)[0]["text"], "Shipped a payments API used by 2,000 merchants")
        v = studio.change(self.id, first["bid"], "revert")
        self.assertEqual(self.bullets(v)[0]["text"], "Built a **payments API** in Python for 2,000 merchants")
        v = studio.change(self.id, second["bid"], "revert")  # new material, reverted: off the page
        self.assertEqual(len(self.bullets(v)), 1)
        self.assertEqual(studio.change(self.id, first["bid"], "accept")["doc"]["sections"][0]["entries"][0]["bullets"][0]["status"], "accepted")
        with self.assertRaisesRegex(ValueError, "isn't on the resume"):
            studio.change(self.id, second["bid"], "accept")

    def test_keyword_questions_and_answers(self):
        cards = studio.open_draft(self.id)["reviews"]
        self.assertEqual([(c["term"], c["bid"]) for c in cards], [("Kubernetes", None)])  # asked once, Redis is backed
        v = studio.answer(self.id, cards[0]["id"], "remove")
        self.assertEqual(v["doc"]["skills"], [{"group": "Tools", "items": "Python, Redis"}])
        self.assertEqual(bank.load()["declined"], ["Kubernetes"])
        bank.answer_skill("Kubernetes", True)  # changed their mind: confirmed, no longer declined
        d = bank.load()
        self.assertEqual(d["declined"], [])
        self.assertIn("Kubernetes", next(e for e in d["entries"] if e["title"] == bank.CONFIRMED)["text"])

    def chat(self, reply, **kw):
        chunks = [reply[i:i + 7] for i in range(0, len(reply), 7)]
        with patch.object(studio.llm, "chat_stream", side_effect=lambda *a, **k: (c for c in chunks)):
            return list(studio.chat(self.id, "make the latency one sharper", **kw))

    def test_chat_edit_is_checked(self):
        bid = self.bullets(studio.open_draft(self.id))[1]["bid"]
        ok = json.dumps({"bid": bid, "text": "Cut checkout latency **30%** with Redis caching",
                         "evidence": "Cut checkout latency by 30% with Redis caching."})
        events = self.chat(f"Tightened it to the result.\n{studio.MARKER}\n{ok}")
        said = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertEqual(said, "Tightened it to the result.")  # the edit itself never shows as text
        change = next(e for e in events if e["type"] == "change")
        self.assertEqual(self.bullets(change["state"])[1]["text"], "Cut checkout latency **30%** with Redis caching")
        bad = json.dumps({"bid": bid, "text": "Cut checkout latency 45% with Redis",
                          "evidence": "Cut checkout latency by 30% with Redis caching."})
        events = self.chat(f"Done.\n{studio.MARKER}\n{bad}")
        self.assertIn("45%", next(e for e in events if e["type"] == "rejected")["reason"])
        self.assertEqual(self.bullets(studio.open_draft(self.id))[1]["text"], "Cut checkout latency **30%** with Redis caching")

    def test_chat_facts_go_to_the_bank(self):
        bid = self.bullets(studio.open_draft(self.id))[0]["bid"]
        self.chat("Thanks, noted.", bid=bid, facts=True)
        self.assertIn("make the latency one sharper", next(e for e in bank.load()["entries"] if e["id"] == "e1")["text"])

    def test_diagnose(self):
        first = self.bullets(studio.open_draft(self.id))[0]["bid"]
        studio.change(self.id, first, "edit", "Helped with the payments work")
        with patch.object(studio, "_text_of", return_value="Experience\nHelped with the payments work"):
            issues = studio.diagnose(self.id)["diagnosis"]
        kinds = {(i["kind"], i["bid"]) for i in issues}
        self.assertIn(("weak_verb", first), kinds)
        self.assertIn(("no_number", first), kinds)
        self.assertIn(("missing_summary", None), kinds)
        self.assertTrue(all(i["fix"] for i in issues if i["bid"]))

    def test_versions_restore_state(self):
        export = studio._folder(self.id) / "export"
        export.mkdir()
        (export / "r.pdf").write_bytes(b"one")
        studio.save(studio.load(self.id))
        studio.save_version(self.id)
        first = self.bullets(studio.open_draft(self.id))[0]["bid"]
        studio.change(self.id, first, "edit", "Shipped a payments API for 2,000 merchants")
        (export / "r.pdf").write_bytes(b"two")
        vid = studio.open_draft(self.id)["versions"][0]["id"]
        v = studio.restore(self.id, vid)
        self.assertEqual((export / "r.pdf").read_bytes(), b"one")
        self.assertEqual(self.bullets(v)[0]["text"], "Built a **payments API** in Python for 2,000 merchants")
        self.assertEqual(len(v["versions"]), 2)  # what was current is kept too


class HistoryTests(unittest.TestCase):
    def test_snapshot_prune_and_restore_oldest(self):
        with tempfile.TemporaryDirectory() as d, patch.object(history, "KEEP", 3):
            folder = Path(d)
            for i in range(4):
                (folder / "r.pdf").write_bytes(str(i).encode())
                history.snapshot(folder)
            self.assertEqual(len(history.versions(folder)), 3)
            oldest = history.versions(folder)[-1]["id"]
            history.restore(folder, oldest)  # pruned by the snapshot restore takes: read first
            self.assertEqual((folder / "r.pdf").read_bytes(), b"1")
            with self.assertRaises(ValueError):
                history.restore(folder, "../x")


TEX = r"""\documentclass[letterpaper,11pt]{article}
\usepackage{titlesec}
\titleformat{\section}{\large}{}{0em}{}[\titlerule \vspace{-5pt}]
\begin{document}
\begin{center}Ada\end{center}
\section{Experience}
\resumeSubheading{Paylane}{SF}{Engineer}{Sep 2023 - Present}
\resumeItem{Shipped in May 2024}
\section{Technical Skills}
Python, Redis
\end{document}
"""


class StyleTests(unittest.TestCase):
    def test_tex_styles(self):
        self.assertEqual(tex.styled(TEX, {}), TEX)
        self.assertEqual(tex.styled(TEX, {"template": "huge"}), TEX)  # unknown values are ignored
        s = tex.styled(TEX, {"template": "compact", "header": "left", "date_format": "MM/YYYY", "divider": False,
                             "skills_columns": 2})
        self.assertIn("[letterpaper,10pt]", s)
        self.assertIn(r"\begin{flushleft}Ada\end{flushleft}", s)
        self.assertIn("{09/2023 - Present}", s)
        self.assertIn("Shipped in May 2024", s)  # a date inside a bullet is your own words
        self.assertNotIn(r"\titlerule", s)
        self.assertIn("\\begin{multicols}{2}\nPython, Redis\n\\end{multicols}", s)
        self.assertIn(r"\usepackage{multicol}", s)
        self.assertEqual(tex.format_dates("Sep 2023 - Jun 2027", "Month YYYY"), "September 2023 - June 2027")
        self.assertEqual(tex.format_dates("2023-09", "Mon YYYY"), "Sep 2023")

    def test_word_styles(self):
        doc = Document()
        doc.add_paragraph("Ada Example").alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.add_heading("Experience", 1)
        doc.add_paragraph("Paylane, Engineer  Sep 2023 - Present")
        doc.add_paragraph("Shipped in May 2024", style="List Bullet")
        doc.add_heading("Skills", 1)
        doc.add_paragraph("Languages: Python, Go")
        doc.add_paragraph("Tools: Redis, Docker")
        buf = io.BytesIO()
        doc.save(buf)
        out = Document(io.BytesIO(word.styled(buf.getvalue(), {"header": "left", "date_format": "YYYY-MM",
                                                               "skills_columns": 2, "template": "compact"})))
        texts = [p.text for p in out.paragraphs]
        self.assertEqual(out.paragraphs[0].alignment, WD_ALIGN_PARAGRAPH.LEFT)
        self.assertIn("Paylane, Engineer  2023-09 - Present", texts)
        self.assertIn("Shipped in May 2024", texts)
        cols = [c.get(qn("w:num")) for c in out.element.body.iter(qn("w:cols"))]
        self.assertEqual(cols, ["1", "2", None])  # before Skills one column, the Skills lines two, then the default
        self.assertEqual(word.styled(buf.getvalue(), {}), buf.getvalue())


if __name__ == "__main__":
    unittest.main()
