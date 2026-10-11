"""Build an honest, tailored resume for one job from the experience bank.

    plan      the planner reads the posting and the bank, and decides the layout: 1 or 2 pages, which sections in which
              order, which entries go in and how many bullets each gets
    write     the writer drafts every bullet in the posting's terminology; each bullet quotes the bank passage it's from
    critique  a second model plays a skeptical hiring manager and fact-checker (unsupported or inflated claims, numbers,
              missing terms the bank supports, layout, AI-sounding wording); the writer revises, up to two rounds
    verify    code checks: every quote must really be in its entry and every number in a bullet must be in that entry;
              anything that fails is dropped and listed
    render    the candidate's own LaTeX template (preamble, header, education copied as they are), trimmed to fit
The result waits in Resume review: nothing is used until the person approves it.
"""
import json
import os
import re
from datetime import date
from difflib import SequenceMatcher

from .. import bank, config, llm
from ..style import STYLE, clean
from . import match, tex
from .tailor import _LINE_HINTS  # where a posting skill goes: words that identify the fitting Skills line

SECTION_KINDS = {
    "experience": ("experience",), "research": ("research",), "projects": ("project",), "publications": ("publication",),
    "leadership": ("leadership",), "awards": ("award",),
}
DEFAULT_HEADINGS = {"education": "Education", "experience": "Work Experience", "research": "Research Experience",
                    "projects": "Projects", "publications": "Publications", "leadership": "Leadership",
                    "awards": "Awards", "skills": "Technical Skills"}


# first match wins: "Research Experience" is research, "Projects & Awards" is projects
_HEADING_TYPES = [("education", r"educat"), ("publications", r"publicat|papers"), ("research", r"research"),
                  ("projects", r"project"), ("experience", r"experience|employment|\bwork\b"),
                  ("leadership", r"leadership|activit|involvement|volunteer"), ("awards", r"award|honou?r|achievement"),
                  ("skills", r"skill")]


def own_headings(base_src: str | None) -> dict[str, str]:
    """Section titles from the person's own resume, by type, e.g. {"projects": "Projects & Awards"}: a resume built
    from the bank keeps their wording instead of whatever the planner calls a section."""
    out: dict[str, str] = {}
    for raw in re.findall(r"\\section\*?\{([^}]*)\}", base_src or ""):
        title = re.sub(r"\\([&%$#_])", r"\1", raw).strip()
        for t, pat in _HEADING_TYPES:
            if re.search(pat, title, re.I):
                out.setdefault(t, title)
                break
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%.+# ]", " ", (s or "").lower())).strip()


def candidate_level(profile) -> str:
    edu = profile.education[0] if profile.education else None
    today = date.today()
    if edu and (edu.end_year, edu.end_month) >= (today.year, today.month):
        return f"student, graduating {edu.end_year}-{edu.end_month:02d} ({edu.degree})"
    return f"graduated {edu.end_year} ({edu.degree})" if edu else "unknown"


# ---- 1. plan ---------------------------------------------------------------------------------------------------
_PLAN_SYSTEM = """You are an expert technical recruiter laying out a resume for ONE job, choosing from the candidate's
experience bank. Decide:
- pages: 1 or 2. Students, new grads and anyone under ~5 years of experience get 1 page unless the posting is research /
  PhD-level or the bank has substantial publications or research that this job values; then 2 is allowed.
- sections, in the order that best sells this candidate for THIS job (e.g. research roles lead with Research and
  Publications; product engineering roles lead with Experience then Projects). Education is always included (first for
  students and new grads unless the job is senior). Skills is always included. Section types: education, experience,
  research, projects, publications, leadership, awards, skills. For a section type listed under YOUR HEADINGS, use that
  heading exactly; name any other section plainly ("Publications", "Research Experience"). Education and Skills are
  filled in from the candidate's own resume and skills list, so give them no entries and don't comment on them.
- entries: pick the bank entries (by id) that best match the posting's requirements, most relevant first within each
  section. Fill the page: a 1-page resume holds about 16-20 bullets in total, so after the most relevant entries, use
  the next-best ones (a related project still beats empty space). Each picked entry gets a bullet count (1-5)
  reflecting its relevance and how much real material it has. The candidate's current or most recent job gets the
  most bullets (4-5): it's what a recruiter reads first.
- reserve: every other usable entry, best first, with a bullet count: they're added only if the page still has room.
Use only entries whose kind fits the section. Never invent entries.
Return JSON: {"pages": 1|2, "rationale": "<two sentences on the layout choice>",
"sections": [{"type": str, "heading": str, "entries": [{"id": str, "bullets": int}]}],
"reserve": [{"id": str, "bullets": int}]}"""

# entry kind -> the section type it goes in
_KIND_SECTION = {k: t for t, kinds in SECTION_KINDS.items() for k in kinds}


_DATED = ("experience", "research", "leadership")
CURRENT_JOB_BULLETS = 4
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _end_key(e: dict) -> tuple[int, int]:
    """'Present' > 'Aug 2025' > 'May 2023'; undated last."""
    end = (e.get("end") or "").strip().lower()
    if end in ("present", "current", "now", "ongoing"):
        return (9999, 12)
    y = re.search(r"(19|20)\d{2}", end)
    return (int(y.group(0)), _MONTHS.get(end[:3], 0)) if y else (0, 0)


def template_sections(base_src: str | None) -> list[str]:
    """Section types of the person's own resume, in their order (e.g. education, experience, projects, skills)."""
    out: list[str] = []
    for raw in re.findall(r"\\section\*?\{([^}]*)\}", base_src or ""):
        title = re.sub(r"\\([&%$#_])", r"\1", raw).strip()
        for t, pat in _HEADING_TYPES:
            if re.search(pat, title, re.I):
                if t not in out:
                    out.append(t)
                break
    return out


