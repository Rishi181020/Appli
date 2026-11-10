"""Offline checks for the job import payload and the bank resume's Skills section. Run: python -m tests.test_bank"""
from runner.db import job_payload
from runner.resume.bankbuild import merge_skills
from runner.sheet import JobRow

# ---- import: every row has the same keys and a status (a bulk insert sends NULL for a key only some rows have) ----
row = dict(dedupe_key="k", company="Acme", role="SWE", location=None, salary=None, source=None, fit_tier=None,
           days_posted=None)
rows = [JobRow(**row, url="https://jobs.ashbyhq.com/acme/1", ats="ashby"),
        JobRow(**row, url=None, ats="none"),
        JobRow(**row, url="https://acme.com/careers/1", ats="other")]
p = job_payload(rows, "owner")
assert len({frozenset(d) for d in p}) == 1, [sorted(d) for d in p]
assert [d["status"] for d in p] == ["queued", "needs_manual", "needs_manual"], p
print("import payload checks passed")

# ---- Skills: the base resume's lines stay whole; posting skills are added, languages only if the base has them ----
base = [{"group": "Languages", "items": "Python, TypeScript, SQL"},
        {"group": "Frontend", "items": "React, Angular"},
        {"group": "Databases & Data", "items": "PostgreSQL, Neo4j (Cypher)"},
        {"group": "Tools, Infra & AI", "items": "Docker, AWS"}]
terms = [{"term": "Rust", "aliases": [], "category": "language"},
         {"term": "Python", "aliases": [], "category": "language"},
         {"term": "Electron", "aliases": [], "category": "framework"},
         {"term": "Kubernetes", "aliases": ["K8s"], "category": "tool"},
         {"term": "Software Engineer", "aliases": [], "category": "title"},
         {"term": "AWS Certified", "aliases": [], "category": "certification"},
         {"term": "networks", "aliases": [], "category": "concept"}]
lines, added, excluded = merge_skills(base, [{"group": "Databases & Data", "items": "Neo4j, Supabase"}], terms,
                                      "Python TypeScript SQL React", "", "kubernetes supabase")
items = {g["group"]: [i.strip() for i in g["items"].split(",")] for g in lines}
for g in base:  # nothing of the template is lost
    for i in g["items"].split(","):
        assert i.strip() in items[g["group"]], (i, items)
assert excluded == ["Rust"], excluded
assert {a["skill"]: a["backed"] for a in added if not a.get("suggested")} == {"Electron": False, "Kubernetes": True}, added
assert [a["skill"] for a in added if a.get("suggested")] == ["networks"], added  # a concept is offered, not added
assert not any("networks" in g["items"] for g in lines), lines
assert "Electron" in items["Frontend"] and "Kubernetes" in items["Tools, Infra & AI"], items
assert items["Databases & Data"].count("Neo4j") == 0 and "Supabase" in items["Databases & Data"], items  # no repeat
assert items["Languages"][0] == "Python", items  # the posting's requirement first
print("skills merge checks passed")

# ---- pinned bullets: used word for word, as many as planned, the rest kept as spares ----
from runner.resume.bankbuild import pin
plan_ = {"sections": [{"type": "experience", "entries": [{"id": "job", "bullets": 2}]}]}
d = pin({"entries": {"job": {"bullets": [{"text": "model words"}]}}}, plan_, {"job": {"pinned": ["A", "B", "C"]}, "other": {}})
assert [b["text"] for b in d["entries"]["job"]["bullets"]] == ["A", "B"] and [b["text"] for b in d["entries"]["job"]["spare"]] == ["C"], d
assert all(b["edited"] for b in d["entries"]["job"]["bullets"]), "pinned bullets skip the evidence check"
print("pinned bullet checks passed")

# ---- Claude single pass: one call gives the layout and the bullets; no planner, writer or critic call -------------
from types import SimpleNamespace
from unittest.mock import patch

from runner.resume import bankbuild

bank_entries = [{"id": i, "kind": k, "title": i, "org": "Acme", "start": "2024", "end": e, "tags": [], "text": f"{i} text"}
                for i, k, e in (("old", "experience", "2024"), ("now", "experience", "Present"), ("proj", "project", ""))]
reply = {"pages": 1, "rationale": "r",
         "sections": [{"type": "experience", "heading": "Jobs", "entries": [{"id": "old", "bullets": 2}, {"id": "now", "bullets": 1}]},
                      {"type": "projects", "heading": "Projects", "entries": [{"id": "ghost", "bullets": 2}]}],
         "reserve": [{"id": "proj", "bullets": 2}],
         "entries": {"now": {"bullets": [{"text": "Did it", "evidence": "now text", "terms": []}]}}, "skills": []}
with patch.object(bankbuild.llm, "claude_json", return_value=reply) as one, \
        patch.object(bankbuild.llm, "chat_json", side_effect=AssertionError("no other model call")):
    plan_c, draft_c = bankbuild.claude_build({"role": "SWE", "company": "X"}, {"terms": []}, bank_entries,
                                             SimpleNamespace(education=[]), {"experience": "Work Experience"},
                                             ["education", "experience", "projects", "skills"], "Python")
assert one.call_count == 1
exp = next(s for s in plan_c["sections"] if s["type"] == "experience")
assert exp["heading"] == "Work Experience", "the person's own heading wins"
assert [x["id"] for x in exp["entries"]] == ["now", "old"] and exp["entries"][0]["bullets"] >= 4, exp  # newest first, current job 4+
assert [x["id"] for x in plan_c["reserve"]] == ["proj"], "an invented id is dropped"
assert draft_c["entries"]["now"]["bullets"][0]["text"] == "Did it"
print("claude single pass checks passed")

