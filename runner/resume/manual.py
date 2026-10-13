"""Paste-description matching and reviewed exports, kept in the signed-in person's folder."""
import json
import re
import uuid

from .. import config, profile_md
from ..profile import load_profile
from ..screening import active_screens, jev_screen, screen
from . import build, pdf, select, tailor


def folder(draft_id: str):
    if not isinstance(draft_id, str) or not re.fullmatch(r"[a-f0-9]{32}", draft_id):
        raise ValueError("Invalid draft id")
    if not config.HOME:
        raise ValueError("Finish your profile and resume setup first")
    return config.HOME / "resume-studio" / draft_id


def analyze(body: dict) -> dict:
    description = str(body.get("description") or "").strip()
    if not 300 <= len(description) <= 40000:
        raise ValueError("Paste a job description between 300 and 40,000 characters")
    if not config.HOME or not config.RESUMES:
        raise ValueError("Add a resume in My files first")
    draft_id = uuid.uuid4().hex
    job = {"id": draft_id, "company": str(body.get("company") or "Pasted job")[:200],
           "role": str(body.get("role") or "Target role")[:200], "description": description}
    profile_md.sync_from_db(config.PROFILE_PATH)
    profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
    active = active_screens(profile.standard_answers)
    restriction = screen(description, active)
    if not restriction:
        try:
            restriction = jev_screen(description, draft_id, active)
        except Exception:
            pass  # screening is informational here; matching still works without Jev
    select.base_docs.cache_clear()  # cached sources belong to the active user and may have been edited
    info = select.evaluate(job)
    if not info:
        raise ValueError("Could not identify enough job skills to score. Include the full requirements and responsibilities.")
    if info.get("tailorable"):
        edits = tailor.propose(job, info, profile.text)
    else:
        text = pdf.extract(config.RESUMES[info["chosen"]])
        pdf.skills_layout(config.RESUMES[info["chosen"]])
        edits = pdf.propose(info, profile.text, text)
    result = {"draft_id": draft_id, "job": job, "match": info, "edits": edits,
              "restriction": restriction, "version": select.resume_version()}
    target = folder(draft_id)
    target.mkdir(parents=True, exist_ok=True)
    (target / "draft.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def export(draft_id: str, accepted: list[str], *, extraction_confirmed: bool = False) -> dict:
    target = folder(draft_id)
    try:
        draft = json.loads((target / "draft.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Draft not found. Analyze the job description again.") from None
    if draft["version"] != select.resume_version():
        raise ValueError("Your base resumes changed. Analyze the description again before building.")
    if not isinstance(accepted, list) or any(not isinstance(item, str) for item in accepted):
        raise ValueError("Select valid suggested changes")
    if not set(accepted).issubset({e["id"] for e in draft["edits"]["edits"]}):
        raise ValueError("Unknown suggested change. Analyze the description again.")
    job = {**draft["job"], "resume_match": draft["match"], "resume_edits": draft["edits"]}
    if draft["edits"].get("format") == "pdf_rebuild":
        if extraction_confirmed is not True:
            raise ValueError("Review and confirm the extracted PDF content before building")
        result = pdf.build(draft, accepted, target / "export")
    else:
        result = build.build(job, accepted, persist=False, output_dir=target / "export", allow_unchanged=True)
    if result.get("ok"):
        result["draft_id"] = draft_id
        result["unchanged"] = not accepted
    return result