def plan(job: dict, info: dict, entries: list[dict], profile, headings: dict[str, str] | None = None,
         template: list[str] | None = None) -> dict:
    headings = headings or {}
    template = template or []
    listing = [{"id": e["id"], "kind": e["kind"], "title": e["title"], "org": e["org"],
                "dates": f"{e['start']} - {e['end']}".strip(" -"), "tags": e["tags"][:12], "text": e["text"][:500]}
               for e in entries if e["kind"] not in ("skills", "narrative", "education")]
    user = (f"JOB: {job.get('role')} at {job.get('company')}\nCANDIDATE: {candidate_level(profile)}\n"
            f"REQUIREMENTS (* = required): {', '.join(('*' if t.get('required') else '') + t['term'] for t in info.get('terms', []))}\n\n"
            f"YOUR HEADINGS (from the candidate's own resume): {json.dumps(headings, ensure_ascii=False)}\n\n"
            f"POSTING:\n{job.get('description', '')[:5000]}\n\nBANK ENTRIES:\n{json.dumps(listing, ensure_ascii=False)}")
    p = llm.chat_json(_PLAN_SYSTEM, user, model=config.model("write"), max_tokens=4000, purpose="bank resume: plan")
    kind_of = {e["id"]: e["kind"] for e in entries}
    usable = {i for i, k in kind_of.items() if k in _KIND_SECTION}

    def bullets(x):
        try:
            return max(1, min(int(x.get("bullets") or 2), 5))
        except (TypeError, ValueError):
            return 2

    def section(t: str, heading: str | None = None) -> dict:
        return by_type.setdefault(t, {"type": t, "entries": [],
                                      "heading": (headings.get(t) or heading or DEFAULT_HEADINGS[t])[:60]})

    by_type: dict[str, dict] = {}
    used: set[str] = set()
    for s in p.get("sections") or []:
        t = str(s.get("type", "")).lower()
        if t not in DEFAULT_HEADINGS:
            continue
        section(t, str(s.get("heading") or ""))
        for x in s.get("entries") or []:
            if isinstance(x, dict) and x.get("id") in usable and x["id"] not in used:
                # an entry goes in its own kind's section, whatever section the model put it under
                section(_KIND_SECTION[kind_of[x["id"]]])["entries"].append({"id": x["id"], "bullets": bullets(x)})
                used.add(x["id"])
    reserve = [{"id": x["id"], "bullets": bullets(x)} for x in p.get("reserve") or []
               if isinstance(x, dict) and x.get("id") in usable and x["id"] not in used]
    reserve_ids = {x["id"] for x in reserve}
    # anything the model forgot about is still a reserve, after the ones it ranked
    reserve += [{"id": i, "bullets": 2} for i in (e["id"] for e in entries) if i in usable and i not in used and i not in reserve_ids]

    # the person's own template decides which sections there are and in what order; Education and Skills always
    order = template or ["education", "experience", "projects", "skills"]
    for t in ("education", "skills"):
        if t not in order:
            order = (["education"] + order) if t == "education" else (order + ["skills"])
    extra = [t for t in by_type if t not in order]  # e.g. Publications when the template has none: just before Skills
    order = [t for t in order if t != "skills"] + extra + ["skills"]
    secs = []
    for t in order:
        if t not in DEFAULT_HEADINGS:
            continue
        sec = by_type.get(t) or {"type": t, "heading": headings.get(t, DEFAULT_HEADINGS[t]), "entries": []}
        if t in SECTION_KINDS and not sec["entries"] and not any(_KIND_SECTION.get(kind_of[r["id"]]) == t for r in reserve):
            continue  # nothing in the bank for this section
        if t in ("education", "skills"):
            sec["entries"] = []
        if t in _DATED:  # jobs read newest first, however relevant the model thought each was
            sec["entries"].sort(key=lambda x: _end_key(next(e for e in entries if e["id"] == x["id"])), reverse=True)
        if t == "experience" and sec["entries"]:  # the current job is read first: it never gets the fewest bullets
            sec["entries"][0]["bullets"] = max(sec["entries"][0]["bullets"], CURRENT_JOB_BULLETS)
        secs.append(sec)
    return {"pages": 2 if p.get("pages") == 2 else 1, "rationale": str(p.get("rationale") or "")[:600], "sections": secs,
            "reserve": reserve, "headings": headings, "template": template}


# ---- 2. write / revise -----------------------------------------------------------------------------------------
_WRITE_SYSTEM = """You write the resume bullets for ONE job from the candidate's own experience-bank entries.
Honesty first:
- Every bullet states only what its entry's text says. `evidence` must be a passage copied word for word from that
  entry's text (one or two consecutive sentences) that supports everything in the bullet.
- Numbers, metrics, scale, team size and outcomes only when they appear in that evidence. Never round up or add.
- Use the posting's exact terminology where the evidence genuinely shows that skill (it helps the ATS); never claim a
  skill, tool or responsibility the evidence doesn't show.
Quality:
- Start with a strong past-tense verb (present tense for current roles). One idea per bullet, 1-2 lines
  (about 90-200 characters). Lead with the result or the technical substance, not the duty.
- Mark at most two key terms per bullet as **bold** (the posting's requirements).
- Never repeat a fact, number or achievement in two bullets (on the whole resume, each claim appears once).
- Write exactly the number of bullets the plan gives each entry (fewer only if the entry lacks material). Then, in
  `spare`, up to 2 more good bullets per entry from material you didn't use (same honesty rules): they're added only
  if the page has room. Entries marked "reserve": true are next-best material, used only if the page has room: write
  their bullets too, same rules.
- Skills: list the skills from the bank or profile that this posting asks for, as group lines ("Languages",
  "Frameworks", "Cloud & Tools"...). They're merged into the candidate's own Skills section by code, so don't repeat
  their whole list. Real skill names only (no generic words like "systems").
""" + STYLE + """
Return JSON: {"entries": {"<entry id>": {"bullets": [{"text": str, "evidence": str, "terms": [str]}],
"spare": [{"text": str, "evidence": str, "terms": [str]}]}}, "skills": [{"group": str, "items": "comma, separated"}]}"""


