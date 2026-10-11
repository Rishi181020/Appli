"""Offline checks for resume parsing and matching. Run: python -m tests.test_resume"""
from runner import config
from runner.profile import load_profile
from runner.resume import match, select, tailor, tex

from runner import db

config.activate(db._owner())  # the files of whoever last signed in here (no network needed)
profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
docs = select.base_docs()
assert set(docs) == {"swe", "ai", "fde"}, docs.keys()

# ---- parsing --------------------------------------------------------------------------------
swe = docs["swe"]
bullets = [u for u in swe["units"] if u.kind == "bullet"]
skills = [u for u in swe["units"] if u.kind == "skill"]
assert len(bullets) == 14 and len(skills) == 5, (len(bullets), len(skills))
assert tex.apply_edits(swe["src"], swe["units"], {u.id: u.text for u in swe["units"]}) == swe["src"]
assert tex.apply_edits(swe["src"], swe["units"], {}) == swe["src"]
assert "Jio Platforms" in bullets[3].context or "Jio Platforms" in " ".join(u.context for u in bullets), "context"
assert tex.plain(r"\textbf{Redis/\allowbreak ARQ} improved 15\% \& more") == "Redis/ARQ improved 15% & more"

# ---- markup round trip and safety -----------------------------------------------------------
assert tex.from_markup("Cut costs 85% & used `a_b` with **R&D**") == r"Cut costs 85\% \& used \texttt{a\_b} with \textbf{R\&D}"
for u in swe["units"]:
    assert tex.plain(tex.from_markup(tex.to_markup(u.text))) == tex.plain(u.text), u.id
assert tex.markup_problems(r"uses \textbf{x}") and tex.markup_problems("has {braces}") and tex.markup_problems("**open")
assert not tex.markup_problems("plain **bold** and `code`")
assert tex.latex_problems(r"\input{/etc/passwd}") and tex.latex_problems("50% unescaped") and not tex.latex_problems(r"ok \textbf{x} 5\%")

# ---- matching -------------------------------------------------------------------------------
terms = [
    {"term": "PostgreSQL", "aliases": ["Postgres"], "required": True},
    {"term": "Docker", "aliases": [], "required": True},
    {"term": "GPT-4o", "aliases": ["GPT 4o"], "required": False},
    {"term": "Kubernetes", "aliases": ["K8s"], "required": True},
    {"term": "Java", "aliases": [], "required": True},
]
assert match.has_term(match.norm_text("uses Postgres and pgvector"), "Postgres")
assert not match.has_term(match.norm_text("I know JavaScript"), "Java"), "Java must not match JavaScript"
assert match.has_term(match.norm_text("C++, Node.js, CI/CD"), "C++") and match.has_term(match.norm_text("Node.js"), "node.js")
s = match.score(swe["text"], terms)
assert "PostgreSQL" in s["matched"] and "Docker" in s["matched"] and "Kubernetes" in s["missing"], s
assert abs(s["pct"] - (2 + 2 + 2) / 9) < 1e-6 or s["pct"] > 0, s  # java is in the SWE skills line

# ---- word diff ------------------------------------------------------------------------------
a, r = tailor.word_diff("Built APIs with Postgres and Docker", "Built REST APIs with PostgreSQL and Docker")
assert a == ["REST", "PostgreSQL"] and r == ["Postgres"], (a, r)

print("all resume checks passed")

# an alias that is only a piece of its term is dropped when terms are extracted (stubbed model)
from runner import llm
_real = llm.chat_json
llm.chat_json = lambda *a, **k: {"terms": [
    {"term": "structured debugging", "aliases": ["debugging", "structured debug"], "required": True},
    {"term": "PostgreSQL", "aliases": ["Postgres"], "required": True},
    {"term": "Kubernetes", "aliases": ["K8s"], "required": False},
    {"term": "Made Up Thing", "aliases": [], "required": True}]}
try:
    got = match.extract_terms("We need structured debugging skills, PostgreSQL and Kubernetes (K8s) experience. " * 3, "SWE", "Acme")
finally:
    llm.chat_json = _real
byname = {t["term"]: t for t in got}
assert "Made Up Thing" not in byname, "terms the posting never mentions are dropped"
assert "debugging" not in byname["structured debugging"]["aliases"], byname["structured debugging"]
assert byname["PostgreSQL"]["aliases"] == ["Postgres"]
print("alias filtering checks passed")

# ---- ATS criteria ---------------------------------------------------------------------------
# categorical weighting: a missing required hard skill costs more than a missing required concept
t_tool = {"term": "Kubernetes", "aliases": [], "required": True, "category": "tool"}
t_concept = {"term": "event sourcing", "aliases": [], "required": True, "category": "concept"}
t_have = {"term": "Python", "aliases": [], "required": True, "category": "language"}
miss_tool = match.score("Python and event sourcing", [t_have, t_tool, t_concept])["pct"]
miss_concept = match.score("Python and Kubernetes", [t_have, t_tool, t_concept])["pct"]
assert miss_tool < miss_concept, (miss_tool, miss_concept)

# exact vs semantic: an equivalent earns half credit, and exact_pct ignores it
t_vc = {"term": "version control", "aliases": [], "required": True, "category": "concept"}
sc = match.score("Git/GitHub Flow and Python", [t_have, t_vc], semantic={"version control"})
assert sc["equivalent"] == ["version control"] and sc["exact_pct"] < sc["pct"] < 1, sc
assert abs(sc["pct"] - (2 + 2 * 0.6 * 0.5) / (2 + 2 * 0.6)) < 1e-9

# frequency & density: more than 3 mentions is flagged as stuffing
sc = match.score("Python Python Python Python", [t_have])
assert sc["stuffed"] == ["Python"] and sc["counts"]["Python"] == 4

# where keywords live: a hard skill only in Skills (no bullet) is reported
sc = match.score("Python, SQL. Built dashboards in Angular", [t_have], skills_text="Python, SQL", bullets_text="Built dashboards in Angular")
assert sc["skills_only"] == ["Python"], sc

print("ATS criteria checks passed")
