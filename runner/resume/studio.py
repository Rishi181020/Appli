"""Tailor resume, after the first match: pick the skills to target, generate, compare, verify, chat, versions, diagnose.

One draft per pasted posting, in users/<Name>/resume-studio/<draft id>/ (manual.py starts it):
    draft.json   the posting, the match, and everything below
    export/      the current resume (PDF or .docx), studio-state.json, and versions/ (each past one, restorable)

A draft built from the experience bank (mode "bank") is a list of bullets, each with a stack of versions: the closest
bullet of the base resume (None when it's new material), then what the writer drafted, then every regeneration, chat
edit or edit of your own. Revert pops the top one. Every model-written bullet passes bankbuild.verify (its quote is in
the bank entry, its numbers come from that entry) before it goes on the page. Without a usable bank the draft is the
skills-only tailoring (mode "skills"): skills added to the base resume's Skills lines.
"""
import json
import re
import uuid
from difflib import SequenceMatcher

from .. import bank, config, llm, userconfig
from ..profile import load_profile
from ..style import STYLE, clean
from . import bankbuild, history, manual, match, select, tex

MARKER = "@@EDIT@@"  # the chat model writes its reply, then this line, then the edit as JSON
WEAK_VERB = re.compile(r"^(helped|assisted|worked|responsible|participated|involved|handled|tasked|did|made|utilized|"
                       r"was|were|duties|in charge)\b", re.I)
LONG_BULLET = 220


# ---- storage -------------------------------------------------------------------------------------------------------
def _folder(draft_id: str):
    return manual.folder(draft_id)


def load(draft_id: str) -> dict:
    select.base_docs.cache_clear()  # cached sources belong to whoever used the server last
    try:
        return json.loads((_folder(draft_id) / "draft.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Draft not found. Analyze the job description again.") from None


def save(d: dict) -> None:
    target = _folder(d["draft_id"])
    (target / "draft.json").write_text(json.dumps(d), encoding="utf-8")
    export = target / "export"
    if export.exists() and any(f.is_file() for f in export.glob("*")):  # kept with each version, for restore
        state = {k: d.get(k) for k in _STATE}
        (export / "studio-state.json").write_text(json.dumps(state), encoding="utf-8")


_STATE = ("mode", "targets", "bank", "bullets", "order", "reviews", "score_after", "accepted", "file")


def _profile():
    return load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)


def bank_ready() -> bool:
    return len([e for e in bank.load()["entries"] if e["kind"] not in ("skills", "narrative", "education")]) >= 2


# ---- 1. analyze: the match, the score card and the keywords to pick from -------------------------------------------
def keyword_groups(info: dict, profile_text: str) -> list[dict]:
    """The posting's terms the chosen resume lacks in exact wording, grouped: have (your materials show it), similar
    (shown under another name), missing (nowhere in your materials, or you said you don't have it)."""
    ev = match.norm_text(select.evidence_text(profile_text))
    declined = {match.norm_text(x) for x in bank.load()["declined"]}
    equivalent = set(info.get("equivalent_any", [])) | set(info.get("equivalent", []))
    out = []
    for t in info["terms"]:
        if t["term"] not in set(info["missing"]):
            continue
        no = match.norm_text(t["term"]) in declined
        group = "missing" if no else "have" if match.supported(t, ev) else "similar" if t["term"] in equivalent else "missing"
        out.append({"term": t["term"], "category": t.get("category", "concept"), "required": bool(t.get("required")),
                    "group": group, "declined": no})
    return out


def analyze(body: dict) -> dict:
    d = manual.analyze(body)
    profile = _profile()
    d.update(keywords=keyword_groups(d["match"], profile.text), score_base=d["match"].get("breakdown"),
             bank_ready=bank_ready(), mode=None, chat=[], reviews=[], bullets={}, order={})
    d["targets"] = [k["term"] for k in d["keywords"] if k["group"] != "missing"]
    save(d)
    return view(d)


def open_draft(draft_id: str) -> dict:
    return view(load(draft_id))