def _entries_for(plan_: dict, by_id: dict) -> list[dict]:
    return [by_id[x["id"]] for s in plan_["sections"] for x in s["entries"] if x["id"] in by_id]


RESERVE_WRITTEN = 3  # the writer drafts the best few reserve entries: all of them overflows one reply


def _reserve_for(plan_: dict, by_id: dict) -> list[dict]:
    return [by_id[x["id"]] for x in (plan_.get("reserve") or [])[:RESERVE_WRITTEN] if x["id"] in by_id]


def write(job: dict, info: dict, plan_: dict, by_id: dict, skills_source: str, critique_: dict | None = None,
          previous: dict | None = None) -> dict:
    wanted = {x["id"]: x["bullets"] for s in plan_["sections"] for x in s["entries"]}
    wanted.update({x["id"]: x["bullets"] for x in (plan_.get("reserve") or [])[:RESERVE_WRITTEN]})
    picked = [{"id": e["id"], "kind": e["kind"], "title": e["title"], "org": e["org"], "bullets_wanted": wanted[e["id"]],
               "text": e["text"]} for e in _entries_for(plan_, by_id)]
    picked += [{"id": e["id"], "kind": e["kind"], "title": e["title"], "org": e["org"], "bullets_wanted": wanted[e["id"]],
                "reserve": True, "text": e["text"]} for e in _reserve_for(plan_, by_id)]
    user = (f"JOB: {job.get('role')} at {job.get('company')}\n"
            f"REQUIREMENTS (* = required): {', '.join(('*' if t.get('required') else '') + t['term'] for t in info.get('terms', []))}\n\n"
            f"POSTING:\n{job.get('description', '')[:4000]}\n\nENTRIES:\n{json.dumps(picked, ensure_ascii=False)}\n\n"
            f"SKILLS IN THE BANK / PROFILE:\n{skills_source[:3000]}")
    if previous and critique_:
        user += (f"\n\nYOUR PREVIOUS DRAFT:\n{json.dumps(previous, ensure_ascii=False)}\n\n"
                 f"REVIEWER'S FINDINGS (fix every high and medium one; keep what was fine):\n"
                 f"{json.dumps(critique_.get('issues', []), ensure_ascii=False)}")
    return llm.chat_json(_WRITE_SYSTEM, user, model=config.model("write"), max_tokens=16000,
                         purpose="bank resume: revise" if previous else "bank resume: write")


# ---- 3. adversarial review ---------------------------------------------------------------------------------------
_CRITIC_SYSTEM = """You are the adversary: a skeptical hiring manager for this job AND a strict fact-checker. You did not
write this resume and your job is to find what's wrong with it. Check:
- unsupported: a bullet claims something its quoted evidence (or the entry text) doesn't say
- inflated: stronger wording than the evidence supports (led vs helped, built vs used, scale or ownership overstated)
- number: a number that isn't in the evidence, or is changed
- missing_term: an important requirement of the posting that the entries DO support but no bullet shows
- weak: vague, duty-like or low-impact bullet when the entry has better material; repetitive bullets
- ai_tone: sounds machine-written (buzzwords, em dashes, "leveraged", "spearheaded", "passionate", rule-of-three lists)
- layout: section order, page choice, or an entry that's irrelevant for this job / a more relevant entry left out
Severity: high = a false or unsupported claim, a wrong number, or a requirement the bank supports that's missing
entirely; medium = clearly overstated wording or a much stronger entry/bullet left out; low = phrasing and polish.
Only the bank counts: never suggest adding a skill, tool or experience unless it appears in an entry's text or in
SKILLS IN THE BANK below. If the posting wants something the bank doesn't show, that's not an issue for this resume
(no "the candidate may have..." or "consider adding a project"). Education, the Skills section and the contact header
are built by code from the candidate's own resume: don't review them or call them empty or missing.
Be concrete: point at the entry id and bullet index, say exactly what's wrong and how to fix it with the candidate's
real material. Don't invent problems: an accurate, specific bullet is fine, and close synonyms of the evidence's own verb
(built / designed and built) are not inflation.
Return JSON: {"verdict": "ok" | "revise", "score": 0-10, "issues": [{"entry": "<id or 'layout' or 'skills'>",
"bullet": <index or null>, "severity": "high"|"medium"|"low", "type": str, "problem": str, "fix": str}]}"""


_SPECULATIVE = re.compile(r"\bmay have\b|\bmight have\b|if (the candidate|they) ha(s|ve)\b|consider adding a (project|coursework)", re.I)


