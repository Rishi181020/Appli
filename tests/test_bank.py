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
