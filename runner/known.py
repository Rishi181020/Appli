"""Known answers: what the person saved themselves, plus what was learned from forms already filled.

Learning is deliberately narrow: only factual option questions (yes/no, dropdowns, radios) answered by Jev or the
chat model. Never essays, free text, legal/consent questions, or anything specific to one company or office.
"""
import re

from .answering import _FOLLOW_UP, _SENSITIVE, Field, norm
from .db import sb, user_id

_JOB_SPECIFIC = re.compile(
    r"this (role|position|job|opportunity|internship)|our (office|team|company|headquarters|hq|mission|product)|"
    r"\boffice\b|on-?site|in[- ]person|headquarter|\bhq\b|days (a|per) week|located in|relocat|commut|"
    r"which (location|office|team)|preferred location|start date|when (can|could|would) you|salary|compensation|"
    r"how did you hear|referr",
    re.I,
)


def load_known() -> list[dict]:
    """All saved + learned answers (plain questions only; '~rules' are handled by the direct tier)."""
    try:
        rows = sb().table("answers").select("question_key,question_text,answer,source,options,uses").execute().data
    except Exception:  # migration 003 not run yet: no source/options/uses columns
        rows = sb().table("answers").select("question_key,question_text,answer").execute().data
    return [r for r in rows if not (r.get("question_key") or "").startswith("~") and (r.get("question_text") or r.get("question_key"))
            and r.get("answer")]


def learnable(f: Field, tier: str, company: str | None) -> bool:
    if tier not in ("jev", "choice") or not f.options:
        return False
    if _SENSITIVE.search(f.label) or _FOLLOW_UP.search(f.label) or _JOB_SPECIFIC.search(f.label):
        return False
    if company:
        c = norm(company)
        words = [w for w in c.split() if len(w) > 3]
        nl = norm(f.label)
        if c and (c in nl or any(w in nl.split() for w in words)):
            return False
    return len(f.label) >= 8


def learn(items: list[tuple[Field, str, str]], company: str | None) -> int:
    """items = (field, value, tier). Upsert learned answers; never overwrite one Rishi saved himself."""
    todo = {}
    for f, value, tier in items:
        if learnable(f, tier, company) and value:
            todo[norm(f.label)] = (f, value)
    if not todo:
        return 0
    try:
        existing = {r["question_key"]: r for r in
                    sb().table("answers").select("question_key,source,uses").in_("question_key", list(todo)).execute().data}
    except Exception:
        return 0  # migration 003 not run: learning needs the source/uses columns
    n = 0
    for key, (f, value) in todo.items():
        row = existing.get(key)
        if row and row.get("source", "manual") != "learned":
            continue  # their own saved answer wins
        payload = {"owner_id": user_id(), "question_key": key, "question_text": f.label, "answer": value, "source": "learned",
                   "options": f.options[:40], "uses": (row or {}).get("uses", 0) + 1}
        sb().table("answers").upsert(payload, on_conflict="owner_id,question_key").execute()
        n += 1
    return n