def critique(job: dict, info: dict, plan_: dict, draft: dict, by_id: dict, skills_source: str = "") -> dict:
    view = {"pages": plan_["pages"], "sections": [
        {"heading": s["heading"], "type": s["type"], "note": "typeset from the candidate's own resume; not reviewed"}
        if s["type"] in ("education", "skills") else
        {"heading": s["heading"], "type": s["type"],
         "entries": [{"id": x["id"], "title": by_id[x["id"]]["title"], "org": by_id[x["id"]]["org"],
                      "entry_text": by_id[x["id"]]["text"][:2500],
                      "bullets": (draft.get("entries", {}).get(x["id"]) or {}).get("bullets", [])}
                     for x in s["entries"] if x["id"] in by_id]} for s in plan_["sections"]]}
    user = (f"JOB: {job.get('role')} at {job.get('company')}\n"
            f"REQUIREMENTS (* = required): {', '.join(('*' if t.get('required') else '') + t['term'] for t in info.get('terms', []))}\n\n"
            f"POSTING:\n{job.get('description', '')[:4000]}\n\nRESUME DRAFT:\n{json.dumps(view, ensure_ascii=False)}\n\n"
            f"SKILLS IN THE BANK / PROFILE:\n{skills_source[:3000]}\n\n"
            f"OTHER BANK ENTRIES NOT USED (titles): {', '.join(e['title'] for e in by_id.values() if e['id'] not in {x['id'] for s in plan_['sections'] for x in s['entries']})[:1500]}")
    model = os.getenv("LLM_MODEL_CRITIC") or config.model("fast")  # a different model than the writer, on purpose
    out = llm.chat_json(_CRITIC_SYSTEM, user, model=model, max_tokens=5000, purpose="bank resume: adversarial review")
    # "add C++ if the candidate has it" is not a finding: only what the bank shows can go on the resume
    # the Skills section is built by code from the person's own resume, so a model's opinion of it is not a finding
    issues = [i for i in out.get("issues") or [] if isinstance(i, dict) and str(i.get("entry", "")).lower() != "skills"
              and not _SPECULATIVE.search(f"{i.get('problem', '')} {i.get('fix', '')}")]
    return {"verdict": out.get("verdict", "revise"), "score": out.get("score"), "issues": issues, "model": model}


# ---- 4. verify (code, not a model) --------------------------------------------------------------------------------
_NUM = re.compile(r"\d[\d,.]*\+?%?")


def _found(evidence: str, text: str) -> bool:
    ev, tx = _norm(evidence), _norm(text)
    if len(ev) < 12:
        return False
    if ev in tx:
        return True
    m = SequenceMatcher(None, ev, tx, autojunk=False).find_longest_match(0, len(ev), 0, len(tx))
    return m.size >= 0.85 * len(ev)


def verify(draft: dict, by_id: dict) -> tuple[dict, list[dict]]:
    """Keep only bullets whose quote is really in their entry and whose numbers come from that entry."""
    dropped = []
    out = {"entries": {}, "skills": draft.get("skills") or []}
    raw = draft.get("entries") or {}
    if isinstance(raw, list):  # a model sometimes returns [{"id": ..., "bullets": [...]}]
        raw = {x.get("id"): x for x in raw if isinstance(x, dict) and x.get("id")}
    for eid, block in raw.items():
        e = by_id.get(eid)
        if not e:
            continue
        src = e["text"].replace(",", "")
        kept: dict[str, list] = {"bullets": [], "spare": []}
        for part in ("bullets", "spare"):
            for b in block.get(part) or []:
                if b.get("edited"):  # typed by the person in Resume review: their own words
                    kept[part].append(b)
                    continue
                text = clean(str(b.get("text") or "").strip())
                if not text:
                    continue
                why = None
                if not _found(str(b.get("evidence") or ""), e["text"]):
                    why = "its quoted evidence isn't in the entry"
                else:
                    bad = [n for n in _NUM.findall(text.replace("**", "")) if n.replace(",", "").rstrip(".%+") not in src]
                    if bad:
                        why = f"number(s) not in the entry: {', '.join(bad)}"
                if why:
                    if part == "bullets":
                        dropped.append({"entry": eid, "title": e["title"], "text": text, "reason": why})
                else:
                    kept[part].append({"text": text, "evidence": b.get("evidence", ""), "terms": b.get("terms") or []})
        out["entries"][eid] = kept
    # skills: only items that actually appear in the bank or profile
    return out, dropped


def verify_skills(skills: list[dict], source: str) -> list[dict]:
    src = _norm(source)
    good = []
    for g in skills:
        items = [i.strip() for i in str(g.get("items") or "").split(",") if i.strip()]
        items = [i for i in items if _norm(i) and _norm(i) in src]
        if items:
            good.append({"group": clean(str(g.get("group") or "Skills"))[:40], "items": ", ".join(items)})
    return good


def base_skill_lines(base_key: str | None) -> list[dict]:
    """The Skills lines of the person's own base resume: [{group, items}]."""
    from .select import base_docs

    docs = base_docs()
    doc = docs.get(base_key) or next(iter(docs.values()), None)
    if not doc or "units" not in doc:
        return []
    return [{"group": clean(u.context or "Skills")[:40], "items": tex.plain(u.text).strip().rstrip(".")}
            for u in doc["units"] if u.kind == "skill" and tex.plain(u.text).strip()]


_ADDABLE = {"framework", "tool", "platform", "database", "language"}  # concepts ("databases", "networks") are only offered


def _items(line: dict) -> list[str]:
    return [i.strip() for i in re.split(r",(?![^()]*\))", str(line.get("items") or "")) if i.strip()]


def _line_for(lines: list[dict], *words: str) -> dict:
    for w in words:
        for ln in lines:
            if w in ln["group"].lower():
                return ln
    return lines[-1]


