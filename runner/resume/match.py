"""Requirement terms from a job posting, and how well a resume covers them - scored the way an ATS reads a resume.

ATS criteria this module implements (see README "ATS criteria"):
- Exact vs. semantic matching: legacy ATSs (e.g. Taleo) match exact strings; modern ones (Greenhouse, Lever) use NLP
  and understand synonyms. Exact phrasing is the safest everywhere, so an exact match earns full credit and a term the
  resume only shows under another name ("Git" for "version control", judged by Jev) earns half credit. Tailoring
  exists to turn those half-credit terms into exact wording.
- Categorical weighting: hard skills, specific tools, certifications and official job titles weigh more than broad
  concepts; soft skills are not extracted at all. Required terms weigh double.
- Frequency and density: the ideal is 1-3 natural occurrences of a term. More than 3 risks keyword-stuffing filters,
  so it is flagged, and tailoring may never push a term past 3.
- Where keywords live: the Skills section (direct hard skills) and Work Experience bullets (contextual proof tied to
  action verbs and results). A hard skill that appears only in Skills, never in a bullet, is reported as lacking proof.
The LLM only *extracts* terms; coverage is computed by plain code (plus Jev's yes/no for equivalents), so it is
repeatable and explainable.
"""
import re

from .. import llm

_GENERIC = {
    "experience", "team", "teams", "communication", "skills", "skill", "ability", "strong", "knowledge", "work",
    "years", "year", "degree", "bachelor", "master", "collaboration", "problem solving", "leadership", "passion",
    "fast paced", "environment", "software", "engineering", "development", "technical", "excellent", "good",
    "software engineering", "systems thinking", "web development", "best practices", "code review", "code reviews",
    "debugging", "testing", "collaborative development workflows", "collaborative workflows", "software development",
    "computer science", "scalable systems", "high quality code", "clean code", "modern programming language",
    "programming language", "programming languages", "hardware", "physics", "outages", "postmortems", "ownership",
    "attention to detail", "curiosity", "startup", "fast learner", "self starter",
}


# Eligibility and logistics are not skills: they can't be "matched" by any resume wording.
_LOGISTICS = re.compile(
    r"degree|bachelor|master|ph\.?d|gpa|graduat|start date|\b20\d\d\b|authori[sz]|sponsor|visa|citizen|clearance|"
    r"on ?site|in person|remote|hybrid|relocat|travel|\b[a-z]+ ?, ?[a-z]{2}\b|office|years? of experience",
)

# Categorical weighting: named hard skills and tools outrank broad concepts.
CATEGORY_WEIGHT = {
    "language": 1.0, "framework": 1.0, "tool": 1.0, "platform": 1.0, "database": 1.0,
    "certification": 1.0, "title": 1.0, "concept": 0.6,
}
HARD_SKILL = {"language", "framework", "tool", "platform", "database", "certification"}
SEMANTIC_CREDIT = 0.5  # shown under another name: modern ATSs may accept it, legacy ones won't
MAX_OCCURRENCES = 3  # above this, keyword-stuffing risk


def norm_text(s: str) -> str:
    """Lowercase, hyphens/underscores to spaces, keep the characters that appear in tech names (c++ c# node.js ci/cd)."""
    s = re.sub(r"[-‐-―_]", " ", s.lower())
    s = re.sub(r"[^a-z0-9+#./ ]+", " ", s)
    return " " + re.sub(r"\s+", " ", s).strip() + " "


def _pattern(term: str):
    t = norm_text(term).strip()
    if len(t) < 2:
        return None
    body = r"\s+".join(re.escape(w) for w in t.split())
    return re.compile(r"(?<![a-z0-9+#])" + body + r"(?:s|es)?(?![a-z0-9+#])")


def has_term(text_norm: str, term: str) -> bool:
    p = _pattern(term)
    return bool(p and p.search(text_norm))


def term_present(text_norm: str, t: dict) -> bool:
    return any(has_term(text_norm, x) for x in [t["term"], *t.get("aliases", [])])


def occurrences(text_norm: str, t: dict) -> int:
    """How many times the term (any spelling) appears: the 'frequency and density' check."""
    n = 0
    for x in [t["term"], *t.get("aliases", [])]:
        p = _pattern(x)
        if p:
            n += len(p.findall(text_norm))
    return n


def weight(t: dict) -> float:
    return (2 if t.get("required") else 1) * CATEGORY_WEIGHT.get(t.get("category", "concept"), 0.6)


