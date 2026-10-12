"""Skill matching: add the posting's missing skills to the Technical Skills section of the best base resume.

Only the Skills lines change: bullets, employers, dates and layout never do. One suggestion per missing skill,
placed on the fitting Skills line (Jev decides which line; code appends the skill in the posting's exact wording):
- skills backed by Rishi's own materials (any resume or the profile, or shown there under another name) are
  suggested ticked;
- skills found nowhere in his materials are still suggested, but unticked and marked "add only if true".
No chat model is involved, so a draft takes about a second.
"""
import difflib
import re

from .. import config, jev
from . import match, select, tex, word

MAX_SKILLS = 15
_TOKEN = re.compile(r"[A-Za-z0-9+#./'-]+")

# fallback placement when Jev is unavailable: category -> words that identify a Skills line label
_LINE_HINTS = {
    "language": ("language", "engineering"),
    "framework": ("framework", "frontend", "backend", "full stack", "product", "ml", "ai"),
    "database": ("database", "data", "store"),
    "tool": ("tool", "infra", "cloud", "devops", "engineering", "delivery", "integration"),
    "platform": ("cloud", "tool", "infra", "platform", "devops", "integration", "engineering"),
    "certification": ("tool", "infra", "engineering"),
    "concept": ("system", "backend", "ai", "engineering", "integration", "ways"),
}


def word_diff(old: str, new: str) -> tuple[list[str], list[str]]:
    """Words added / removed, computed from the two texts."""
    a, b = _TOKEN.findall(old), _TOKEN.findall(new)
    sm = difflib.SequenceMatcher(None, [x.lower() for x in a], [x.lower() for x in b])
    added, removed = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("replace", "delete"):
            removed += a[i1:i2]
        if op in ("replace", "insert"):
            added += b[j1:j2]
    return added, removed


def _evidence_snippet(t: dict, evidence: str) -> str:
    """A short quote from Rishi's materials that contains the skill (for the review screen)."""
    for name in [t["term"], *t.get("aliases", [])]:
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", evidence, re.I)
        if m:
            a, b = max(0, m.start() - 60), min(len(evidence), m.end() + 60)
            return "…" + re.sub(r"\s+", " ", evidence[a:b]).strip() + "…"
    return ""


def addable_terms(info: dict, profile_text: str) -> list[str]:
    """Missing skills that could be added (backed ones first). Empty = nothing to tailor."""
    ev_norm = match.norm_text(select.evidence_text(profile_text))
    missing = set(info["missing"])
    equivalent = set(info.get("equivalent_any", []))
    terms = [t for t in info["terms"] if t["term"] in missing]
    backed = [t["term"] for t in terms if match.supported(t, ev_norm) or t["term"] in equivalent]
    return backed + [t["term"] for t in terms if t["term"] not in backed]


def _place(skills: list[dict], lines: list[tex.Unit], job_id) -> dict[str, str]:
    """skill term -> unit id of the Skills line it belongs on. Jev decides; category hints are the fallback."""
    def fallback(t):
        hints = _LINE_HINTS.get(t.get("category", "concept"), ())
        for h in hints:
            for u in lines:
                if h in u.context.lower():
                    return u.id
        return lines[-1].id

    crit = {u.id: f"{u.context}: {tex.plain(u.text)[:160]}" for u in lines}
    qs = {f"s{i}": jev.choice(f"Which Technical Skills line of a resume should list \"{t['term']}\" "
                              f"(a {t.get('category', 'skill')})?", crit) for i, t in enumerate(skills)}
    try:
        ans = jev.decide({"skill_lines": crit}, qs, session_id=f"job-{job_id}-skills", purpose="skill placement")
    except Exception as e:
        print(f"    Jev skill placement unavailable, using categories: {str(e)[:80]}")
        ans = {}
    out = {}
    for i, t in enumerate(skills):
        key, _conf = jev.picked(ans.get(f"s{i}"), min_confidence=0.3)
        out[t["term"]] = key if key in crit else fallback(t)
    return out


def compose(unit_text: str, skills_tex: list[str]) -> str:
    """The Skills line with extra items appended, in the line's own comma-separated style."""
    base = unit_text.rstrip()
    return base + "".join(f", {s}" for s in skills_tex)


