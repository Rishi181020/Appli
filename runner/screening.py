"""Screening of posting text: skip roles the candidate can't take. Patterns first (free), then a Jev check."""
import re

# Candidate needs visa sponsorship, so these are dead ends.
_NO_SPONSOR = re.compile(
    r"(not|unable to|cannot|can't|won't|will not|does not|do not|doesn't|don't)\s+(be able to\s+)?"
    r"(provide|offer|support|sponsor|consider)[^.]{0,40}(sponsor|visa)|"
    r"no\s+(visa\s+)?sponsorship|without\s+(the need for\s+)?(visa\s+)?sponsorship|"
    r"sponsorship\s+(is\s+)?(not|unavailable)|unable to sponsor|not\s+sponsor|"
    r"(must|should|will)\s+not\s+(now\s+or\s+in\s+the\s+future\s+)?require\s+(visa\s+|employment\s+)?sponsorship|"
    r"not\s+(be\s+)?(eligible|able)\s+to\s+(offer|provide)\s+(visa\s+)?sponsorship|"
    r"(must|need to)\s+be\s+(legally\s+)?(authorized|eligible)[^.]{0,60}without\s+(current or future\s+)?sponsorship",
    re.I,
)
_CITIZEN = re.compile(
    r"u\.?s\.?\s+citizen(ship)?\s+(is\s+)?(only|required)|"
    r"(must|need to|required to)\s+be\s+a\s+(u\.?s\.?|united states)\s+citizen|"
    r"citizens? of the (u\.?s\.?|united states)\s+only|us persons? only|"
    r"\bitar\b|export[- ]control[^.]{0,80}(u\.?s\.? person|citizen)",
    re.I,
)
_CLEARANCE = re.compile(
    r"(active|current|top secret|ts/sci|secret)\s+(u\.?s\.?\s+)?(government\s+)?(security\s+)?clearance|"
    r"(obtain|hold|maintain|possess|eligible for)\s+(a\s+)?(u\.?s\.?\s+)?(government\s+)?(security\s+)?clearance|"
    r"ts/sci|security clearance (is )?required",
    re.I,
)
_CLOSED = re.compile(
    r"no longer accepting|position has been filled|job (is )?no longer available|"
    r"this job (has )?(expired|closed)|posting (has )?(expired|closed)|page (you are looking for )?(not found|doesn.t exist)|"
    r"we couldn.t find|job not found|role has been filled|no longer open",
    re.I,
)


def _snippet(text: str, m: re.Match) -> str:
    a, b = max(0, m.start() - 60), min(len(text), m.end() + 60)
    return re.sub(r"\s+", " ", text[a:b]).strip()


ALL_SCREENS = frozenset({"no_sponsor", "citizen", "clearance"})


def active_screens(standard_answers: dict[str, str] | None = None) -> frozenset[str]:
    """Which eligibility screens apply to this person. Their profile answers win; users/<Name>/appli.json is the default.

    no_sponsor: skip "no visa sponsorship" postings   - only if the person needs sponsorship
    citizen:    skip "US citizens / US persons only"  - only if the person isn't a US citizen / permanent resident
    clearance:  skip "security clearance required"    - only if they can't hold one (or chose to skip them)
    """
    from . import config
    from .userconfig import PersonCfg

    p = config.USER.person if config.USER else PersonCfg()
    needs, us_person, skip_clear = p.needs_sponsorship, p.us_person, p.skip_clearance_jobs
    for q, a in (standard_answers or {}).items():
        q, yes = q.lower(), str(a).strip().lower().startswith("y")
        if "sponsorship" in q:
            needs = yes
        elif "citizen" in q or "permanent resident" in q or "us person" in q:
            us_person = yes
        elif "clearance" in q:  # "Can you hold / obtain a US security clearance?"
            skip_clear = not yes
    out = set()
    if needs:
        out.add("no_sponsor")
    if not us_person:
        out.add("citizen")
    if skip_clear:
        out.add("clearance")
    return frozenset(out)


def screen(text: str, active: frozenset[str] = ALL_SCREENS) -> str | None:
    """Return a human-readable skip reason, or None if the posting looks applicable to this person."""
    head = text[:1500]
    m = _CLOSED.search(head)
    if m:
        return f"Posting closed: “{_snippet(head, m)}”"
    for key, pattern, label in (("no_sponsor", _NO_SPONSOR, "No visa sponsorship"), ("citizen", _CITIZEN, "US citizenship required"),
                                ("clearance", _CLEARANCE, "Security clearance required")):
        if key not in active:
            continue
        m = pattern.search(text)
        if m:
            return f"{label}: “{_snippet(text, m)}”"
    return None


# Wording varies too much for patterns alone, so Jev reads the posting as a second screen (one request, ~0.5s).
_JEV_SCREENS = {
    "no_sponsor": ("No visa sponsorship",
                   "Does this job posting say the employer will not sponsor work visas, or that applicants must not need "
                   "sponsorship now or in the future (including excluding OPT/CPT/H-1B holders)?",
                   "The posting rules out candidates who need visa sponsorship.",
                   "The posting does not rule out visa sponsorship, or doesn't mention it."),
    "citizen": ("US citizenship required",
                "Does this job posting require US citizenship or US-person / green-card status (e.g. ITAR, export control)?",
                "Only US citizens / US persons can be hired.",
                "No citizenship or US-person requirement is stated."),
    "clearance": ("Security clearance required",
                  "Does this job require holding or obtaining a US government security clearance?",
                  "A security clearance is required or must be obtained.",
                  "No clearance is required."),
}
JEV_SCREEN_MIN = 0.85  # only skip on a clear yes: a missed skip costs one application, a wrong skip loses a job


def jev_screen(text: str, job_id=None, active: frozenset[str] = ALL_SCREENS) -> str | None:
    from . import jev

    qs = {k: jev.noul(q, yes, no) for k, (_, q, yes, no) in _JEV_SCREENS.items() if k in active}
    if not qs:
        return None
    answers = jev.decide(text[:12000], qs, session_id=f"job-{job_id}-screen", purpose="posting screen")
    for k, (label, *_rest) in _JEV_SCREENS.items():
        if k not in active:
            continue
        p = float((answers.get(k) or {}).get("noul") or 0)
        if p >= JEV_SCREEN_MIN:
            return f"{label} (Jev read the posting: {p:.0%} sure)"
    return None
