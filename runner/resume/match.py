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
Return JSON: {"terms": [{"term": str, "aliases": [str], "required": bool, "category": str}],
"quals": {"seniority": "intern"|"entry"|"mid"|"senior"|"lead"|null, "min_yoe": int|null,
"education": {"level": "none"|"associate"|"bachelor"|"master"|"phd", "fields": [str]},
"industry": [str], "work_model": "onsite"|"hybrid"|"remote"|null, "company_size": "startup"|"mid"|"large"|null,
"soft_skills": [str]}}
"quals" describes the role, separately from the terms: seniority as the posting pitches it; min_yoe = the fewest years
of experience it asks for (null if none); education = the lowest degree it requires (null-like "none" if none) and the
fields it names; industry = the specific domain experience it asks for (e.g. "fintech", "healthcare", "payments"),
empty unless the posting asks for it; work_model and company_size only when the posting says so; soft_skills as
written (at most 6).
ATS rules for "terms":
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
    return extract(posting, role, company)["terms"]


_PROMPT_VERSION = "2"  # bump when _SYSTEM changes: cached extractions are keyed by it


def extract(posting: str, role: str | None, company: str | None, cache_dir=None) -> dict:
    """{"terms": [...], "quals": {...}} for a posting. cache_dir: reuse the extraction of an identical posting."""
    import hashlib
    import json

    key = hashlib.sha1(f"{_PROMPT_VERSION}|{role}|{company}|{posting[:7000]}".encode()).hexdigest()[:20]
    cached = cache_dir / f"{key}.json" if cache_dir else None
    if cached and cached.exists():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def check(obj):
        if not isinstance(obj.get("terms"), list):
            raise ValueError('missing "terms" list')

    import os

    obj = llm.chat_json(_SYSTEM, f"JOB: {role} at {company}\n\nPOSTING:\n{posting[:7000]}", validate=check,
                        model=os.getenv("LLM_MODEL_EXTRACT") or None, purpose="posting requirements")
    out = {"terms": _clean_terms(obj["terms"], posting), "quals": _clean_quals(obj.get("quals"), posting)}
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(out), encoding="utf-8")
    return out


_KIND = {"certification": "cert", "tool": "tool", "platform": "tool", "database": "tool"}  # the rest are hard skills
SENIORITY = ("intern", "entry", "mid", "senior", "lead")
DEGREE_RANK = {"none": 0, "associate": 1, "bachelor": 2, "master": 3, "phd": 4}


def _clean_quals(q, posting: str) -> dict:
    q = q if isinstance(q, dict) else {}
    edu = q.get("education") if isinstance(q.get("education"), dict) else {}
    pick = lambda v, allowed: v if v in allowed else None  # noqa: E731
    strs = lambda v, n: [str(x).strip() for x in (v or []) if isinstance(x, str) and x.strip()][:n]  # noqa: E731
    try:
        yoe = int(q["min_yoe"]) if q.get("min_yoe") is not None else None
    except (TypeError, ValueError):
        yoe = None
    posting_norm = norm_text(posting)
    return {
        "seniority": pick(q.get("seniority"), SENIORITY),
        "min_yoe": yoe if yoe is not None and 0 <= yoe <= 20 else None,
        "education": {"level": pick(edu.get("level"), DEGREE_RANK) or "none", "fields": strs(edu.get("fields"), 5)},
        # only domains the posting really names: a model's guess at the company's industry isn't a requirement
        "industry": [x for x in strs(q.get("industry"), 4) if has_term(posting_norm, x)],
        "work_model": pick(q.get("work_model"), ("onsite", "hybrid", "remote")),
        "company_size": pick(q.get("company_size"), ("startup", "mid", "large")),
        "soft_skills": strs(q.get("soft_skills"), 6),
    }


def _clean_terms(raw_terms: list, posting: str) -> list[dict]:
    posting_norm = norm_text(posting)
    out, seen = [], set()
    for raw in raw_terms:
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
        category = category if category in CATEGORY_WEIGHT else "concept"
        t = {"term": term, "aliases": aliases, "required": bool(raw.get("required")),
             "category": category, "kind": _KIND.get(category, "hard")}
        if not term_present(posting_norm, t):  # guard against requirements the posting never mentions
            continue
        seen.add(key)
        out.append(t)
    return out[:15]


# ---- the four-part match (like Jobright's: skills, seniority, education, industry) ----------------------------------
WEIGHTS = {"skills": 0.5, "seniority": 0.2, "education": 0.15, "industry": 0.15}  # Jobright's are unknown: tune here
BANDS = (0.70, 0.85)  # <= 70% weak, <= 85% good, above that strong (Jobright's less-match / match / strong-match)
_INTERN_TITLE = re.compile(r"\bintern(ship)?\b|\bco-?op\b|\bteaching assistant\b|\bresearch assistant\b", re.I)


