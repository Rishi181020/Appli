"""The person's profile: structured data (edited in the dashboard) <-> profile.md (what every model call reads).

The dashboard's Profile onboarding saves `data` to the Supabase `profiles` table; `render()` turns it into the
markdown layout that runner/profile.py parses and every prompt embeds. The dashboard mirrors render() in
dashboard/src/profileMd.ts so the Review step shows exactly this text. `parse()` does the reverse, for adopting an
existing hand-written profile.md.
"""
import re
from pathlib import Path

# Work-authorization / self-ID questions, in display order. The first three drive screening.
AUTH_QUESTIONS = [
    "Authorized to work in the US?",
    "Will you now or in the future require sponsorship for employment visa status?",
    "Are you a US citizen or permanent resident?",
    "Can you obtain a US security clearance?",
    "Disability?",
    "Gender",
    "Pronouns",
    "Race",
    "Hispanic or Latino?",
    "Veteran?",
    "LGBTQ+?",
    "Sexual orientation",
]
REQUIRED_AUTH = AUTH_QUESTIONS[:2]

# Common application answers: saved as '~rules' in Saved answers too, so forms get them with no model call.
COMMON = {
    "start_date": ("Earliest start date", "~start date|when are you able to|when would you be|earliest you can|available to start"),
    "salary": ("Salary expectation", "~compensation|salary|pay expectation"),
    "relocation": ("Willing to relocate", "~relocate|relocation"),
    "work_arrangement": ("Work arrangement preference", "~work arrangement|remote|hybrid|onsite preference"),
    "notice_period": ("Notice period", "~notice period"),
    "hear_about": ("How did you hear about us", "~how did you hear|referred by|referral source"),
}


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _dates(a: str, b: str) -> str:
    return f"{_clean(a) or '?'} to {_clean(b) or 'Present'}"


def render(d: dict) -> str:
    out = [f"# {_clean(d.get('name'))}", ""]
    contact = " · ".join(x for x in (_clean(d.get("location")), _clean(d.get("email")), _clean(d.get("phone"))) if x)
    out.append(contact)
    for label, key in (("Country", "country"), ("State/Province", "state"), ("LinkedIn", "linkedin"), ("GitHub", "github"),
                       ("Website", "website")):
        if _clean(d.get(key)):
            out.append(f"{label}: {_clean(d.get(key))}")
    out.append("")

    if d.get("education"):
        out += ["## Education", ""]
        for e in d["education"]:
            head = f"**{_clean(e.get('school'))}** — {_clean(e.get('degree'))}"
            if _clean(e.get("field")):
                head += f", {_clean(e.get('field'))}"
            line = _dates(e.get("start"), e.get("end")) + (f" · GPA {_clean(e.get('gpa'))}" if _clean(e.get("gpa")) else "")
            out += [head, line, ""]

    if d.get("experience"):
        out += ["## Work Experience", ""]
        for x in d["experience"]:
            out += [f"### {_clean(x.get('title'))} — {_clean(x.get('company'))}", "", _dates(x.get("start"), x.get("end")), ""]
            bullets = [b for b in (x.get("bullets") or []) if _clean(b)]
            out += [f"- {_clean(b)}" for b in bullets]
            if bullets:
                out.append("")

    skills = [s for s in (d.get("skills") or []) if _clean(s.get("items"))]
    if skills:
        out += ["## Skills", ""] + [f"- **{_clean(s.get('group')) or 'Skills'}:** {_clean(s.get('items'))}" for s in skills] + [""]

    auth = d.get("authorization") or {}
    rows = [(q, _clean(auth.get(q))) for q in AUTH_QUESTIONS if _clean(auth.get(q))]
    rows += [(q, _clean(a)) for q, a in auth.items() if q not in AUTH_QUESTIONS and _clean(a)]
    if rows:
        out += ["## Equal Employment and Work Authorization", "", "| Question | Answer |", "|---|---|"]
        out += [f"| {q} | {a} |" for q, a in rows] + [""]

    common = d.get("common") or {}
    rows = [(COMMON[k][0], _clean(common.get(k))) for k in COMMON if _clean(common.get(k))]
    if rows:
        out += ["## Common Application Answers", "", "| Question | Answer |", "|---|---|"]
        out += [f"| {q} | {a} |" for q, a in rows] + [""]

    extra = _clean(d.get("notes"))
    if extra:
        out += ["## Additional Notes", "", str(d.get("notes")).strip(), ""]
    return "\n".join(out).rstrip() + "\n"


