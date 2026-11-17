"""Provider-agnostic LLM wrapper (OpenAI SDK pointed at OpenRouter)."""
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field

import openai
from openai import OpenAI

from . import calllog, config


@dataclass
class Usage:
    calls: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    invalid_json: int = 0
    per_model: dict = field(default_factory=dict)
    seconds: float = 0.0  # wall time spent waiting on chat models
    jev_calls: int = 0
    jev_input_tokens: int = 0
    jev_cost: float = 0.0
    jev_seconds: float = 0.0


usage = Usage()
_client: OpenAI | None = None
_client_key = ""


def client() -> OpenAI:
    """OpenRouter client for the signed-in person's key (rebuilt when the key changes: each person brings their own)."""
    global _client, _client_key
    key = config.require("OPENROUTER_API_KEY")
    if _client is None or key != _client_key:
        _client = OpenAI(api_key=key, base_url=config.OPENROUTER_BASE_URL, default_headers={"X-Title": "Appli"})
        _client_key = key
    return _client


def _extract_json(text: str):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def reasoning_for(model: str) -> dict | None:
    """OpenRouter's reasoning setting for this model, from .env: LLM_REASONING_FAST / LLM_REASONING_WRITE.

    off -> no hidden thinking (DeepSeek extraction: ~1s instead of ~7s, and no budget-exhausted empty replies);
    low / medium / high -> thinking effort. Hidden thinking counts against max_tokens, so 'off' also prevents the
    truncated-JSON retries that thinking causes on long postings."""
    kind = "EXTRACT" if model == os.getenv("LLM_MODEL_EXTRACT") else "FAST" if model == os.getenv("LLM_MODEL_FAST") else "WRITE"
    level = (os.getenv(f"LLM_REASONING_{kind}") or ("low" if kind == "WRITE" else "off")).strip().lower()
    if level in ("off", "none", "false", "0"):
        return {"enabled": False}
    if level in ("minimal", "low", "medium", "high"):
        return {"effort": level}
    return None  # "default": let the provider decide


def _complete(messages, model, json_mode, max_tokens, temperature, low_reasoning=True, purpose="chat"):
    """One completion with exponential backoff on rate limits / server errors. Logs one line per call.

    Returns the message content (possibly empty: reasoning models sometimes spend the whole budget thinking).
    `low_reasoning=False` only means "the provider rejected the reasoning parameter, don't send it".
    """
    reasoning = reasoning_for(model)
    for attempt in range(5):
        kwargs = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if low_reasoning and reasoning is not None:
            kwargs["extra_body"] = {"reasoning": reasoning}
        t0 = time.time()
        try:
            resp = client().chat.completions.create(**kwargs)
            usage.seconds += time.time() - t0
            break
        except openai.BadRequestError as e:
            usage.seconds += time.time() - t0
            if json_mode:
                json_mode = False  # provider rejects response_format; rely on the prompt
            elif low_reasoning:
                low_reasoning = False  # provider rejects the reasoning param
            else:
                calllog.record("chat", model, purpose, time.time() - t0, f"FAILED {str(e)[:80]}", ok=False)
                raise
            calllog.note(f"[chat] {model.split('/')[-1]} rejected a parameter, retrying without it")
        except (openai.RateLimitError, openai.APIConnectionError, openai.InternalServerError) as e:
            usage.seconds += time.time() - t0
            usage.retries += 1
            calllog.record("chat", model, purpose, time.time() - t0, f"{type(e).__name__}, retry in {2**attempt}s", ok=False)
            time.sleep(2**attempt)
    else:
        raise RuntimeError(f"LLM call failed after retries ({model})")
    took = time.time() - t0
    usage.calls += 1
    tin = tout = 0
    if resp.usage:
        tin, tout = resp.usage.prompt_tokens, resp.usage.completion_tokens
        usage.prompt_tokens += tin
        usage.completion_tokens += tout
    out = resp.choices[0].message.content or ""
    speed = f"  ({tout / took:.0f} tok/s)" if took > 0 and tout else ""
    calllog.record("chat", model, purpose, took, f"in {tin:,} / out {tout:,} tok{speed}" + ("  EMPTY reply" if not out.strip() else ""))
    return out