def drafts() -> list[dict]:
    """Your earlier drafts, newest first, to reopen."""
    root = config.HOME / "resume-studio" if config.HOME else None
    out = []
    for f in sorted(root.glob("*/draft.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30] if root and root.exists() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"draft_id": d["draft_id"], "company": d["job"]["company"], "role": d["job"]["role"],
                    "mode": d.get("mode"), "pct": (d.get("score_after") or d.get("score_base") or {}).get("pct"),
                    "versions": len(history.versions(f.parent / "export"))})
    return out


# ---- 2. generate ---------------------------------------------------------------------------------------------------
def generate(draft_id: str, targets: list[str], extraction_confirmed: bool = False, log=print) -> dict:
    """Build the tailored resume aimed at the picked keywords only (the ones left out aren't pushed in)."""
    d = load(draft_id)
    if d["version"] != select.resume_version():
        raise ValueError("Your base resumes changed. Analyze the description again.")
    known = {k["term"] for k in d.get("keywords", [])}
    if not isinstance(targets, list) or not set(map(str, targets)) <= known:
        raise ValueError("Pick keywords from the list")
    info = d["match"]
    missing = set(info["missing"])
    aimed = {**info, "terms": [t for t in info["terms"] if t["term"] not in missing or t["term"] in targets]}
    d["targets"] = list(targets)
    profile = _profile()
    if bank_ready():
        r = bankbuild.build_draft({**d["job"], "id": draft_id}, aimed, profile, info["chosen"], log=log,
                                  folder=_folder(draft_id) / "export")
        d.update(mode="bank", bank=r, bullets={}, order={}, chat=d.get("chat") or [])
        _sync(d)
        d["reviews"] = _verify_cards(d, profile)
    else:
        accepted = [e["id"] for e in d["edits"]["edits"] if e.get("skill") in targets]
        result = manual.export(draft_id, accepted, extraction_confirmed=extraction_confirmed)
        if not result.get("ok"):
            raise ValueError(result.get("error") or "Could not build your resume")
        d.update(mode="skills", accepted=accepted, file=result["file"], reviews=[])
    d["score_after"] = _score(d, profile)
    save(d)
    return view(d)


def _text_of(d: dict) -> str:
    """The current tailored resume as plain text."""
    from . import word

    if d.get("mode") == "bank":
        r = d["bank"]
        if r.get("tex"):
            return tex.document_plain((config.ROOT / r["tex"]).read_text(encoding="utf-8"))
        return word.parse(config.ROOT / r["file"])["text"]
    path = config.ROOT / d["file"] if d.get("file") else None
    if not path or not path.exists():
        return ""
    src = path.with_suffix(".tex")
    if src.exists():
        return tex.document_plain(src.read_text(encoding="utf-8"))
    if path.suffix == ".docx":
        return word.parse(path)["text"]
    from ..cover_letter import resume_text

    return resume_text(str(path))


def _score(d: dict, profile) -> dict | None:
    """The four-part match of the tailored resume, on the posting's full requirements (the before/after card)."""
    text = _text_of(d)
    info = d["match"]
    return match.breakdown(profile, text, info["terms"], info.get("quals"), set(info.get("equivalent", []))) if text else None


# ---- 3. the bullets: compare, accept, revert, edit, regenerate ----------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%+#. ]", " ", (s or "").replace("**", "").lower())).strip()


def _base_bullets(key: str) -> list[tuple[str, str]]:
    """(the entry it's under, e.g. "Jio Platforms Limited - Software Engineer", text) for each base resume bullet."""
    doc = select.base_docs().get(key) or {}
    if doc.get("format") == "docx":
        return [("", b) for b in doc.get("bullets") or []]  # a Word resume's bullets don't say which entry they're in
    return [(u.context, tex.plain(u.text)) for u in doc.get("units", []) if u.kind == "bullet"]


_STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "for", "with", "on", "by", "at", "from", "as", "into", "using"}


def _overlap(a: str, b: str) -> float:
    x, y = ({w.strip(".") for w in _norm(s).split()} - _STOP - {""} for s in (a, b))
    return len(x & y) / len(x | y) if x and y else 0.0


