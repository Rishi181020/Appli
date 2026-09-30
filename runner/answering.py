"""Answer application-form fields, spending as little model budget as possible.

Tiers, cheapest first:
  1. direct  - identity fields, standard work-auth/EEO answers, learned answers bank. No LLM.
  2. choice  - yes/no, select, radio, short factual text -> LLM_MODEL_FAST (cheap model).
  3. open    - essays / "why us" / "describe a time" -> LLM_MODEL_WRITE (stronger model),
               grounded in the profile, the chosen resume text and the job description.
Anything the model can't ground in the profile is returned in `unanswered` for human review.
"""
import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import llm
from .profile import MONTHS, Profile


@dataclass
class Field:
    id: str
    label: str
    type: str  # text | textarea | select | radio | checkbox | number | email | tel | url
    options: list[str] = field(default_factory=list)
    required: bool = False
    occ: int = 1  # 1st, 2nd... field with this label on the form (repeatable blocks such as Education)


@dataclass
class Result:
    values: dict[str, str] = field(default_factory=dict)
    unanswered: list[dict] = field(default_factory=list)
    tiers: dict[str, str] = field(default_factory=dict)  # field id -> direct|jev|choice|open
    confidence: dict[str, float] = field(default_factory=dict)  # Jev confidence per field
    notes: list[str] = field(default_factory=list)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (text or "").lower())).strip()


# ---- tier 1: direct -------------------------------------------------------------------------
_IDENTITY = [
    (r"^(legal )?first name|given name", lambda p: p.first_name),
    (r"^(legal )?last name|surname|family name", lambda p: p.last_name),
    (r"^(full |legal )?name$", lambda p: p.full_name),
    (r"e-?mail", lambda p: p.email),
    (r"phone|mobile|telephone", lambda p: p.phone),
    (r"linkedin", lambda p: p.linkedin),
    (r"github", lambda p: p.github),
    (r"^(current )?(city|location)|where are you (located|based)", lambda p: p.city),
    (r"^preferred (first )?name|^nickname", lambda p: p.first_name),
    (r"website|portfolio|personal (site|page)|^url$", lambda p: p.github),
]
_STANDARD = [  # label regex -> key in profile.standard_answers
    (r"authori[sz]ed to work|legally (authori[sz]ed|eligible)|eligible to work", "Authorized to work in the US?"),
    (r"sponsorship|visa status", "Will you now or in the future require sponsorship for employment visa status?"),
    (r"disabilit", "Disability?"),
    (r"\bgender\b", "Gender"),
    (r"pronoun", "Pronouns"),
    (r"\brace\b|ethnic", "Race"),
    (r"hispanic|latino", "Hispanic or Latino?"),
    (r"veteran", "Veteran?"),
    (r"lgbt", "LGBTQ+?"),
    (r"sexual orientation", "Sexual orientation"),
]


def match_option(answer: str, options: list[str]) -> str | None:
    """Map a canonical answer ('Yes', 'Male', 'Asian') onto one of the form's option labels."""
    a = norm(answer)
    for o in options:
        if norm(o) == a:
            return o
    for o in options:  # 'Asian' -> 'Asian (Not Hispanic or Latino)'
        if a and (norm(o).startswith(a + " ") or norm(o).endswith(" " + a)):
            return o
    return None


def fuzzy_option(value: str, options: list[str]) -> str | None:
    """Best option for a free-text value: strict match, then substring either way, then close spelling."""
    strict = match_option(value, options)
    if strict:
        return strict
    v = norm(value)
    for o in options:
        if v and (v in norm(o) or (norm(o) and norm(o) in v)):
            return o
    close = difflib.get_close_matches(v, [norm(o) for o in options], n=1, cutoff=0.6)
    return next((o for o in options if norm(o) == close[0]), None) if close else None


# Education blocks repeat on a form: the n-th "School" field gets the n-th education entry from the profile.
_EDU_RULES = [
    (r"^(school|university|college|institution)( name)?$|^(school|university|college)\b", "school"),
    (r"^(degree|level of education)\b", "degree"),
    (r"^(discipline|field of study|major|area of study)\b", "field"),
    (r"^(start date month|start month|from month)", "start_month"),
    (r"^(start date year|start year|from year)", "start_year"),
    (r"^(end date month|end month|to month|graduation month)", "end_month"),
    (r"^(end date year|end year|to year|graduation year)", "end_year"),
    (r"^(gpa|grade point)", "gpa"),
]