def score(text: str, terms: list[dict], semantic: set[str] | None = None,
          skills_text: str | None = None, bullets_text: str | None = None) -> dict:
    """ATS-style coverage.

    pct       = blended score: exact matches full weight, equivalents (in `semantic`) half weight
    exact_pct = exact wording only (what a legacy ATS sees)
    """
    semantic = semantic or set()
    tn = norm_text(text)
    sk = norm_text(skills_text) if skills_text is not None else None
    bl = norm_text(bullets_text) if bullets_text is not None else None
    total = got = exact_got = 0.0
    matched, equivalent, missing, stuffed, skills_only = [], [], [], [], []
    counts = {}
    for t in terms:
        w = weight(t)
        total += w
        if term_present(tn, t):
            got += w
            exact_got += w
            matched.append(t["term"])
            c = occurrences(tn, t)
            counts[t["term"]] = c
            if c > MAX_OCCURRENCES:
                stuffed.append(t["term"])
            if sk is not None and bl is not None and t.get("category") in HARD_SKILL \
                    and term_present(sk, t) and not term_present(bl, t):
                skills_only.append(t["term"])
        elif t["term"] in semantic:
            got += w * SEMANTIC_CREDIT
            equivalent.append(t["term"])
        else:
            missing.append(t["term"])
    return {
        "pct": (got / total) if total else 0.0,
        "exact_pct": (exact_got / total) if total else 0.0,
        "matched": matched, "equivalent": equivalent, "missing": missing,
        "counts": counts, "stuffed": stuffed, "skills_only": skills_only,
    }


def supported(t: dict, evidence_norm: str) -> bool:
    """Is this term backed, word for word, by the candidate's own materials (profile + resumes)?"""
    return term_present(evidence_norm, t)


_SYSTEM = """You extract the requirements an applicant-tracking system (ATS) would screen a resume for.
Return JSON: {"terms": [{"term": str, "aliases": [str], "required": bool, "category": str}]}
ATS rules to follow:
- Hard skills and specific tools first: programming languages, frameworks, libraries, tools, cloud platforms,
  databases, certifications/licenses, and the official job title if it is a standard one (e.g. "Software Engineer").
  Then standard technical concepts (e.g. CI/CD, microservices, REST APIs, distributed systems, machine learning,
  data structures). Hard skills outrank concepts.
- "term" must mirror the posting's EXACT wording (use "project management", not "managing projects"), because exact
  phrasing is what every ATS can match.
- "category" is one of: language, framework, tool, platform, database, certification, title, concept.
- "aliases" = other common spellings of the SAME thing (Postgres / PostgreSQL, K8s / Kubernetes). Not related
  skills, not broader or narrower terms.
- "required" = true if under requirements/qualifications/must-have, false if preferred/nice-to-have/bonus.
Never include: soft skills or personality traits, vague phrases ("modern programming language", "systems thinking",
"best practices", "hardware", "physics", "outages", "triage", "ownership"), logistics or eligibility (degrees, GPA,
dates, work authorization, visa/sponsorship, citizenship, clearance, locations, on-site/remote, travel, relocation),
years of experience, benefits, or company descriptions.
At most 15 terms, most important first. Every term must appear in the posting."""


def extract_terms(posting: str, role: str | None, company: str | None) -> list[dict]:
    def check(obj):
        if not isinstance(obj.get("terms"), list):
            raise ValueError('missing "terms" list')

    obj = llm.chat_json(_SYSTEM, f"JOB: {role} at {company}\n\nPOSTING:\n{posting[:7000]}", validate=check,
                        purpose="posting requirements")
    posting_norm = norm_text(posting)
    out, seen = [], set()
    for raw in obj["terms"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("term"), str):
            continue
        term = raw["term"].strip()
        aliases = [a.strip() for a in raw.get("aliases", []) if isinstance(a, str) and a.strip()][:5]
        key = norm_text(term).strip()
        if len(key) < 2 or key in _GENERIC or key in seen or _LOGISTICS.search(term.lower()):
            continue
        # an alias that is just a piece of the term ("debugging" for "structured debugging") is not the same skill
        aliases = [a for a in aliases if f" {norm_text(a).strip()} " not in f" {key} " or norm_text(a).strip() == key]
        category = str(raw.get("category", "concept")).lower().strip()
        t = {"term": term, "aliases": aliases, "required": bool(raw.get("required")),
             "category": category if category in CATEGORY_WEIGHT else "concept"}
        if not term_present(posting_norm, t):  # guard against requirements the posting never mentions
            continue
        seen.add(key)
        out.append(t)
    return out[:15]