def _closest(text: str, base: list[tuple[str, str]], entry: dict | None = None, taken: set | None = None) -> str | None:
    """The base resume's bullet this one rewrites: the most similar line under the same job or project on your resume
    (any line, for a Word resume). None = new material from your bank."""
    taken = taken if taken is not None else set()
    names = [_norm(entry.get(k, "")) for k in ("org", "title")] if entry else []
    same = [b for ctx, b in base if any(n and n in _norm(ctx) for n in names)]
    pool, floor = (same, 0.12) if same else ([b for _, b in base], 0.3)
    scored = [(max(_overlap(text, b), SequenceMatcher(None, _norm(text), _norm(b), autojunk=False).ratio() - 0.3), b)
              for b in pool if b not in taken]
    best = max(scored, default=(0.0, None))
    if best[0] >= floor:
        taken.add(best[1])
        return best[1]
    return None


def _sync(d: dict) -> None:
    """Match the bullets model to what's on the page now (fitting the page may trim or add bullets)."""
    r = d["bank"]
    base = _base_bullets(d["match"]["chosen"])
    bullets, order = d.setdefault("bullets", {}), {}
    for sec in r["plan"]["sections"]:
        for x in sec["entries"]:
            eid, used = x["id"], set()
            taken = {v["stack"][0] for v in bullets.values() if v["eid"] == eid and v["stack"][0]}
            order[eid] = []
            for b in (r["draft"]["entries"].get(eid) or {}).get("bullets", []):
                bid = next((k for k, v in bullets.items() if v["eid"] == eid and k not in used and v["stack"][-1] == b["text"]), None)
                if bid is None:
                    bid = uuid.uuid4().hex[:8]
                    base_line = _closest(b["text"], base, r["entries"].get(eid), taken)
                    bullets[bid] = {"eid": eid, "stack": [base_line, b["text"]], "status": "pending",
                                    "source": "generated"}
                used.add(bid)
                order[eid].append(bid)
    d["order"] = order


def _rerender(d: dict, skills: list[dict] | None = None) -> None:
    edits = {"bullets": {eid: [d["bullets"][b]["stack"][-1] for b in bids if d["bullets"][b]["stack"][-1]]
                         for eid, bids in d["order"].items()}}
    if skills is not None:
        edits["skills"] = skills
    d["bank"] = bankbuild.rebuild({**d["job"], "resume_edits": d["bank"]}, edits, _profile())
    _sync(d)
    d["score_after"] = _score(d, _profile())


def _bullet(d: dict, bid: str) -> dict:
    if d.get("mode") != "bank":
        raise ValueError("Generate a resume from your experience bank first")
    if bid not in d.get("bullets", {}) or not any(bid in v for v in d["order"].values()):
        raise ValueError("That line isn't on the resume any more")
    return d["bullets"][bid]


def change(draft_id: str, bid: str, action: str, text: str | None = None) -> dict:
    d = load(draft_id)
    b = _bullet(d, bid)
    if action == "accept":
        b["status"] = "accepted"
        save(d)
        return view(d)
    if action == "revert":
        if len(b["stack"]) > 1:
            b["stack"].pop()
        b["status"] = "reverted"
        if b["stack"][-1] is None:  # new material that wasn't on your resume: off the page
            d["order"][b["eid"]].remove(bid)
    elif action == "edit":
        words = clean(str(text or "").strip())
        if not words:
            raise ValueError("Type the line, or revert it")
        b["stack"].append(words)  # your own words: no check needed
        b.update(status="accepted", source="you")
    elif action == "regenerate":
        b["stack"].append(_rewrite(d, bid, "Write this bullet again with different wording and a different angle on "
                                           "the same material. Keep everything it claims true to the entry."))
        b.update(status="pending", source="regenerated")
    else:
        raise ValueError("Unknown action")
    _rerender(d)
    save(d)
    return view(d)


_REWRITE_SYSTEM = """You rewrite ONE resume bullet for a job, from the candidate's own experience-bank entry.
- State only what the entry says. `evidence` must be a passage copied word for word from the entry text (one or two
  consecutive sentences) that supports everything in the bullet.
- Numbers only when they appear in that evidence. Never invent a metric, scale or outcome.
- Use the posting's exact wording where the evidence shows that skill. Mark at most two key terms **bold**.
- One idea, 1-2 lines (90-200 characters), starting with a strong verb.
""" + STYLE + """
Return JSON: {"text": str, "evidence": str}"""