def _education_answer(f: Field, label: str, profile: Profile) -> str | None:
    if not profile.education or f.type in ("file", "checkbox", "radio", "consent"):
        return None
    for pattern, what in _EDU_RULES:
        if re.search(pattern, label):
            break
    else:
        return None
    if f.occ > len(profile.education):
        return None  # no such entry in the profile: leave the extra block empty
    e = profile.education[f.occ - 1]
    value = {
        "school": e.school, "degree": e.level, "field": e.field, "gpa": e.gpa,
        "start_month": MONTHS[e.start_month - 1], "start_year": str(e.start_year),
        "end_month": MONTHS[e.end_month - 1], "end_year": str(e.end_year),
    }[what]
    if not value:
        return None
    if f.options:
        for candidate in (value, str(int(value)) if value.isdigit() else "", f"{MONTHS.index(value) + 1:02d}" if value in MONTHS else ""):
            hit = fuzzy_option(candidate, f.options) if candidate else None
            if hit:
                return hit
        return None
    return value


_COUNTRY = re.compile(r"^country( of residence| / region|/region)?$|^country\b(?!.*(citizen|authori|visa|phone|code))")
_STATE = re.compile(r"^(state|province|region)( / province|/province| or province)?$|^state\b(?!.*(sponsor|visa))")


def _place_answer(f: Field, label: str, profile: Profile) -> str | None:
    """Country and state/province of residence from the profile (never guessed)."""
    from .places import country_option, state_option

    if f.type in ("file", "checkbox", "radio", "consent"):
        return None
    if _COUNTRY.search(label) and profile.country:
        return country_option(profile.country, f.options) if f.options else profile.country
    if _STATE.search(label) and profile.state:
        return state_option(profile.state, f.options) if f.options else profile.state
    return None


def direct_answer(f: Field, profile: Profile, bank: dict[str, str]) -> str | None:
    label = f.label.lower().strip(" *:?")
    learned = bank.get(norm(f.label))
    if learned is None:  # keyword rules: key '~how did you hear|referral' matches any label containing one of the phrases
        nl = norm(f.label)
        for key, ans in bank.items():
            if key.startswith("~") is False:
                continue
            if any(p.strip() and p.strip() in nl for p in key[1:].split("|")):
                learned = ans
                break
    if learned is not None:
        return match_option(learned, f.options) if f.options else learned
    edu = _education_answer(f, label, profile)
    if edu is not None:
        return edu
    place = _place_answer(f, label, profile)
    if place is not None:
        return place
    if f.type in ("text", "email", "tel", "url", "combobox") and not f.options:
        for pattern, getter in _IDENTITY:
            if re.search(pattern, label):
                return getter(profile)
    for pattern, key in _STANDARD:
        if re.search(pattern, label) and key in profile.standard_answers:
            ans = profile.standard_answers[key]
            if f.options:
                return match_option(ans, f.options)  # None -> falls through to the choice tier
            return ans
    return None


# ---- tier selection -------------------------------------------------------------------------
_OPEN = re.compile(
    r"why (do|are|would|did)|tell us|describe|explain|what (interests|excites|motivates)|"
    r"cover letter|additional information|anything else|in your own words|passion|tell me",
    re.I,
)


# "If yes, please provide details" style boxes only apply when an earlier answer triggers them.
_FOLLOW_UP = re.compile(
    r"\bif (the answer is|you (answered|selected|chose|checked|said)|yes|so|applicable|other|you have)\b|"
    r"please (provide|explain|give|list|describe) (the )?(details|name|names|more)|\bif\b.{0,40}\b(yes|other)\b",
    re.I,
)


# Legal disclosures / attestations: silence in the profile is NOT a "No". These only get an answer
# from the profile's standard answers or from a saved answer - never from the model.
_SENSITIVE = re.compile(
    r"family member|immediate family|related to|relative of|close personal relationship|"
    r"conflict of interest|politically exposed|government (official|employee)|public official|"
    r"criminal|convicted|felony|misdemeanor|arrest|"
    r"non-?compete|restrictive covenant|non-?solicit|background (check|screen)|drug (test|screen)|"
    r"\bconsent\b|acknowledg|\bcertify\b|\battest\b|i understand|i agree|terms and conditions|"
    r"privacy (policy|notice)|under (penalty|oath)|"
    r"currently employed by|previously (employed|worked)|(worked|employed) (for|at|by) .{0,40}before",
    re.I,
)


def is_open(f: Field) -> bool:
    return not f.options and (f.type == "textarea" or bool(_OPEN.search(f.label)))


# ---- tier 2: choice (cheap model) -----------------------------------------------------------
_CHOICE_SYSTEM = """You fill out a job application on behalf of the candidate, using ONLY the candidate profile below.
For each field return a value. Rules:
- For fields with options, the value MUST be copied exactly from that field's options.
- Use only facts stated in the profile. Never guess or invent (salary, dates, addresses, references, etc.).
- If the profile does not contain enough information, use null. Missing information is NEVER a "No":
  do not answer yes/no questions the profile doesn't explicitly settle.
Return JSON: {"answers": {"<field id>": <string or null>, ...}}"""