def merge_skills(base_lines: list[dict], writer_lines: list[dict], terms: list[dict], base_text: str,
                 shown_text: str, backed_text: str) -> tuple[list[dict], list[dict], list[str]]:
    """The Skills section: the base resume's own lines (nothing of the template is lost), the posting's requirements
    first within each line, plus the writer's bank-backed extras, plus the posting's other missing skills. A programming
    language goes on only if the base resume already lists it.
    -> (lines, added [{skill, group, backed}], excluded languages)"""
    lines = [{"group": ln["group"], "items": ", ".join(_items(ln))} for ln in base_lines] or \
            [{"group": clean(str(g.get("group") or "Skills"))[:40], "items": ", ".join(_items(g))} for g in writer_lines]
    if not lines:
        lines = [{"group": "Skills", "items": ""}]

    def on_resume(name: str) -> bool:  # "Neo4j" is already there when a line says "Neo4j (Cypher)"
        n = match.norm_text(name).strip()
        return bool(n) and match.has_term(match.norm_text(" , ".join(ln["items"] for ln in lines)), n)

    def add(ln: dict, item: str):
        ln["items"] = ", ".join([*_items(ln), item])

    for g in writer_lines:  # already checked against the bank by verify_skills
        target = next((ln for ln in lines if match.norm_text(ln["group"]) == match.norm_text(str(g.get("group") or ""))), None)
        for item in _items(g):
            if not on_resume(item):
                add(target or _line_for(lines, *match.norm_text(str(g.get("group") or "")).split()), item)

    base_norm, backed_norm = match.norm_text(base_text), match.norm_text(backed_text)
    resume_norm = lambda: match.norm_text(" ".join(ln["items"] for ln in lines) + " " + shown_text)  # noqa: E731
    added, excluded = [], []
    for t in terms:
        cat = t.get("category", "concept")
        parts = [x for x in re.split(r"\s*/\s*", t["term"]) if x]  # "Node.js/Express" is there when both are
        if match.term_present(resume_norm(), t) or all(match.has_term(resume_norm(), x) for x in parts):
            continue
        if cat == "concept":  # a broad idea, not a skill name: listed in review, unticked, for you to add
            added.append({"skill": t["term"], "group": _line_for(lines, *_LINE_HINTS["concept"])["group"],
                          "backed": match.term_present(backed_norm, t), "suggested": True})
            continue
        if cat not in _ADDABLE:
            continue
        if cat == "language" and not match.term_present(base_norm, t):
            excluded.append(t["term"])
            continue
        ln = _line_for(lines, *_LINE_HINTS.get(cat, ()))
        add(ln, t["term"])
        added.append({"skill": t["term"], "group": ln["group"], "backed": match.term_present(backed_norm, t)})

    # the posting's requirements first within each line (exact wording helps the ATS)
    for ln in lines:
        items = _items(ln)
        wanted = [i for i in items if any(match.term_present(match.norm_text(i), t) for t in terms)]
        ln["items"] = ", ".join(wanted + [i for i in items if i not in wanted])
    return [ln for ln in lines if ln["items"]], added, excluded


# ---- 5. render -----------------------------------------------------------------------------------------------------
_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "|": r"$|$", "<": r"$<$", ">": r"$>$"}


def esc(s: str) -> str:
    s = "".join(_ESC.get(c, c) for c in (s or ""))
    return re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", s)


_JAKE_PREAMBLE = r"""\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}\usepackage[empty]{fullpage}\usepackage{titlesec}\usepackage[usenames,dvipsnames]{color}
\usepackage{enumitem}\usepackage[hidelinks]{hyperref}\usepackage{fancyhdr}\usepackage[english]{babel}\usepackage{tabularx}
\pagestyle{fancy}\fancyhf{}\fancyfoot{}\renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0pt}
\addtolength{\oddsidemargin}{-0.5in}\addtolength{\evensidemargin}{-0.5in}\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}\addtolength{\textheight}{1.0in}\urlstyle{same}\raggedbottom\raggedright
\setlength{\tabcolsep}{0in}
\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]
\newcommand{\resumeItem}[1]{\item\small{{#1 \vspace{-2pt}}}}
\newcommand{\resumeSubheading}[4]{\vspace{-2pt}\item
  \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}\textbf{#1} & #2 \\ \textit{\small#3} & \textit{\small #4} \\
  \end{tabular*}\vspace{-7pt}}
\newcommand{\resumeProjectHeading}[2]{\item\begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}\small#1 & #2 \\
  \end{tabular*}\vspace{-7pt}}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
"""


def _template(base_src: str | None, profile) -> tuple[str, str, str]:
    """(preamble, header, education block) from the person's own LaTeX resume, or a clean default built from the profile."""
    if base_src and "\\begin{document}" in base_src and "\\resumeSubheading" in base_src:
        pre, body = base_src.split("\\begin{document}", 1)
        # "{#1 \vspace...}" in \resumeItem: a bullet that exactly fills its line got an empty line after it
        pre = re.sub(r"#1\s+\\vspace", r"#1\\vspace", pre)
        first = body.find("\\section")
        header = body[:first] if first > 0 else ""
        m = re.search(r"\\section\{Education\}.*?(?=\\section\{|\\end\{document\})", body, re.S)
        edu = m.group(0).strip() if m else ""
        return pre, header, edu
    contact = " $|$ ".join(esc(x) for x in (profile.city, profile.phone, profile.email) if x)
    links = " $|$ ".join(f"\\href{{{u}}}{{\\underline{{{n}}}}}" for n, u in (("LinkedIn", profile.linkedin), ("GitHub", profile.github)) if u)
    header = (f"\\begin{{center}}\n  \\textbf{{\\Huge \\scshape {esc(profile.full_name)}}} \\\\ \\vspace{{1pt}}\n"
              f"  \\small{{{contact}}}" + (f" $|$ {links}" if links else "") + "\n\\end{center}\n")
    edu_items = "\n".join(
        f"    \\resumeSubheading{{{esc(e.school)}}}{{}}{{{esc(e.degree)}" + (f", {esc(e.field)}" if e.field else "")
        + (f", GPA {esc(e.gpa)}" if e.gpa else "") + f"}}{{{e.start_year} - {e.end_year}}}" for e in profile.education)
    edu = f"\\section{{Education}}\n  \\resumeSubHeadingListStart\n{edu_items}\n  \\resumeSubHeadingListEnd" if edu_items else ""
    return _JAKE_PREAMBLE, header, edu


