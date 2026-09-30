"""Score each base resume against a posting and pick the best one (Jev decides, code keeps it honest).

Resumes come from the person's folder (users/<Name>/, added on the dashboard). A resume with LaTeX source is parsed into sections (and can be
tailored); a PDF-only resume is scored on its extracted text (match % and filling work, tailoring doesn't).
"""
from functools import lru_cache

from .. import config, jev
from ..resume_picker import pick_resume
from . import match, tex

MAX_PCT_GAP = 0.10  # Jev may prefer a resume for fit, but never one more than 10 points below the best coverage


def focus(key: str) -> str:
    """How a resume is pitched (the one-line description on My files), used by Jev's resume pick."""
    return config.RESUME_FOCUS.get(key) or f"{config.RESUME_LABELS.get(key, key)} resume"


@lru_cache(maxsize=1)
def base_docs() -> dict[str, dict]:
    """key -> {src, units, text, skills_text, bullets_text, tailorable} for every base resume that can be read."""
    docs = {}
    for key, pdf in config.RESUMES.items():
        tex_path = config.RESUME_TEX.get(key)
        if tex_path:
            try:
                src = tex_path.read_text(encoding="utf-8")
                units = tex.parse(src)
                docs[key] = {
                    "src": src, "units": units, "text": tex.document_plain(src), "tailorable": True,
                    "skills_text": " ".join(tex.plain(u.text) for u in units if u.kind == "skill"),
                    "bullets_text": " ".join(tex.plain(u.text) for u in units if u.kind == "bullet"),
                }
                continue
            except (OSError, ValueError) as e:
                print(f"    {key}: LaTeX unreadable ({e}); using the PDF text instead")
        try:
            from ..cover_letter import resume_text

            text = resume_text(str(pdf))
        except Exception as e:
            print(f"    {key}: resume PDF unreadable ({type(e).__name__}); skipped")
            continue
        # PDF only: no sections, so Skills-vs-bullets placement checks are skipped and it can't be tailored
        docs[key] = {"src": "", "units": [], "text": text, "tailorable": False, "skills_text": None, "bullets_text": None}
    return docs


SEMANTIC_MIN = 0.8  # Jev must be this sure a resume shows a skill under another name


def jev_equivalents(docs: dict[str, dict], terms: list[dict], job_id=None) -> dict[str, set[str]]:
    """Per resume: missing terms the resume demonstrates under another name ("Git" -> version control).

    One Jev request per resume (all its missing terms as yes/no questions). Empty sets if Jev is unavailable."""
    out: dict[str, set[str]] = {k: set() for k in docs}
    for key, d in docs.items():
        tn = match.norm_text(d["text"])
        missing = [t for t in terms if not match.term_present(tn, t)]
        if not missing:
            continue
        qs = {f"t{i}": jev.noul(
            f"Does this resume show real experience with \"{t['term']}\", even if it is named differently "
            f"(for example a specific tool that is an instance of it, or a standard synonym)?",
            f"The resume clearly demonstrates {t['term']} or a direct equivalent.",
            f"The resume does not demonstrate {t['term']}; at most something loosely related.")
            for i, t in enumerate(missing)}
        try:
            ans = jev.decide(d["text"], qs, session_id=f"job-{job_id}-equiv-{key}", purpose=f"equivalents ({key})")
        except Exception as e:
            print(f"    Jev equivalence check unavailable: {str(e)[:80]}")
            return {k: set() for k in docs}
        for i, t in enumerate(missing):
            if float((ans.get(f"t{i}") or {}).get("noul") or 0) >= SEMANTIC_MIN:
                out[key].add(t["term"])
    return out


def evidence_text(profile_text: str) -> str:
    """Everything the candidate can truthfully claim: the profile plus all three resumes."""
    return profile_text + "\n" + "\n".join(d["text"] for d in base_docs().values())


def jev_pick(job: dict, terms: list[dict], scores: dict[str, dict]) -> tuple[str | None, float]:
    """Ask Jev which resume fits, given the keyword coverage. (None, conf) if it can't decide confidently."""
    required = {t["term"] for t in terms if t.get("required")}
    state = {
        "job": f"{job.get('role')} at {job.get('company')}",
        "required_terms": sorted(required),
        "preferred_terms": [t["term"] for t in terms if not t.get("required")],
        "resumes": {
            k: {
                "focus": focus(k),
                "match_percent": round(s["pct"] * 100),
                "terms_matched": len(s["matched"]),
                "required_terms_matched": len([m for m in s["matched"] if m in required]),
                "missing": s["missing"],
            }
            for k, s in scores.items()
        },
    }
    q = {"resume": jev.choice(
        "Which resume should this candidate send for this job? Prefer the one covering the most required terms, "
        "then the highest overall match percent; use the focus only to break near-ties.",
        {k: f"{focus(k)} ({round(s['pct'] * 100)}% match)" for k, s in scores.items()},
    )}
    ans = jev.decide(state, q, session_id=f"job-{job.get('id', 'x')}-resume", purpose="resume pick")
    return jev.picked(ans.get("resume"))


def choose(job: dict, terms: list[dict], scores: dict[str, dict]) -> tuple[str, str, float]:
    """-> (resume key, chosen_by 'jev'|'score', jev confidence)."""
    heuristic, _ = pick_resume(job.get("role"))
    best = max(scores, key=lambda k: (round(scores[k]["pct"], 4), k == heuristic))
    try:
        key, conf = jev_pick(job, terms, scores)
    except Exception as e:  # Jev unreachable: coverage alone decides
        print(f"    Jev resume pick unavailable: {str(e)[:80]}")
        return best, "score", 0.0
    if key in scores and scores[best]["pct"] - scores[key]["pct"] <= MAX_PCT_GAP + 1e-9:
        return key, "jev", conf
    return best, "score", conf


def evaluate(job: dict) -> dict | None:
    """Match info for a job, or None when there is no usable posting text / no resume source."""
    desc = job.get("description") or ""
    docs = base_docs()
    if len(desc) < 300 or not docs:
        return None
    terms = match.extract_terms(desc, job.get("role"), job.get("company"))
    if len(terms) < 4:
        return None
    equiv = jev_equivalents(docs, terms, job.get("id"))
    scores = {k: match.score(d["text"], terms, equiv[k], d["skills_text"], d["bullets_text"]) for k, d in docs.items()}
    chosen, by, conf = choose(job, terms, scores)
    s = scores[chosen]
    return {
        "terms": terms,
        "scores": {k: {**v, "pct": round(v["pct"], 4), "exact_pct": round(v["exact_pct"], 4)} for k, v in scores.items()},
        "chosen": chosen,
        "chosen_by": by,
        "jev_confidence": round(conf, 2),
        "pct": round(s["pct"], 4),
        "exact_pct": round(s["exact_pct"], 4),
        # what tailoring works on: terms not in exact wording (equivalents first - those are honest rewordings)
        "missing": s["equivalent"] + s["missing"],
        "equivalent": s["equivalent"],
        "skills_only": s["skills_only"],
        "stuffed": s["stuffed"],
        # terms the candidate shows under another name on ANY resume: honest to add in exact wording
        "equivalent_any": sorted(set().union(*equiv.values())) if equiv else [],
        # per-person resume names, so the dashboard shows "SWE" / "Data Eng" etc. instead of internal keys
        "labels": {k: config.RESUME_LABELS.get(k, k) for k in scores},
        "tailorable": bool(docs[chosen].get("tailorable")),
    }