def _choice_batch(fields: list[Field], profile: Profile, job: dict) -> dict[str, str | None]:
    listing = [
        {"id": f.id, "question": f.label, "type": f.type, **({"options": f.options} if f.options else {})}
        for f in fields
    ]
    user = (
        f"CANDIDATE PROFILE:\n{profile.text}\n\n"
        f"JOB: {job.get('role')} at {job.get('company')}\n\nFIELDS:\n{listing}"
    )
    ids = {f.id for f in fields}

    def check(obj):
        if not isinstance(obj.get("answers"), dict):
            raise ValueError('missing "answers" object')
        extra = set(obj["answers"]) - ids
        if extra:
            raise ValueError(f"unknown field ids: {sorted(extra)}")

    answers = llm.chat_json(_CHOICE_SYSTEM, user, model=None, validate=check,
                            purpose=f"form answers ({len(fields)} fields)")["answers"]
    # models sometimes return numbers/booleans; forms take strings
    return {k: (None if v is None else str(v).strip() or None) for k, v in answers.items()}


# ---- tier 3: open (stronger model) ----------------------------------------------------------
_OPEN_SYSTEM = """You write one answer for a job application on behalf of the candidate.
Rules:
- Ground every claim in the candidate profile / resume text. Do not invent employers, projects, numbers, skills or motivations.
- Use the job description only to choose which real experience to emphasise.
- First person, plain and specific, no filler or cliches. Match the length the question implies
  (default 80-150 words; stay within any stated character limit).
- If the question needs company-specific knowledge you don't have, keep it general and honest.
Return only the answer text."""


def _open_answer(f: Field, profile: Profile, resume_text: str, job: dict) -> str:
    user = (
        f"CANDIDATE PROFILE:\n{profile.text}\n\nRESUME TEXT:\n{resume_text}\n\n"
        f"JOB: {job.get('role')} at {job.get('company')}\nJOB DESCRIPTION:\n{job.get('description', '(not available)')}\n\n"
        f"QUESTION: {f.label}"
    )
    return llm.chat_text(_OPEN_SYSTEM, user, purpose=f"essay: {f.label}")


# ---- tier 2a: Jev decides from what is already known ----------------------------------------
_JEV_CANDIDATES = 8


def similar_known(label: str, known: list[dict], n: int = _JEV_CANDIDATES, cutoff: float = 0.35) -> list[dict]:
    """The known answers whose question text is closest to this label (cheap prefilter before Jev)."""
    q = norm(label)
    qw = set(q.split())
    scored = []
    for k in known:
        kq = norm(k.get("question_text") or "")
        if not kq or kq.startswith("~"):
            continue
        ratio = difflib.SequenceMatcher(None, q, kq).ratio()
        overlap = len(qw & set(kq.split())) / max(1, len(qw | set(kq.split())))
        s = max(ratio, overlap)
        if s >= cutoff:
            scored.append((s, k))
    scored.sort(key=lambda x: -x[0])
    return [k for _, k in scored[:n]]


def _jev_batch(fields: list[Field], profile: Profile, known: list[dict], job: dict) -> tuple[dict, dict, list[Field]]:
    """-> (values, confidences, still_pending). Option fields Jev isn't confident about come back in the flag list
    (third tuple element holds text fields for the chat model; low-confidence option fields are marked in values as None)."""
    from . import jev

    questions: dict[str, dict] = {}
    plan: dict[str, tuple[str, Field, dict]] = {}  # qid -> (kind, field, key->value)
    relevant: dict[str, dict] = {}
    to_chat: list[Field] = []
    for f in fields:
        cands = similar_known(f.label, known)
        for c in cands:
            relevant[norm(c["question_text"])] = c
        if f.options:
            keys = {f"o{i}": o for i, o in enumerate(f.options[:40])}
            crit = {k: v for k, v in keys.items()}
            crit["unknown"] = "Neither the candidate's profile nor the known answers settle this question."
            qid = f"f_{f.id}"
            questions[qid] = jev.choice(
                f"On a job application, the question is: \"{f.label}\". Using only the candidate's profile and the "
                f"answers the candidate has already given (known_answers), which option is the candidate's answer?", crit)
            plan[qid] = ("option", f, keys)
        elif cands:
            keys = {f"k{i}": c for i, c in enumerate(cands)}
            crit = {k: f"{c['question_text']} -> {c['answer']}" for k, c in keys.items()}
            crit["none"] = "None of these asks the same thing."
            qid = f"f_{f.id}"
            questions[qid] = jev.choice(
                f"A job application asks: \"{f.label}\". Which previously answered question asks exactly the same "
                f"thing (same meaning, just worded differently)?", crit)
            plan[qid] = ("known", f, keys)
        else:
            to_chat.append(f)

    values: dict[str, str | None] = {}
    confs: dict[str, float] = {}
    if questions:
        state = {
            "candidate_profile": profile.text,
            "known_answers": [{"question": c["question_text"], "answer": c["answer"]} for c in relevant.values()],
            "job": f"{job.get('role')} at {job.get('company')}",
        }
        answers = jev.decide(state, questions, session_id=f"job-{job.get('id', 'x')}", purpose="form answers")
        for qid, (kind, f, keys) in plan.items():
            key, conf = jev.picked(answers.get(qid))
            confs[f.id] = conf
            if kind == "option":
                values[f.id] = keys.get(key) if key and key != "unknown" else None
            else:
                if key and key != "none" and key in keys:
                    values[f.id] = keys[key]["answer"]
                else:
                    to_chat.append(f)  # no known answer fits: the chat model may still find it in the profile
    return values, confs, to_chat


