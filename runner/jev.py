"""Jev: a decision model on OpenRouter. Typed Choice / Noul answers with probabilities, no text.

Used for decisions only: mapping a form question onto an answer we already know, picking a resume, screening postings.
All questions in one request are answered in parallel and can't see each other, so send one request per form.
The model id comes from JEV_MODEL in .env (see .env.example); nothing is hardcoded.
"""
import os
import time

import httpx

from . import calllog, config, llm

JEV_URL = os.getenv("JEV_URL", "https://openrouter.ai/api/alpha/decisions")
JEV_MIN_CONFIDENCE = float(os.getenv("JEV_MIN_CONFIDENCE", "0.6"))
JEV_ENABLED = os.getenv("JEV_ENABLED", "1") != "0"


class JevError(RuntimeError):
    pass


def model_name() -> str:
    """JEV_MODEL from .env. Missing -> JevError, so callers fall back to the chat model instead of stopping."""
    name = os.getenv("JEV_MODEL", "").strip()
    if not name:
        raise JevError("JEV_MODEL is not set in .env (see .env.example)")
    return name


def choice(instructions: str, criteria: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions: str, yes: str, no: str) -> dict:
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


def decide(state, questions: dict[str, dict], session_id: str | None = None, timeout: float = 60,
           purpose: str = "decision") -> dict[str, dict]:
    """-> {question_id: answer}. Raises JevError when Jev can't be reached or answers malformed. Logs one line per call."""
    if not JEV_ENABLED:
        raise JevError("Jev is disabled (JEV_ENABLED=0)")
    if not questions:
        return {}
    model = model_name()
    label = f"{purpose} ({len(questions)} q)"
    body = {"model": model, "state": state, "questions": questions}
    if session_id:
        body["session_id"] = session_id[:256]
    headers = {"Authorization": f"Bearer {config.require('OPENROUTER_API_KEY')}", "X-Title": "Appli"}
    last = None
    t_all = time.time()
    for attempt in range(4):
        t0 = time.time()
        try:
            r = httpx.post(JEV_URL, json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as e:
            last = f"{type(e).__name__}: {e}"
            calllog.record("jev", model, label, time.time() - t0, f"{type(e).__name__}, retry in {2**attempt}s", ok=False)
            time.sleep(2**attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            usage_retry()
            calllog.record("jev", model, label, time.time() - t0, f"HTTP {r.status_code}, retry in {2**attempt}s", ok=False)
            time.sleep(2**attempt)
            continue
        if r.status_code != 200:
            calllog.record("jev", model, label, time.time() - t0, f"FAILED HTTP {r.status_code}", ok=False)
            raise JevError(f"HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        answers = data.get("answers")
        if not isinstance(answers, dict):
            calllog.record("jev", model, label, time.time() - t0, "FAILED malformed response", ok=False)
            raise JevError(f"malformed response: {str(data)[:200]}")
        u = data.get("usage") or {}
        tin, cost = int(u.get("input_tokens") or 0), float(u.get("cost") or 0)
        llm.usage.jev_calls += 1
        llm.usage.jev_input_tokens += tin
        llm.usage.jev_cost += cost
        llm.usage.jev_seconds += time.time() - t_all
        calllog.record("jev", model, label, time.time() - t0, f"in {tin:,} tok  ${cost:.5f}")
        return answers
    raise JevError(f"Jev unreachable after retries ({last})")


def usage_retry():
    llm.usage.retries += 1


def picked(answer: dict | None, min_confidence: float = JEV_MIN_CONFIDENCE) -> tuple[str | None, float]:
    """(choice key, confidence) for a Choice answer; key is None when missing or below the confidence bar."""
    if not answer or answer.get("type") != "choice":
        return None, 0.0
    conf = float(answer.get("confidence") or 0.0)
    key = answer.get("choice")
    if key is None or conf < min_confidence:
        return None, conf
    return str(key), conf