def parse(md: str) -> dict:
    """Best-effort reverse of render(): turns an existing profile.md into structured data (for adopting a hand-written profile)."""
    d: dict = {"education": [], "experience": [], "skills": [], "authorization": {}, "common": {}}
    m = re.search(r"^#\s+(.+)$", md, re.M)
    d["name"] = m.group(1).strip() if m else ""
    for key, pat in (("email", r"[\w.+-]+@[\w-]+\.[\w.]+"), ("phone", r"\+?\d[\d ()-]{8,}\d")):
        m = re.search(pat, md)
        d[key] = m.group(0).strip() if m else ""
    m = re.search(r"^([A-Za-z .'-]+,\s*[A-Z]{2})\s*·", md, re.M)
    d["location"] = m.group(1) if m else ""
    for label, key in (("LinkedIn", "linkedin"), ("GitHub", "github"), ("Website", "website")):
        m = re.search(rf"^{label}:\s*(\S+)", md, re.M)
        d[key] = m.group(1) if m else ""
    for label, key in (("Country", "country"), ("State(?:/Province)?", "state")):
        m = re.search(rf"^{label}:\s*(.+)$", md, re.M)
        d[key] = m.group(1).strip() if m else ""
    if not (d["country"] and d["state"]):
        from .places import from_location

        st, co = from_location(d["location"])
        d["country"], d["state"] = d["country"] or co, d["state"] or st

    sections = {m.group(1).strip().lower(): m.end() for m in re.finditer(r"^##\s+(.+)$", md, re.M)}
    starts = sorted(sections.values())

    def body(name_part: str) -> str:
        for title, start in sections.items():
            if name_part in title:
                nxt = next((s for s in starts if s > start), len(md))
                block = md[start:nxt]
                return block[: block.rfind("\n##")] if "\n##" in block else block
        return ""

    for m in re.finditer(r"\*\*(?P<school>[^*\n]+)\*\*\s+[—-]\s+(?P<deg>[^,\n]+)(?:,\s*(?P<field>[^\n]+))?\n\s*"
                         r"(?P<s>[\d-]+|\?)\s+to\s+(?P<e>[\w-]+)(?:\s*·\s*GPA\s*(?P<gpa>[\d.]+))?", body("education")):
        d["education"].append({"school": m["school"].strip(), "degree": m["deg"].strip(), "field": (m["field"] or "").strip(),
                               "start": m["s"], "end": m["e"], "gpa": m["gpa"] or ""})

    exp = body("experience")
    blocks = re.split(r"^###\s+", exp, flags=re.M)[1:]
    for b in blocks:
        head, _, rest = b.partition("\n")
        title, _, company = head.partition(" — ")
        dm = re.search(r"([\d-]+|\?)\s+to\s+([\w-]+)", rest)
        d["experience"].append({
            "title": title.strip(), "company": company.strip(),
            "start": dm.group(1) if dm else "", "end": dm.group(2) if dm else "",
            "bullets": [ln[2:].strip() for ln in rest.splitlines() if ln.startswith("- ")],
        })

    for m in re.finditer(r"^-\s+\*\*(.+?):\*\*\s*(.+)$", body("skills"), re.M):
        d["skills"].append({"group": m.group(1).strip(), "items": m.group(2).strip()})

    labels = {v[0].lower(): k for k, v in COMMON.items()}
    for line in md.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 2 and set(cells[0]) - set("-: ") and cells[0] != "Question":
            if cells[0].lower() in labels:
                d["common"][labels[cells[0].lower()]] = cells[1]
            else:
                d["authorization"][cells[0]] = cells[1]
    return d


def validate(d: dict) -> list[str]:
    errors = []
    if not _clean(d.get("name")):
        errors.append("Name is required")
    if not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.]+", _clean(d.get("email"))):
        errors.append("A valid email is required")
    if _clean(d.get("phone")) and not re.fullmatch(r"\+\d[\d ()-]{8,}\d", _clean(d.get("phone"))):
        errors.append("Phone must include the country code, e.g. +1 408 555 0123")
    country = _clean(d.get("country")).lower()
    if not country:
        errors.append("Country you live in is required")
    elif country in ("united states", "united states of america", "usa", "us") and not _clean(d.get("state")):
        errors.append("State is required for the United States")
    if not d.get("education"):
        errors.append("Add at least one education entry")
    for q in REQUIRED_AUTH:
        if not _clean((d.get("authorization") or {}).get(q)):
            errors.append(f"Answer: {q}")
    for key in ("linkedin", "github", "website"):
        v = _clean(d.get(key))
        if v and not re.match(r"https?://", v):
            errors.append(f"{key.title()} must be a full URL (https://...)")
    return errors


def common_rules(d: dict) -> list[dict]:
    """Saved-answer '~rules' for the common answers (question_key, question_text, answer)."""
    common = d.get("common") or {}
    return [{"question_key": COMMON[k][1], "question_text": COMMON[k][1], "answer": _clean(common[k])}
            for k in COMMON if _clean(common.get(k))]


def sync_from_db(path: Path) -> str:
    """Write the person's profile.md from Supabase. -> 'synced' | 'local' (no row yet, local file kept) | 'missing'."""
    from .db import sb

    rows = sb().table("profiles").select("data,markdown").execute().data
    if rows and (rows[0].get("markdown") or rows[0].get("data")):
        md = rows[0].get("markdown") or render(rows[0]["data"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != md:
            path.write_text(md, encoding="utf-8")
        return "synced"
    return "local" if path.exists() else "missing"
