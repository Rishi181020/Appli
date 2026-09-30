"""Fallback resume choice from the job title alone (used when a posting has no usable text to score)."""
import re
from pathlib import Path

from . import config


def pick_resume(role: str | None) -> tuple[str, Path]:
    """(key, pdf) of the resume whose title_keywords (users/<Name>/appli.json) match the role most specifically
    (longest matching keyword, so "solutions engineer" beats "ai"); the first resume when nothing matches."""
    title = role or ""
    best_key, best_len = None, 0
    for key, words in config.RESUME_TITLE_KEYWORDS.items():
        for w in words:
            if len(w) > best_len and re.search(r"(?<![A-Za-z0-9])" + re.escape(w) + r"(?![A-Za-z0-9])", title, re.I):
                best_key, best_len = key, len(w)
    key = best_key or next(iter(config.RESUMES))
    return key, config.RESUMES[key]