def chat_text(system: str, user: str, model: str | None = None, max_tokens: int = 1500, purpose: str = "text") -> str:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    model = model or config.model("write")
    for budget in (max_tokens, max_tokens * 3):
        out = _complete(msgs, model, False, budget, 0.4, purpose=purpose).strip()
        if out:
            return out
        usage.retries += 1
    raise RuntimeError("model returned empty text twice")


def chat_json(system: str, user: str, model: str | None = None, max_tokens: int = 3000, validate=None,
              purpose: str = "json"):
    """Ask for a JSON object. Up to 3 attempts; each retry gives the model more room and (from the 2nd) drops
    response_format. `validate(obj)` may raise ValueError to trigger a repair attempt."""
    msgs = [
        {"role": "system", "content": system + "\nRespond with a single JSON object and nothing else."},
        {"role": "user", "content": user},
    ]
    model = model or config.model("fast")
    last_err = None
    for attempt in range(3):
        raw = _complete(msgs, model, attempt == 0, max_tokens * (2**attempt), 0.0,
                        purpose=purpose if attempt == 0 else f"{purpose} (retry {attempt})")
        try:
            if not raw.strip():
                raise ValueError("empty response")
            obj = _extract_json(raw)
            if validate:
                validate(obj)
            return obj
        except (json.JSONDecodeError, ValueError) as e:
            usage.invalid_json += 1
            last_err = e
            if raw.strip():
                msgs = msgs + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That was invalid ({e}). Return corrected JSON only."},
                ]
    raise ValueError(f"LLM returned invalid JSON 3 times: {last_err}")


def claude_available() -> bool:
    return shutil.which("claude") is not None


def claude_json(system: str, user: str, purpose: str = "claude", timeout: int = 600):
    """One JSON reply from Claude through the local Claude Code CLI (`claude -p`, the person's own Claude login: no API
    key). No tools, no MCP servers, no user settings or plugins (their hooks would leak into the prompt): just text in,
    JSON out. Runs in an empty temp folder so it never reads the project."""
    import subprocess
    import tempfile

    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--setting-sources", "project", "--model", "claude-opus-5-5", "--effort", "high",
           "--system-prompt", system + "\nRespond with a single JSON object and nothing else."]
    t0 = time.time()
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run(cmd, input=user, capture_output=True, text=True, encoding="utf-8", timeout=timeout, cwd=cwd,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    took = time.time() - t0
    usage.calls += 1
    usage.seconds += took
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        out = {"is_error": True, "result": (p.stderr or p.stdout)[-300:]}
    if p.returncode or out.get("is_error"):
        calllog.record("chat", "claude-cli", purpose, took, f"FAILED {str(out.get('result'))[:80]}", ok=False)
        raise RuntimeError(f"Claude Code failed: {str(out.get('result'))[:200]}")
    u = out.get("usage") or {}
    calllog.record("chat", "claude-cli", purpose, took, f"in {u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0):,}"
                                                        f" / out {u.get('output_tokens', 0):,} tok")
    return _extract_json(out.get("result") or "")


def chat_stream(system: str, messages: list[dict], model: str | None = None, max_tokens: int = 2500,
                purpose: str = "chat stream"):
    """Yield the reply's text as it arrives. Closing the generator (the person pressed Stop, or the browser went away)
    closes the connection to the model, so nothing more is generated or paid for."""
    model = model or config.model("write")
    kwargs = dict(model=model, messages=[{"role": "system", "content": system}, *messages], max_tokens=max_tokens,
                  temperature=0.4, stream=True)
    reasoning = reasoning_for(model)
    if reasoning is not None:
        kwargs["extra_body"] = {"reasoning": reasoning}
    t0 = time.time()
    try:
        stream = client().chat.completions.create(**kwargs)
    except openai.BadRequestError:  # the provider rejects the reasoning parameter
        kwargs.pop("extra_body", None)
        stream = client().chat.completions.create(**kwargs)
    n = 0
    try:
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                n += len(delta)
                yield delta
    finally:
        stream.close()
        usage.calls += 1
        usage.seconds += time.time() - t0
        calllog.record("chat", model, purpose, time.time() - t0, f"streamed ~{n // 4:,} tok")