# ---- a project's prize is printed on its heading; the person's own instructions reach Claude ------------------------
src = r"\documentclass{article}\begin{document}\section{Education}\resumeSubheading{U}{}{MS}{2027}\end{document}"
won = {"id": "r", "kind": "project", "title": "Riff", "org": "1st Place, X Hackathon", "link": "", "start": "", "end": ""}
tex_out = bankbuild.render({"sections": [{"type": "projects", "heading": "Projects", "entries": [{"id": "r", "bullets": 1}]}]},
                           {"entries": {"r": {"bullets": [{"text": "Built it"}]}}}, {"r": won}, None, src)
assert r"\textbf{Riff} $|$ \textit{\small 1st Place, X Hackathon}" in tex_out, tex_out
with patch.object(bankbuild.config, "USER", SimpleNamespace(tailor_prompt="exactly 2 projects")), \
        patch.object(bankbuild.llm, "claude_json", return_value=reply) as one:
    bankbuild.claude_build({"role": "SWE", "company": "X"}, {"terms": []}, bank_entries, SimpleNamespace(education=[]), {}, [], "")
assert one.call_args[0][0].endswith("exactly 2 projects") and '{"pages"' in one.call_args[0][0], "instructions appended, JSON shape single-braced"
print("prize heading and instruction checks passed")

# ---- metrics are bolded by code, whatever the model did ----------------------------------------------------------
assert bankbuild.bold_numbers("cut costs by 85% for 1,500 orgs, 100+ APIs, 5K+ req/day") == \
    "cut costs by **85%** for **1,500** orgs, **100+** APIs, **5K+** req/day"
assert bankbuild.bold_numbers("C3.js on EC2 with GPT-4o, 4 agents, since 2023") == "C3.js on EC2 with GPT-4o, 4 agents, since 2023"
assert bankbuild.bold_numbers("from **70% to 95%**") == "from **70% to 95%**", "already bold: untouched"
print("number highlighting checks passed")

# ---- a figure appears once on the page, whatever the wording; your own words are never dropped for it -------------
plan_f = {"sections": [{"type": "experience", "heading": "Work", "entries": [{"id": "m", "bullets": 3}]}]}
draft_f = {"entries": {"m": {"bullets": [
    {"text": "Raised accuracy across **70,000 documents** by adding hybrid retrieval"},
    {"text": "Wrote the Box sync that fetched 70,000 documents from one spreadsheet"},
    {"text": "Integrated **100+ APIs** into forms"},
    {"text": "Shipped dashboards for 100+ clients"},
    {"text": "Typed by hand about 70,000 documents again", "edited": True}],
    "spare": [{"text": "Cut parsing errors over 70,000 documents with table repair"}]}}}
bankbuild.drop_repeats(plan_f, draft_f)
assert [b["text"][:5] for b in draft_f["entries"]["m"]["bullets"]] == ["Raise", "Integ", "Shipp", "Typed"], draft_f
assert draft_f["entries"]["m"]["spare"] == [], draft_f
print("repeated figure checks passed")

# ---- pinned / edited bullets (your own words) get their numbers bolded on the page too --------------------------
job_e = {"id": "m", "kind": "experience", "title": "Intern", "org": "Miller", "location": "", "start": "2026", "end": "Present"}
tex_m = bankbuild.render({"sections": [{"type": "experience", "heading": "Work", "entries": [{"id": "m", "bullets": 1}]}]},
                         {"entries": {"m": {"bullets": [{"text": "Indexed 70,000 documents", "edited": True}]}}}, {"m": job_e}, None, src)
assert r"Indexed \textbf{70,000} documents" in tex_m, tex_m
print("pinned bullet bolding checks passed")

# ---- repeats never reach the page: bullets that say the same thing, skills listed twice ---------------------------
plan_r = {"sections": [{"type": "experience", "heading": "Work", "entries": [{"id": "c", "bullets": 2}]}]}
draft_r = {"entries": {"c": {"bullets": [
    {"text": "Led the full development lifecycle in Angular.NET and **MSSQL** and built a searchable catalog"},
    {"text": "Led the full development lifecycle using Angular, DOTNET, and MSSQL, managing the project"},
    {"text": "Built a client history database cutting data entry time by 40%"}],
    "spare": [{"text": "Led the full development lifecycle from requirement gathering to deployment"}]}}}
bankbuild.drop_repeats(plan_r, draft_r)
assert [b["text"][:10] for b in draft_r["entries"]["c"]["bullets"]] == ["Led the fu", "Built a cl"], draft_r
assert draft_r["entries"]["c"]["spare"] == [], "a repeating spare is never added back"
base_r = [{"group": "Backend", "items": "FastAPI, Node.js"}, {"group": "Databases", "items": "PostgreSQL, SQLite"},
          {"group": "Tools", "items": "AWS, Docker"}]
got = bankbuild.dedupe_skills([
    {"group": "Backend", "items": "FastAPI, Node.js, FastAPI, async Python, PostgreSQL, SQLite), Alembic"},
    {"group": "Databases", "items": "PostgreSQL, SQLite"},
    {"group": "Tools", "items": "AWS, AWS (EC2, S3), Docker"}], base_r)
assert got == [{"group": "Backend", "items": "FastAPI, Node.js, async Python, Alembic"},
               {"group": "Databases", "items": "PostgreSQL, SQLite"},
               {"group": "Tools", "items": "AWS (EC2, S3), Docker"}], got
print("repeat removal checks passed")
