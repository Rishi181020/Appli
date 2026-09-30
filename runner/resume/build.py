"""Turn the skills Rishi ticked into a tailored PDF.

Saved as resumes/tailored/<job id>/<First name> Resume <Role>.pdf, the industry-standard name: no company in it,
same pattern as the base resumes ("Rishi Resume SWE.pdf"). The job id folder keeps two jobs with the same role apart.
"""
import json
import re

from .. import config
from ..db import sb
from ..profile import load_profile
from . import match, tailor, tex

_NOISE = re.compile(
    r"\b(20\d\d|new ?grads?|new graduates?|university (grad|graduate)s?|college grads?|graduates?|early[- ]career|"
    r"entry[- ]level|emerging talent|early talent|campus|summer|fall|spring|winter|start|starts|in person|remote|"
    r"hybrid|us|usa)\b",
    re.I,
)
_ROLE_WORD = re.compile(r"engineer|developer|scientist|analyst|architect|programmer|staff|researcher|designer|manager|intern", re.I)


def role_title(role: str | None) -> str:
    """'2027 University Graduate - Software Engineer' -> 'Software Engineer'; 'Member of Technical Staff New Grad -
    2027 Start' -> 'Member of Technical Staff'. Keeps the job title, drops years, grad labels and locations."""
    if not role:
        return "Software Engineer"
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", role)
    parts = [p for p in re.split(r"\s+[-–—|:,/]\s+|,\s*", text) if p.strip()]
    best = next((p for p in parts if _ROLE_WORD.search(p)), parts[0] if parts else text)
    best = _NOISE.sub(" ", best)
    best = re.sub(r"[^A-Za-z0-9+#&./ ]+", " ", best)
    best = re.sub(r"\s+", " ", best).strip(" -&/")
    return best or "Software Engineer"


def file_stem(role: str | None) -> str:
    first = load_profile(config.PROFILE_PATH).first_name
    return f"{first} Resume {role_title(role)}"


def build(job: dict, accepted: list[str]) -> dict:
    """Add the ticked skills to the base .tex, compile, save, record. Never raises for expected failures."""
    edits = job.get("resume_edits") or {}
    info = job.get("resume_match") or {}
    key = edits.get("base")
    if not key or key not in config.RESUME_TEX:
        return {"ok": False, "error": "No tailoring suggestion for this job."}
    chosen = [e for e in edits.get("edits", []) if e["id"] in set(accepted)]
    if not chosen:
        return {"ok": False, "error": "Tick at least one skill to build a tailored resume."}

    src = config.RESUME_TEX[key].read_text(encoding="utf-8")
    units = tex.parse(src)
    by_id = {u.id: u for u in units}
    for e in chosen:  # the base file may have changed since the suggestion was made
        u = by_id.get(e["unit_id"])
        if u is None or u.text != e["before_tex"]:
            return {"ok": False, "error": f"Your {key} resume changed since this suggestion was made. Re-run the job to refresh it."}
    new_src = tailor.apply(src, units, chosen)

    comp = tex.compile_tex(new_src)
    if not comp.ok:
        return {"ok": False, "error": f"LaTeX failed to compile: {comp.log[:300]}"}
    if comp.pages != 1:
        return {"ok": False, "error": f"With these skills the resume is {comp.pages} pages. Untick a few so it fits on one page."}

    terms = info.get("terms") or []
    after = (match.score(tex.document_plain(new_src), terms, set(info.get("equivalent", []))) if terms
             else {"pct": None, "missing": []})
    added = [e["skill"] if "skill" in e else e["keywords_added"][0] for e in chosen]

    folder = config.TAILORED_DIR / str(job["id"])
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):  # a rebuild replaces the previous version (the role name may have changed)
        old.unlink()
    stem = file_stem(job.get("role"))
    pdf_path = folder / f"{stem}.pdf"
    pdf_path.write_bytes(comp.pdf)
    (folder / f"{stem}.tex").write_text(new_src, encoding="utf-8")
    not_found = [e["skill"] for e in chosen if e.get("support") == "not_found"]
    lines = [
        f"# {stem}  (for {job['company']} - {job.get('role')}, job {job['id']})",
        f"Base resume: {key}  |  match {edits.get('pct_before', 0):.0%} -> {(after['pct'] or 0):.0%}  |  {len(chosen)} skill(s) added",
        "",
        "## Skills added",
        *[f"- {e.get('skill', e['keywords_added'][0])}  ->  {e['context']}"
          + ("  (you confirmed: not in your other materials)" if e.get("support") == "not_found" else "") for e in chosen],
        "",
    ]
    skipped = [e.get("skill", "") for e in edits.get("edits", []) if e["id"] not in set(accepted)]
    if skipped:
        lines += ["## Suggested but not added", ", ".join(skipped)]
    (folder / f"{stem}.changes.md").write_text("\n".join(lines), encoding="utf-8")

    result = {
        "ok": True, "file": str(pdf_path.relative_to(config.ROOT)), "pct_after": after["pct"],
        "added_words": added, "removed_words": [], "accepted": [e["id"] for e in chosen], "not_found_added": not_found,
    }
    from .select import resume_version

    edits_out = {**edits, "built_for": resume_version(),
                 "built": {k: result[k] for k in ("pct_after", "added_words", "removed_words", "accepted", "file")}}
    try:
        sb().table("jobs").update(
            {"resume_file": result["file"], "resume_review": "built", "resume_edits": json.loads(json.dumps(edits_out))}
        ).eq("id", job["id"]).execute()
    except Exception as e:  # e.g. the 002 migration has not been run yet: the PDF is still saved
        result["db_warning"] = f"{type(e).__name__}: {str(e)[:160]}"
    return result