# ---- orchestration --------------------------------------------------------------------------
def answer_fields(
    fields: list[Field], profile: Profile, bank: dict[str, str], job: dict, resume_text: str = "",
    known: list[dict] | None = None, use_jev: bool = True,
) -> Result:
    res = Result()
    pending_choice: list[Field] = []
    open_fields: list[Field] = []
    for f in fields:
        if f.type == "file":
            continue  # resume / cover letter uploads are handled by the ATS adapter
        val = direct_answer(f, profile, bank)
        if val is not None:
            res.values[f.id], res.tiers[f.id] = val, "direct"
        elif _SENSITIVE.search(f.label):
            res.unanswered.append(
                {"id": f.id, "label": f.label, "required": f.required, "options": f.options,
                 "reason": "legal/consent question - only you can answer this"}
            )
        elif not f.options and _FOLLOW_UP.search(f.label):
            res.unanswered.append(
                {"id": f.id, "label": f.label, "required": f.required, "reason": "conditional follow-up - only if the question before it applies"}
            )
        elif is_open(f) and not f.required:
            res.unanswered.append({"id": f.id, "label": f.label, "required": False, "reason": "optional essay skipped"})
        elif is_open(f):
            open_fields.append(f)
        else:
            pending_choice.append(f)

    # Essays run in parallel with everything below (each is an independent chat call).
    pool = ThreadPoolExecutor(max_workers=4) if open_fields else None
    futures = {f.id: pool.submit(_open_answer, f, profile, resume_text, job) for f in open_fields} if pool else {}

    # Jev first: it maps questions onto known answers and picks options, in one fast request per form.
    if pending_choice and use_jev:
        try:
            values, confs, to_chat = _jev_batch(pending_choice, profile, known or [], job)
            chat_ids = {f.id for f in to_chat}
            for f in pending_choice:
                if f.id in chat_ids:
                    continue
                val = values.get(f.id)
                if val:
                    res.values[f.id], res.tiers[f.id] = val, "jev"
                    res.confidence[f.id] = round(confs.get(f.id, 0.0), 2)
                else:
                    res.unanswered.append({"id": f.id, "label": f.label, "options": f.options, "required": f.required,
                                           "reason": f"not settled by your profile or known answers (Jev {confs.get(f.id, 0):.2f})"})
            pending_choice = to_chat
        except Exception as e:  # Jev outage: fall back to the chat model for this form
            res.notes.append(f"Jev unavailable, used the chat model: {str(e)[:120]}")

    if pending_choice:
        try:
            answers = _choice_batch(pending_choice, profile, job)
        except Exception as e:
            answers = {}
            for f in pending_choice:
                res.unanswered.append({"id": f.id, "label": f.label, "reason": f"choice model error: {e}"})
            pending_choice = []
        for f in pending_choice:
            raw = answers.get(f.id)
            val = match_option(raw, f.options) if (raw and f.options) else raw
            if val in (None, ""):
                res.unanswered.append(
                    {"id": f.id, "label": f.label, "options": f.options, "required": f.required, "reason": "not in profile"}
                )
            else:
                res.values[f.id], res.tiers[f.id] = val, "choice"

    for fid, fut in futures.items():
        f = next(x for x in open_fields if x.id == fid)
        try:
            res.values[fid], res.tiers[fid] = fut.result(), "open"
        except Exception as e:  # never let one field sink the whole form
            res.unanswered.append({"id": fid, "label": f.label, "reason": f"open model error: {e}"})
    if pool:
        pool.shutdown(wait=False)
    return res