def _context(d: dict, bid: str) -> tuple[dict, str]:
    """(the bank entry behind this bullet, the prompt context for it)."""
    b = d["bullets"][bid]
    entries = {e["id"]: e for e in bank.load()["entries"]}
    e = entries.get(b["eid"]) or {"id": b["eid"], "text": d["bank"].get("evidence_text", {}).get(b["eid"], ""),
                                  "title": "", "org": ""}
    info = d["match"]
    reqs = ", ".join(("*" if t.get("required") else "") + t["term"] for t in info["terms"])
    prompt = (f"JOB: {d['job']['role']} at {d['job']['company']}\nREQUIREMENTS (* = required): {reqs}\n"
              f"ENTRY ({e.get('title', '')}, {e.get('org', '')}):\n{e['text'][:4000]}\n\nCURRENT BULLET: {b['stack'][-1]}")
    return e, prompt


def _checked(d: dict, eid: str, text: str, evidence: str) -> str:
    """The bullet if it passes the same checks as a built resume, else ValueError with why."""
    by_id = {e["id"]: e for e in bank.load()["entries"]}
    if eid not in by_id:
        raise ValueError("That bullet's bank entry was deleted")
    kept, dropped = bankbuild.verify({"entries": {eid: {"bullets": [{"text": text, "evidence": evidence}]}}}, by_id)
    if dropped or not kept["entries"][eid]["bullets"]:
        raise ValueError(f"Not used: {dropped[0]['reason'] if dropped else 'empty'}")
    return kept["entries"][eid]["bullets"][0]["text"]


def _rewrite(d: dict, bid: str, instruction: str) -> str:
    e, ctx = _context(d, bid)
    prompt = config.USER.tailor_prompt if config.USER else ""
    system = _REWRITE_SYSTEM + (f"\nThe candidate's standing instruction: {prompt}" if prompt else "")
    user = f"{ctx}\n\nINSTRUCTION: {instruction}"
    out = (llm.claude_json(system, user, purpose="tailor: rewrite bullet") if llm.claude_available() else
           llm.chat_json(system, user, model=config.model("write"), max_tokens=1500, purpose="tailor: rewrite bullet"))
    return _checked(d, e["id"], clean(str(out.get("text") or "").strip()), str(out.get("evidence") or ""))


# ---- 4. keyword verify: "Did you use X?" ---------------------------------------------------------------------------
def _verify_cards(d: dict, profile) -> list[dict]:
    """A question for every posting skill on the tailored resume that your bank, profile and resumes don't show."""
    ev = match.norm_text(select.evidence_text(profile.text) + "\n" + "\n".join(e["text"] for e in bank.load()["entries"]))
    declined = {match.norm_text(x) for x in bank.load()["declined"]}
    terms = {t["term"]: t for t in d["match"]["terms"]}
    cards, asked = [], set()

    def ask(term: str, bid: str | None, where: str):
        t = terms.get(term) or {"term": term, "aliases": []}
        if term in asked or match.norm_text(term) in declined or match.supported(t, ev):
            return
        asked.add(term)
        cards.append({"id": f"k{len(cards) + 1}", "term": term, "bid": bid, "where": where, "state": "open"})

    for a in d["bank"].get("skills_added") or []:
        if not a.get("backed") and not a.get("suggested"):
            ask(a["skill"], None, f"Skills: {a['group']}")
    for bid, b in d["bullets"].items():
        text = match.norm_text(b["stack"][-1] or "")
        for t in terms.values():
            if match.term_present(text, t):
                ask(t["term"], bid, b["stack"][-1])
    return cards


def answer(draft_id: str, card_id: str, choice: str) -> dict:
    """keep: you have it (saved to your bank's confirmed skills). remove: you don't (it comes off this resume and is
    never added again). similar: you'll say what you did instead, in the chat."""
    d = load(draft_id)
    card = next((c for c in d.get("reviews", []) if c["id"] == card_id), None)
    if card is None:
        raise ValueError("Unknown question")
    if choice not in ("keep", "remove", "similar"):
        raise ValueError("Unknown answer")
    card["state"] = choice
    if choice == "similar":
        save(d)
        return view(d)
    bank.answer_skill(card["term"], choice == "keep")
    if choice == "remove":
        if card.get("bid") and card["bid"] in d["bullets"]:
            b = d["bullets"][card["bid"]]
            while len(b["stack"]) > 1 and match.term_present(match.norm_text(b["stack"][-1] or ""),
                                                             {"term": card["term"], "aliases": []}):
                b["stack"].pop()  # back to a version without it (your own resume's line, or off the page)
            b["status"] = "reverted"
            if b["stack"][-1] is None:
                d["order"][b["eid"]].remove(card["bid"])
            _rerender(d)
        else:
            t = {"term": card["term"], "aliases": []}
            skills = [{"group": g["group"], "items": ", ".join(i for i in bankbuild._items(g)
                                                               if not match.term_present(match.norm_text(i), t))}
                      for g in d["bank"]["draft"].get("skills", [])]
            _rerender(d, skills)
    save(d)
    return view(d)