def _project_key(title: str) -> str:
    """'Relaynt: Swarm-Based ... | 2nd Place' -> 'relaynt': the project's own name."""
    return _norm(re.split(r"[:|]", tex.plain(title))[0])


def template_links() -> dict[str, str]:
    """Project name -> its link, from the person's own LaTeX resumes (the bank keeps their text, not their links)."""
    out: dict[str, str] = {}
    for path in config.RESUME_TEX.values():
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for title, url in re.findall(r"\\resumeProjectHeading\s*\{(.*?)\}\s*\{\\href\{([^}]+)\}", src):
            out.setdefault(_project_key(title), url)
    return out


def _dates(e: dict) -> str:
    return esc(" - ".join(x for x in (e["start"], e["end"]) if x))


def render(plan_: dict, draft: dict, by_id: dict, profile, base_src: str | None) -> str:
    pre, header, edu = _template(base_src, profile)
    parts = [pre.rstrip(), "\\begin{document}", header.rstrip()]
    for s in plan_["sections"]:
        t = s["type"]
        if t == "education":
            if edu:
                parts.append(re.sub(r"\\section\{Education\}", lambda _m: f"\\section{{{esc(s['heading'])}}}", edu, count=1)
                             + "\n\\vspace{-4pt}")
            continue
        if t == "skills":
            lines = draft.get("skills") or plan_.get("base_skills") or []  # never an empty Skills section
            if lines:
                body = " \\\\\n    ".join(f"\\textbf{{{esc(g['group'])}:}} {esc(g['items'])}" for g in lines)
                parts.append(f"\\section{{{esc(s['heading'])}}}\n\\begin{{itemize}}[leftmargin=0.15in, label={{}}, itemsep=0pt]\n"
                             f"  \\small{{\\item{{\n    {body}\n  }}}}\n\\end{{itemize}}")
            continue
        blocks = []
        for x in s["entries"]:
            e = by_id.get(x["id"])
            bullets = (draft.get("entries", {}).get(x["id"]) or {}).get("bullets", [])
            if not e or not bullets:
                continue
            items = "\n".join(f"        \\resumeItem{{{esc(b['text'])}}}" for b in bullets)
            if e["kind"] in ("experience", "research", "leadership"):
                head = (f"    \\resumeSubheading\n      {{{esc(e['org'] or e['title'])}}}{{\\small{{{esc(e['location'])}}}}}\n"
                        f"      {{{esc(e['title'] if e['org'] else '')}}}{{{_dates(e)}}}")
            else:
                link = e.get("link") or template_links().get(_project_key(e["title"]), "")
                right = f"\\href{{{link}}}{{\\textbf{{[Link]}}}}" if link.startswith("http") else _dates(e)
                name = esc(e["title"]) + (f", {esc(e['org'])}" if e["kind"] == "publication" and e["org"] else "")
                head = f"    \\resumeProjectHeading\n      {{\\textbf{{{name}}}}}{{{right}}}"
            blocks.append(f"{head}\n      \\resumeItemListStart\n{items}\n      \\resumeItemListEnd")
        if blocks:
            parts.append(f"\\section{{{esc(s['heading'])}}}\n  \\resumeSubHeadingListStart\n" + "\n\n".join(blocks)
                         + "\n  \\resumeSubHeadingListEnd\n\\vspace{-4pt}")
    parts.append("\\end{document}\n")
    return "\n\n".join(parts)


MIN_BULLETS = 2  # trimming to fit never takes an entry below this; past that the resume goes to 2 pages


def fit(plan_: dict, draft: dict, by_id: dict, profile, base_src: str | None) -> tuple[str, "tex.Compiled", list[dict]]:
    """Compile; while it's longer than the planned pages, drop the last bullet of the least relevant entry that still
    has more than MIN_BULLETS. When nothing more can go, a 1-page plan becomes 2 pages (Education and Skills are never
    dropped to make room)."""
    trimmed = []
    for _ in range(40):
        src = render(plan_, draft, by_id, profile, base_src)
        comp = tex.compile_tex(src)
        if not comp.ok or comp.pages <= plan_["pages"]:
            return src, comp, trimmed
        # least relevant first: sections and the entries in them are in priority order, so walk from the end
        order = [x["id"] for sec in plan_["sections"] for x in sec["entries"]][::-1]
        counts = {i: len((draft["entries"].get(i) or {}).get("bullets", [])) for i in order}
        victim = next((i for i in order if counts[i] > MIN_BULLETS), None)
        if not victim:
            # before a second page, the least relevant project-type entry goes (a job never does, nor a section's last)
            drop = next((x for sec in plan_["sections"][::-1] if sec["type"] not in _DATED and len(sec["entries"]) > 1
                         for x in sec["entries"][::-1] if counts.get(x["id"])), None)
            if drop and plan_["pages"] < 2:
                sec = next(s for s in plan_["sections"] if drop in s["entries"])
                sec["entries"].remove(drop)
                trimmed += [{"entry": drop["id"], "title": by_id[drop["id"]]["title"], "text": b["text"],
                             "reason": "to fit one page"} for b in draft["entries"][drop["id"]]["bullets"]]
                continue
            if plan_["pages"] < 2:
                plan_["pages"] = 2
                continue
            victim = next((i for i in order if counts[i] > 1), None)  # a 2-page resume that still overflows
            if not victim:
                return src, comp, trimmed
        block = draft["entries"][victim]
        b = block["bullets"].pop()
        block.setdefault("spare", []).insert(0, b)
        trimmed.append({"entry": victim, "title": by_id[victim]["title"], "text": b["text"], "reason": "to fit the page count"})
    return src, comp, trimmed