def propose(job: dict, info: dict, profile_text: str) -> dict:
    docs = select.base_docs()
    key = info["chosen"]
    doc = docs[key]
    evidence = select.evidence_text(profile_text)
    ev_norm = match.norm_text(evidence)
    equivalent = set(info.get("equivalent_any", []))

    missing = set(info["missing"])
    wanted = [t for t in info["terms"] if t["term"] in missing][:MAX_SKILLS]
    result = {
        "mode": "skills", "base": key, "pct_before": info["pct"], "pct_after": info["pct"],
        "threshold": config.RESUME_MATCH_THRESHOLD, "edits": [], "gaps": [], "problems": [],
    }
    if not doc.get("tailorable", True):
        result["problems"].append({"kind": "no_latex", "detail": "This resume is PDF-only: add its Word (.docx) or "
                                   "LaTeX (.tex) source on the My files tab to enable tailoring."})
        return result
    is_docx = doc.get("format") == "docx"
    result["format"] = doc.get("format", "tex")
    shown = (lambda s: s) if is_docx else tex.plain  # Word text is already plain
    join = word.compose if is_docx else compose
    lines = [u for u in doc["units"] if u.kind == "skill" and (is_docx or tex.is_editable(u))]
    if not wanted:
        result["problems"].append({"kind": "nothing_to_add", "detail": "Every skill in the posting is already on this resume."})
        return result
    if not lines:
        result["problems"].append({"kind": "no_skills_section", "detail": "Couldn't find an editable Technical Skills section."})
        return result

    placement = _place(wanted, lines, job.get("id"))
    by_id = {u.id: u for u in lines}
    for t in wanted:
        unit = by_id[placement[t["term"]]]
        backed = match.supported(t, ev_norm)
        shown_as = not backed and t["term"] in equivalent
        skill_tex = t["term"] if is_docx else tex.from_markup(t["term"])
        after_tex = join(unit.text, [skill_tex])
        added_words, removed_words = word_diff(shown(unit.text), shown(after_tex))
        result["edits"].append({
            "id": f"e{len(result['edits']) + 1}", "unit_id": unit.id, "kind": "skill", "context": unit.context,
            "skill": t["term"], "skill_tex": skill_tex, "category": t.get("category", "concept"),
            "required": bool(t.get("required")),
            "support": "backed" if backed else ("shown_as" if shown_as else "not_found"),
            "default": backed or shown_as,
            "before": shown(unit.text), "after": shown(after_tex),
            "before_tex": unit.text, "after_tex": after_tex,
            "keywords_added": [t["term"]],
            "evidence": (_evidence_snippet(t, evidence) if backed else
                         "Shown under another name on your resumes (Jev)" if shown_as else ""),
            "added_words": added_words, "removed_words": removed_words,
        })
        if not (backed or shown_as):
            result["gaps"].append(t["term"])

    # projected match with the default (backed) skills added
    defaults = [e for e in result["edits"] if e["default"]]
    if is_docx:
        text_with = lambda edits: word.apply(config.RESUME_DOCX[key], additions(edits))[1]
    else:
        text_with = lambda edits: tex.document_plain(apply(doc["src"], doc["units"], edits))
    after = match.score(text_with(defaults), info["terms"], set(info.get("equivalent", [])))
    result["pct_after"] = round(after["pct"], 4)
    result["pct_if_all"] = round(match.score(text_with(result["edits"]), info["terms"])["pct"], 4)
    if result["gaps"]:
        result["problems"].append({
            "kind": "not_in_materials", "terms": result["gaps"],
            "detail": "Not in any of your resumes or your profile: suggested unticked. Tick one only if you really have it.",
        })
    if result["pct_after"] < config.RESUME_MATCH_THRESHOLD:
        result["problems"].append({
            "kind": "below_threshold",
            "detail": f"With the backed skills added the match is {result['pct_after']:.0%} "
                      f"({result['pct_if_all']:.0%} if you tick every skill), under the {config.RESUME_MATCH_THRESHOLD:.0%} target.",
        })
    return result


def additions(edits: list[dict]) -> dict[str, list[str]]:
    """unit id -> the skills to append to that line (several skills on one line go in together)."""
    per_line: dict[str, list[str]] = {}
    for e in edits:
        per_line.setdefault(e["unit_id"], []).append(e.get("skill_tex") or tex.from_markup(e["keywords_added"][0]))
    return per_line


def apply(src: str, units: list[tex.Unit], edits: list[dict]) -> str:
    """Apply a set of skill suggestions to the LaTeX source."""
    per_line = additions(edits)
    by_id = {u.id: u for u in units}
    return tex.apply_edits(src, units, {uid: compose(by_id[uid].text, s) for uid, s in per_line.items() if uid in by_id})