# ---- 5. chat: one line at a time, streamed ---------------------------------------------------------------------------
_CHAT_SYSTEM = """You are the candidate's resume editor, working on a resume tailored to ONE job. The candidate asks
for changes in plain words. You change ONE bullet per message.
Honesty rules (code checks them; an edit that breaks them is thrown away):
- A bullet states only what its bank entry says. `evidence` is a passage copied word for word from that entry's text.
- Numbers only when they appear in the evidence. Never invent metrics, scale, tools or outcomes.
If the request needs facts the entry doesn't have, say so and ask the candidate what they actually did.
Reply in 1-3 short sentences. Then, only if you change a bullet, a line with exactly @@EDIT@@ and after it one JSON
object: {"bid": "<the bullet's id>", "text": "<the new bullet>", "evidence": "<quote from its entry>"}.
""" + STYLE


def _chat_context(d: dict, focus: str | None) -> str:
    entries = {e["id"]: e for e in bank.load()["entries"]}
    info = d["match"]
    lines = [f"JOB: {d['job']['role']} at {d['job']['company']}",
             "REQUIREMENTS (* = required): " + ", ".join(("*" if t.get("required") else "") + t["term"] for t in info["terms"]),
             "", "RESUME (bullet id: text):"]
    for path, bid, text in _paths(d):
        lines.append(f"[{bid}] {path}: {text}" + ("   <- the candidate is asking about this one" if bid == focus else ""))
    lines += ["", "BANK ENTRIES BEHIND THEM:"]
    for eid in d["order"]:
        e = entries.get(eid)
        if e:
            lines.append(f"--- entry for bullets {', '.join(d['order'][eid])} ({e['title']}, {e['org']}):\n{e['text'][:2500]}")
    return "\n".join(lines)


def chat(draft_id: str, message: str, bid: str | None = None, facts: bool = False):
    """Yield events for the dashboard: delta (reply text as it arrives), change (a checked edit, with the new state),
    rejected (an edit that failed the checks), error, done. facts: the message is what you actually did: it's saved
    to that bullet's bank entry first, so the rewrite can quote it."""
    d = load(draft_id)
    if d.get("mode") != "bank":
        raise ValueError("Chat edits bullets of a resume built from your experience bank: generate one first")
    message = str(message or "").strip()[:2000]
    if not message:
        raise ValueError("Type a message")
    if bid is not None:
        _bullet(d, bid)
    if facts:
        bank.add_facts(d["bullets"][bid]["eid"] if bid else None, re.sub(r"^here is what i actually did[^:]*:\s*", "",
                                                                          message, flags=re.I))
    prompt = config.USER.tailor_prompt if config.USER else ""
    system = _CHAT_SYSTEM + (f"\nThe candidate's standing instruction for every edit: {prompt}" if prompt else "")
    history_ = [{"role": m["role"], "content": m["content"]} for m in d.get("chat", [])[-6:]]
    user = f"{_chat_context(d, bid)}\n\nREQUEST: {message}"
    d.setdefault("chat", []).append({"role": "user", "content": message, "bid": bid})
    reply, held = "", ""
    stream = llm.chat_stream(system, [*history_, {"role": "user", "content": user}], purpose="tailor: chat")
    try:
        for delta in stream:
            reply += delta
            if MARKER in reply:
                continue
            # hold back what could be the start of the marker
            shown = reply[: max(0, len(reply) - len(MARKER))]
            if len(shown) > len(held):
                yield {"type": "delta", "text": shown[len(held):]}
                held = shown
        text, _, edit = reply.partition(MARKER)
        if len(text.rstrip()) > len(held):
            yield {"type": "delta", "text": text.rstrip()[len(held):]}
        msg = {"role": "assistant", "content": text.strip()}
        if edit.strip():
            try:
                obj = llm._extract_json(edit)
                target = str(obj.get("bid") or bid or "")
                b = _bullet(d, target)
                new = _checked(d, b["eid"], clean(str(obj.get("text") or "").strip()), str(obj.get("evidence") or ""))
                b["stack"].append(new)
                b.update(status="pending", source="chat")
                _rerender(d)
                msg["bid"] = target
                yield {"type": "change", "bid": target, "state": view(d)}
            except (ValueError, KeyError, AttributeError) as e:
                msg["rejected"] = str(e)
                yield {"type": "rejected", "reason": str(e)}
        d["chat"].append(msg)
        save(d)
        yield {"type": "done"}
    finally:
        stream.close()  # stopped early: the model stops too, and nothing half-done is saved


