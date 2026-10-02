"""How answers should read: like the candidate wrote them, not a model.

STYLE is added to every prompt that writes text for a form (short answers, essays, cover letters). clean() then
enforces the punctuation part in code, because a model doesn't always follow instructions: every typed answer
passes through it before it reaches a form.
"""
import re

STYLE = """Writing style (it must read like the candidate typed it themselves):
- Never use em dashes or en dashes. Use a comma, a period, or "and" instead.
- Plain keyboard characters only: straight quotes (' and "), three dots for an ellipsis, no arrows, bullets,
  emoji or other special symbols.
- No semicolons in short answers. No exclamation marks.
- Avoid words and phrases that sound machine-written: delve, leverage, utilize, tapestry, testament, realm, landscape,
  journey, passionate, thrilled, excited to, eager to, I am confident, fast-paced, cutting-edge, seamless, robust,
  synergy, spearhead, foster, honed, showcase, furthermore, moreover, additionally, in conclusion, ultimately,
  "not only ... but also", "I believe that".
- No lists of three adjectives, no rhetorical questions, no closing line that sums up or restates the answer.
- Mix short and longer sentences. Contractions (I'm, I've, it's) are fine. Simple, direct words."""

# what clean() swaps: the characters that most often give away generated text
_SWAPS = [
    (re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)"), "-"),  # 2019–2023 -> 2019-2023
    (re.compile(r"\s*[—―]\s*"), ", "),  # word — word / word—word -> word, word
    (re.compile(r"\s+–\s+"), ", "),  # spaced en dash used as a dash
    (re.compile(r"–"), "-"),  # any other en dash
    (re.compile(r"[‘’‚′]"), "'"),
    (re.compile(r"[“”„″]"), '"'),
    (re.compile(r"…"), "..."),
    (re.compile(r"[→➔➡]"), "to"),
    (re.compile(r"^[ \t]*[•●▪‣⁃][ \t]*", re.M), "- "),  # bullet characters at line start
    (re.compile(r"[•●▪]"), ","),
    (re.compile(r"[     ]"), " "),  # odd spaces
    (re.compile(r"[​‌‍﻿]"), ""),  # invisible characters
    (re.compile(r"!+(?=\s|$)"), "."),  # exclamation marks
]


def clean(text: str | None) -> str | None:
    """Make a typed answer read as hand-written: no em/en dashes, curly quotes, ellipsis or bullet characters,
    invisible characters or exclamation marks. Option values are never passed through this (they must match exactly)."""
    if not text:
        return text
    out = text
    for pat, rep in _SWAPS:
        out = pat.sub(rep, out)
    out = re.sub(r",\s*,", ",", out)  # "word, , word" after a swap
    out = re.sub(r",\s*([.?:;])", r"\1", out)  # ", ." -> "."
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.;:?])", r"\1", out)
    return out.strip()