def fill_ratio(pdf: bytes) -> float:
    """How far down the last page the text reaches (0-1). About 0.9 is a full page with normal margins."""
    from io import BytesIO

    from pypdf import PdfReader

    try:
        page = PdfReader(BytesIO(pdf)).pages[-1]
        h = float(page.mediabox.height)
        ys: list[float] = []
        page.extract_text(visitor_text=lambda t, cm, tm, fd, fs: ys.append(tm[5] * cm[3] + cm[5]) if t.strip() else None)
        return (h - min(ys)) / h if ys else 1.0
    except Exception:
        return 1.0


FULL = 0.9


def _try(plan_, draft, by_id, profile, base_src):
    src = render(plan_, draft, by_id, profile, base_src)
    comp = tex.compile_tex(src)
    return src, comp, comp.ok and comp.pages <= plan_["pages"]


def fit_and_fill(plan_: dict, draft: dict, by_id: dict, profile, base_src: str | None):
    """Trim to the planned pages, then fill the last page while it has room: spare bullets of the entries on it first
    (most relevant entry first), then the next-best entries from the reserve."""
    src, comp, trimmed = fit(plan_, draft, by_id, profile, base_src)
    if not comp.ok:
        return src, comp, trimmed
    entries = draft.setdefault("entries", {})
    # 1. more bullets for the entries already on the page
    order = [x["id"] for sec in plan_["sections"] for x in sec["entries"]]
    for _ in range(30):
        if fill_ratio(comp.pdf) >= FULL:
            return src, comp, trimmed
        eid = next((i for i in order if (entries.get(i) or {}).get("spare")), None)
        if not eid:
            break
        block = entries[eid]
        block["bullets"].append(block["spare"].pop(0))
        src2, comp2, ok = _try(plan_, draft, by_id, profile, base_src)
        if not ok:
            block["spare"].insert(0, block["bullets"].pop())  # that one doesn't fit: this entry is done
            order.remove(eid)
            continue
        src, comp = src2, comp2
    # 2. the next-best entries (written by the writer as reserve material), each in its own section
    headings = plan_.get("headings") or {}
    for r in list(plan_.get("reserve") or []):
        if fill_ratio(comp.pdf) >= FULL:
            break
        block = entries.get(r["id"]) or {}
        e = by_id.get(r["id"])
        if not e or not block.get("bullets") or e["kind"] not in _KIND_SECTION:
            continue
        t = _KIND_SECTION[e["kind"]]
        sec = next((s for s in plan_["sections"] if s["type"] == t), None)
        if sec is None:  # a kind the template has no section for: its own section, just before Skills
            sec = {"type": t, "heading": headings.get(t, DEFAULT_HEADINGS[t]), "entries": []}
            at = next((k for k, s in enumerate(plan_["sections"]) if s["type"] == "skills"), len(plan_["sections"]))
            plan_["sections"].insert(at, sec)
        all_bullets = list(block["bullets"])
        sec["entries"].append({"id": r["id"], "bullets": len(all_bullets)})
        placed = False
        for n in range(len(all_bullets), 0, -1):  # as many of its bullets as fit
            block["bullets"] = all_bullets[:n]
            src2, comp2, ok = _try(plan_, draft, by_id, profile, base_src)
            if ok:
                src, comp, placed = src2, comp2, True
                block["spare"] = all_bullets[n:] + list(block.get("spare") or [])
                break
        if placed:
            plan_["reserve"].remove(r)
        else:
            block["bullets"] = all_bullets
            sec["entries"].pop()
            if not sec["entries"] and sec["type"] not in (x for x in plan_.get("template") or []):
                plan_["sections"].remove(sec)
    return src, comp, trimmed


# ---- the whole build ---------------------------------------------------------------------------------------------
def skills_source(entries: list[dict], profile) -> str:
    skill_lines = [e["text"] for e in entries if e["kind"] == "skills"]
    prof = re.search(r"## Skills(.*?)(\n## |\Z)", profile.text, re.S)
    tags = sorted({t for e in entries for t in e["tags"]})
    return "\n".join(skill_lines + ([prof.group(1)] if prof else []) + ["Also named in entries: " + ", ".join(tags)])