# ---- 6. versions, standing prompt, export style ------------------------------------------------------------------
def save_version(draft_id: str) -> dict:
    d = load(draft_id)
    save(d)  # studio-state.json goes with the files
    if not history.save_copy(_folder(draft_id) / "export", {"pct": (d.get("score_after") or {}).get("pct")}):
        raise ValueError("Generate a resume first")
    return view(d)


def restore(draft_id: str, vid: str) -> dict:
    d = load(draft_id)
    export = _folder(draft_id) / "export"
    save(d)
    history.restore(export, vid)
    try:
        d.update(json.loads((export / "studio-state.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass  # a version from before the studio kept its state: the file is back, the edit history isn't
    if d.get("mode") == "bank":
        d["score_after"] = _score(d, _profile())
    save(d)
    return view(d)


def job_versions() -> list[dict]:
    """Tailored resumes of your applications that have earlier versions."""
    out = []
    for folder in sorted(config.TAILORED_DIR.glob("*"), reverse=True) if config.TAILORED_DIR.exists() else []:
        v = history.versions(folder)
        if v and folder.name.isdigit():
            out.append({"job_id": int(folder.name), "current": sorted(f.name for f in folder.glob("*.pdf")) +
                        sorted(f.name for f in folder.glob("*.docx")), "versions": v})
    return out


def restore_job(job_id: int, vid: str) -> str:
    """Restore an application's tailored resume; returns the restored file (relative to the app folder)."""
    folder = config.TAILORED_DIR / str(int(job_id))
    history.restore(folder, vid)
    files = sorted(folder.glob("*.pdf")) or sorted(folder.glob("*.docx"))
    if not files:
        raise ValueError("That version has no resume file")
    return str(files[-1].relative_to(config.ROOT))


def set_prompt(text: str) -> None:
    if config.USER is None:
        raise ValueError("Finish the setup screens first")
    config.USER.tailor_prompt = clean(str(text or "").strip())[:4000]
    userconfig.save(config.USER)


def set_style(style: dict, draft_id: str | None = None) -> dict:
    """Save your export style; the open draft is rebuilt with it."""
    if config.USER is None:
        raise ValueError("Finish the setup screens first")
    config.USER.resume_style = tex.clean_style(style if isinstance(style, dict) else {})
    userconfig.save(config.USER)
    if not draft_id:
        return {"style": config.USER.resume_style}
    d = load(draft_id)
    if d.get("mode") == "bank":
        _rerender(d)
    elif d.get("mode") == "skills":
        result = manual.export(draft_id, d.get("accepted", []), extraction_confirmed=True)
        if result.get("ok"):
            d["file"] = result["file"]
    save(d)
    return view(d)


# ---- 7. diagnose -----------------------------------------------------------------------------------------------------
def diagnose(draft_id: str) -> dict:
    """What a reviewer would flag, found by code: missing sections, bullets without a number, weak opening verbs,
    over-long bullets, a keyword used more than 3 times. Fixable ones carry an instruction for the chat."""
    d = load(draft_id)
    info = d["match"]
    issues = []

    def add(kind, text, bid=None, fix=None, severity="medium"):
        issues.append({"id": f"d{len(issues) + 1}", "kind": kind, "text": text, "bid": bid, "fix": fix, "severity": severity})

    if d.get("mode") == "bank":
        bullets = [(bid, text) for _, bid, text in _paths(d)]
        whole = _text_of(d)
        has_skills = bool(d["bank"]["draft"].get("skills"))
    else:
        doc = select.base_docs().get(info["chosen"]) or {}
        bullets = [(None, b) for b in _base_bullets(info["chosen"])]
        whole = doc.get("text", "")
        has_skills = bool(doc.get("skills_text"))
    if not re.search(r"^\s*(summary|profile|objective|about)\b", whole, re.I | re.M):
        add("missing_summary", "No summary at the top. Recruiters skim it first; add an 'About me' entry to your "
                               "experience bank if you want one written from your own words.", severity="low")
    if not has_skills:
        add("missing_skills", "No Skills section: ATSs look for one.", severity="high")
    for bid, text in bullets:
        plain = text.replace("**", "")
        if not re.search(r"\d", plain):
            add("no_number", f"No number: {plain[:90]}", bid,
                "Add a concrete number (users, %, time saved, team size) only if this bullet's bank entry states one; "
                "otherwise make the result more specific.", "low")
        if WEAK_VERB.search(plain):
            add("weak_verb", f"Weak opening: {plain[:90]}", bid, "Start with a strong, specific action verb.")
        if len(plain) > LONG_BULLET:
            add("too_long", f"{len(plain)} characters: {plain[:90]}", bid, "Cut this to under 200 characters; keep the substance.")
    if whole:
        s = match.score(whole, info["terms"])
        for term in s["stuffed"]:
            bid = next((b for b, t in reversed(bullets) if b and match.term_present(match.norm_text(t), {"term": term, "aliases": []})), None)
            add("stuffed", f'"{term}" appears {s["counts"][term]} times: more than 3 reads as keyword stuffing.', bid,
                f'Say this without the words "{term}".' if bid else None)
    if d.get("mode") != "bank":  # fixes edit bank bullets
        for i in issues:
            i["fix"] = None
    return {**view(d), "diagnosis": issues}


# ---- what the dashboard shows -------------------------------------------------------------------------------------
def _paths(d: dict) -> list[tuple[str, str, str]]:
    """(path like experience[0].bullet[2], bullet id, text) for every bullet on the page, in order."""
    out = []
    for sec in d["bank"]["plan"]["sections"]:
        for i, x in enumerate(sec["entries"]):
            for j, bid in enumerate(d["order"].get(x["id"], [])):
                text = d["bullets"][bid]["stack"][-1]
                if text:
                    out.append((f"{sec['type']}[{i}].bullet[{j}]", bid, text))
    return out


def view(d: dict) -> dict:
    out = {k: d.get(k) for k in ("draft_id", "job", "match", "edits", "restriction", "keywords", "targets", "mode",
                                 "score_base", "score_after", "bank_ready", "chat", "reviews", "file")}
    out["versions"] = history.versions(_folder(d["draft_id"]) / "export")
    out["style"] = config.USER.resume_style if config.USER else {}
    out["prompt"] = config.USER.tailor_prompt if config.USER else ""
    if d.get("mode") == "bank":
        r = d["bank"]
        paths = {bid: path for path, bid, _ in _paths(d)}
        out["file"] = r["file"]
        out["doc"] = {
            "pages": r["pages"], "warnings": r.get("warnings", []), "skills": r["draft"].get("skills", []),
            "sections": [{"type": s["type"], "heading": s["heading"], "entries": [
                {"id": x["id"], **{k: (r["entries"].get(x["id"]) or {}).get(k, "") for k in ("title", "org", "start", "end")},
                 "bullets": [{"bid": bid, "path": paths.get(bid), "text": d["bullets"][bid]["stack"][-1],
                              "base": d["bullets"][bid]["stack"][0], "status": d["bullets"][bid]["status"],
                              "source": d["bullets"][bid]["source"], "can_revert": len(d["bullets"][bid]["stack"]) > 1}
                             for bid in d["order"].get(x["id"], []) if d["bullets"][bid]["stack"][-1]]}
                for x in s["entries"]]} for s in r["plan"]["sections"] if s["type"] not in ("education",)],
        }
    return out
