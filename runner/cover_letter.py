import re
from functools import lru_cache
from html import escape
from pathlib import Path

from playwright.sync_api import Playwright
from pypdf import PdfReader

from . import llm
from .profile import Profile
from .style import STYLE, clean


@lru_cache(maxsize=8)
def resume_text(path: str) -> str:
    return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)


_SYSTEM = """You write a concise cover letter for the candidate.
Rules:
- Use ONLY facts in the candidate profile / resume text. Never invent employers, projects, metrics, skills or motivations.
- Use the job description only to choose which real experience to emphasise.
- Mention the company name and role title once, in the opening sentence.
- 3 short paragraphs, under 220 words, first person, specific and plain. No cliches, no filler, no placeholders.
- Start with "Dear Hiring Team," and end with "Sincerely," followed by the candidate's name.

""" + STYLE + """

Return only the letter text."""

_NUM = re.compile(r"\d[\d,.]*%?\+?")


def ungrounded_numbers(letter: str, source: str) -> list[str]:
    src = source.replace(",", "")
    return [n for n in _NUM.findall(letter) if n.replace(",", "").rstrip(".") not in src]


def generate(profile: Profile, resume: str, job: dict) -> tuple[str, list[str]]:
    """Return (letter, problems). problems non-empty means the letter still has unverifiable numbers."""
    from . import config
    from .userfiles import cover_text

    source = profile.text + "\n" + resume
    sample = cover_text(config.COVER_LETTER)
    system = _SYSTEM
    if sample:  # the person's own letter: match its voice, never copy its facts about another job
        system += ("\n- Write in the candidate's own voice and tone, as in their sample letter below. Reuse their phrasing "
                   "where it fits, but never its company, role or any claim that isn't in the profile or resume.")
    user = (
        f"CANDIDATE PROFILE:\n{profile.text}\n\nRESUME TEXT:\n{resume}\n\n"
        + (f"CANDIDATE'S OWN SAMPLE COVER LETTER (style only):\n{sample}\n\n" if sample else "")
        + f"JOB: {job.get('role')} at {job.get('company')}\nJOB DESCRIPTION:\n{job.get('description', '(not available)')}"
    )
    letter = clean(llm.chat_text(system, user, max_tokens=900, purpose="cover letter"))
    bad = ungrounded_numbers(letter, source)
    if bad:  # one regeneration, then flag
        letter = llm.chat_text(
            system + f"\nDo NOT use these numbers, they are not in the resume: {bad}.", user, max_tokens=900,
            purpose="cover letter (redo: bad numbers)",
        )
        letter = clean(letter)
        bad = ungrounded_numbers(letter, source)
    return letter, [f"unverified number: {b}" for b in bad]


def render_pdf(pw: Playwright, letter: str, out: Path) -> Path:
    """Headless render (page.pdf() is unsupported in headed Chromium)."""
    body = "".join(f"<p>{escape(p).replace(chr(10), '<br>')}</p>" for p in letter.split("\n\n"))
    html = (
        "<html><body style=\"font-family:Georgia,serif;font-size:12pt;line-height:1.5;margin:0\">"
        f"{body}</body></html>"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    browser = pw.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.set_content(html)
        page.pdf(path=str(out), format="Letter", margin={"top": "1in", "bottom": "1in", "left": "1in", "right": "1in"})
    finally:
        browser.close()
    return out