def build_draft(job: dict, info: dict, profile, base_key: str | None = None, log=print) -> dict:
    """Plan -> write -> adversarial review + revise (x2) -> verify -> render + fit. Saves the PDF next to the job's other
    tailored files; returns the resume_edits payload for Resume review."""
    from . import build as resume_build
    from .select import resume_version

    d = bank.load()
    entries = d["entries"]
    if len([e for e in entries if e["kind"] not in ("skills", "narrative")]) < 2:
        raise bank.BankError("The experience bank is nearly empty: import your resumes or add entries first")
    by_id = {e["id"]: e for e in entries}
    base_key = base_key or info.get("chosen")
    base_tex = config.RESUME_TEX.get(base_key) or next(iter(config.RESUME_TEX.values()), None)
    base_src = base_tex.read_text(encoding="utf-8") if base_tex else None
    sk_src = skills_source(entries, profile)

    template = template_sections(base_src)
    p = plan(job, info, entries, profile, own_headings(base_src), template)
    p["base_skills"] = base_skill_lines(base_key)
    log(f"    bank resume: {p['pages']} page(s), sections {', '.join(s['heading'] for s in p['sections'])}")
    draft = write(job, info, p, by_id, sk_src)
    reviews = []
    for round_ in (1, 2, 3):  # up to two revisions; the last review is of the final draft, for Resume review
        checked, dropped = verify(draft, by_id)
        r = critique(job, info, p, checked, by_id, sk_src)
        serious = [i for i in r["issues"] if i.get("severity") in ("high", "medium")]
        reviews.append({"round": round_, "verdict": r["verdict"], "score": r["score"], "model": r["model"], "issues": r["issues"],
                        "dropped_before_review": dropped})
        log(f"    adversarial review {round_}: score {r['score']}, {len(serious)} serious issue(s)")
        if not serious or round_ == 3:
            break
        draft = write(job, info, p, by_id, sk_src, r, checked)
    final, dropped = verify(draft, by_id)
    shown = " ".join(b["text"] for s in p["sections"] for x in s["entries"]
                     for b in (final["entries"].get(x["id"]) or {}).get("bullets", []))
    final["skills"], skills_added, skills_excluded = merge_skills(
        p["base_skills"], verify_skills(final.get("skills") or [], sk_src), info.get("terms") or [],
        tex.document_plain(base_src) if base_src else "", shown,
        sk_src + "\n" + "\n".join(e["text"] for e in entries))
    if skills_added:
        log(f"    skills added from the posting: {', '.join(a['skill'] for a in skills_added if not a.get('suggested'))}")
    src, comp, trimmed = fit_and_fill(p, final, by_id, profile, base_src)
    if not comp.ok:
        raise RuntimeError(f"LaTeX didn't compile: {comp.log[-300:]}")

    folder = config.TAILORED_DIR / str(job["id"])
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):
        if old.is_file():
            old.unlink()
    stem = resume_build.file_stem(job.get("role"))
    (folder / f"{stem}.pdf").write_bytes(comp.pdf)
    (folder / f"{stem}.tex").write_text(src, encoding="utf-8")
    terms = info.get("terms") or []
    after = match.score(tex.document_plain(src), terms, set(info.get("equivalent", []))) if terms else {"pct": None}
    result = {
        "mode": "bank", "base": base_key, "pages": comp.pages, "planned_pages": p["pages"], "plan": p,
        "draft": final, "entries": {eid: {k: by_id[eid][k] for k in ("title", "org", "kind", "start", "end", "location")}
                                    for eid in final["entries"]},
        "evidence_text": {eid: by_id[eid]["text"][:4000] for eid in final["entries"]},
        "reviews": reviews, "dropped": dropped, "trimmed": trimmed,
        "pct_before": info.get("pct"), "pct_after": after.get("pct"),
        "skills_added": skills_added, "skills_excluded": skills_excluded,
        "file": str((folder / f"{stem}.pdf").relative_to(config.ROOT)), "tex": str((folder / f"{stem}.tex").relative_to(config.ROOT)),
        "resumes_version": resume_version(),
    }
    result["warnings"] = quality(p, final, comp, result)
    for w in result["warnings"]:
        log(f"    check: {w}")
    (folder / "bank-build.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def quality(plan_: dict, draft: dict, comp, result: dict) -> list[str]:
    """What's still wrong with a built resume, in plain words for Resume review (empty = nothing found)."""
    out = []
    shown = {s["type"] for s in plan_["sections"]
             if s["type"] in ("education", "skills") or any((draft["entries"].get(x["id"]) or {}).get("bullets") for x in s["entries"])}
    if "skills" not in shown or not draft.get("skills"):
        out.append("The Technical Skills section is missing")
    missing = [DEFAULT_HEADINGS.get(t, t) for t in plan_.get("template") or [] if t not in shown]
    if missing:
        out.append(f"Sections from your resume that aren't on this one: {', '.join(missing)}")
    fill = fill_ratio(comp.pdf)
    if comp.pages == 1 and fill < 0.8:
        out.append(f"The page is only about {fill:.0%} full: your bank didn't have enough relevant material for this job")
    elif comp.pages >= 2 and fill < 0.4:
        out.append(f"The last page is only about {fill:.0%} full")
    if result.get("pct_before") is not None and result.get("pct_after") is not None and result["pct_after"] < result["pct_before"]:
        out.append(f"It matches the posting less than your base resume does ({result['pct_after']:.0%} vs {result['pct_before']:.0%})")
    return out


def rebuild(job: dict, edits: dict, profile) -> dict:
    """Re-render a draft after the person edited / removed bullets in Resume review (their words are kept as typed)."""
    r = dict(job.get("resume_edits") or {})
    if r.get("mode") != "bank":
        raise bank.BankError("This job has no resume built from the bank")
    by_id = {e["id"]: e for e in bank.load()["entries"]}
    for eid, meta in (r.get("entries") or {}).items():  # entries deleted from the bank since: keep what the draft knew
        by_id.setdefault(eid, {"id": eid, "text": r.get("evidence_text", {}).get(eid, ""), "tags": [], "link": "", **meta})
    draft = r["draft"]
    for eid, bullets in (edits.get("bullets") or {}).items():
        if eid in draft["entries"]:
            draft["entries"][eid]["bullets"] = [{"text": clean(t), "evidence": "", "terms": [], "edited": True}
                                                for t in bullets if str(t).strip()]
    if "skills" in edits:
        draft["skills"] = [{"group": clean(g.get("group", "")), "items": clean(g.get("items", ""))} for g in edits["skills"]
                           if str(g.get("items", "")).strip()]
    base_tex = config.RESUME_TEX.get(r.get("base")) or next(iter(config.RESUME_TEX.values()), None)
    base_src = base_tex.read_text(encoding="utf-8") if base_tex else None
    own = own_headings(base_src)
    for sec in r["plan"]["sections"]:  # drafts made before headings followed the person's resume
        sec["heading"] = own.get(sec["type"], sec["heading"])
    src, comp, trimmed = fit_and_fill(r["plan"], draft, by_id, profile, base_src)
    if not comp.ok:
        raise bank.BankError(f"LaTeX didn't compile: {comp.log[-200:]}")
    pdf = config.ROOT / r["file"]
    pdf.write_bytes(comp.pdf)
    (config.ROOT / r["tex"]).write_text(src, encoding="utf-8")
    r.update(draft=draft, pages=comp.pages, trimmed=trimmed)
    r["warnings"] = quality(r["plan"], draft, comp, r)
    return r