def years_experience(profile_text: str, today=None) -> float:
    """Years of work from the profile's "## Work Experience" ("### Title — Company" then "2023-08 to Present"),
    overlapping roles counted once; internships and assistantships don't count."""
    from datetime import date

    today = today or date.today()
    m = re.search(r"^## Work Experience\s*$(.*?)(?=^## |\Z)", profile_text, re.M | re.S)
    spans = []
    for title, body in re.findall(r"^### (.+?)$(.*?)(?=^### |\Z)", m.group(1) if m else "", re.M | re.S):
        d = re.search(r"(\d{4})-(\d{2})\s+to\s+(?:(\d{4})-(\d{2})|Present)", body)
        if not d or _INTERN_TITLE.search(title):
            continue
        start = int(d[1]) * 12 + int(d[2]) - 1
        end = int(d[3]) * 12 + int(d[4]) - 1 if d[3] else today.year * 12 + today.month - 1
        if end > start:
            spans.append((start, end))
    months, last = 0, -1
    for a, b in sorted(spans):
        a = max(a, last)
        if b > a:
            months, last = months + b - a, b
    return round(months / 12, 1)


def degree_level(education) -> str:
    """Highest degree on the profile, in progress or done ('bachelor', 'master', ...)."""
    best = "none"
    for e in education or []:
        d = e.degree.lower()
        lvl = ("phd" if "doctor" in d or "ph.d" in d or "phd" in d else "master" if "master" in d or re.search(r"\bm\.?s\b", d)
               else "bachelor" if "bachelor" in d or re.search(r"\bb\.?(s|e|a|tech)\b", d) else "associate" if "associate" in d else "none")
        best = max(best, lvl, key=DEGREE_RANK.__getitem__)
    return best


def band(pct: float | None) -> str:
    return "none" if pct is None else "weak" if pct <= BANDS[0] else "good" if pct <= BANDS[1] else "strong"


def breakdown(profile, resume_text: str, terms: list[dict], quals: dict | None = None,
              semantic: set[str] | None = None) -> dict:
    """{pct, band, parts: {skills, seniority, education, industry}, reasons: [top 3], yoe}: plain code, repeatable.

    skills    = the ATS coverage of the posting's terms (score()['pct'])
    seniority = the profile's years of work against the posting's minimum (or its level when it gives none)
    education = the highest degree against the one required
    industry  = the domains the posting asks for that the profile or resume mentions
    """
    quals = quals or {}
    s = score(resume_text, terms, semantic)
    yoe = years_experience(profile.text)
    reasons: list[tuple[float, str]] = []  # (how much it costs, what to say): the biggest gaps first

    need = quals.get("min_yoe")
    level = quals.get("seniority")
    if need:
        seniority = 1.0 if yoe >= need else round(1 - (need - yoe) / (need + 1), 3)
        if yoe < need:
            reasons.append((1 - seniority, f"{need} yrs required, you have {yoe:g}"))
    elif level in ("senior", "lead"):
        seniority = 1.0 if yoe >= 5 else 0.2
        if yoe < 5:
            reasons.append((0.8, f"{level.title()} role, you have {yoe:g} yrs"))
    elif level == "mid":
        seniority = 1.0 if yoe >= 2 else 0.6
        if yoe < 2:
            reasons.append((0.4, f"Mid-level role, you have {yoe:g} yrs"))
    else:
        seniority = 1.0

    req = (quals.get("education") or {}).get("level") or "none"
    have = degree_level(getattr(profile, "education", []))
    gap = DEGREE_RANK[req] - DEGREE_RANK[have]
    education = 1.0 if gap <= 0 else 0.5 if gap == 1 else 0.25
    if gap > 0:
        reasons.append((1 - education, f"{'Ph.D.' if req == 'phd' else req.title() + chr(39) + 's'} required"))

    domains = quals.get("industry") or []
    evidence = norm_text(profile.text + "\n" + resume_text)
    shown = [d for d in domains if has_term(evidence, d)]
    industry = len(shown) / len(domains) if domains else 1.0
    if domains and not shown:
        reasons.append((1.0, f"No {domains[0]} experience shown"))

    required_missing = [t["term"] for t in terms if t.get("required") and t["term"] in s["missing"]]
    if required_missing:
        reasons.append((2.0, "Missing: " + ", ".join(required_missing[:3])))
    elif s["missing"]:
        reasons.append((0.5, "Missing: " + ", ".join(s["missing"][:3])))

    parts = {"skills": round(s["pct"], 4), "seniority": round(seniority, 4), "education": education,
             "industry": round(industry, 4)}
    pct = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 4)
    if s["matched"]:  # what's going for you, after the gaps
        reasons.append((0.0, f"Matches {len(s['matched'])} of {len(terms)} skills"))
    if shown:
        reasons.append((0.0, f"{shown[0][:1].upper() + shown[0][1:]} experience"))
    return {"pct": pct, "band": band(pct), "parts": parts, "yoe": yoe,
            "reasons": [r for _, r in sorted(reasons, key=lambda x: -x[0])][:3]}
