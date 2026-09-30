"""Daily batch: fill application forms one at a time and stop before Submit.

Lifecycle of a job:  queued -> filling -> ready_for_review (tab open, waiting for you)
  -> submitted            when the confirmation page is detected (or you mark it on the dashboard)
  -> queued               if you close the tab / browser without submitting
The runner stays alive while review tabs are open so it can watch them.
"""
import ctypes
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from . import browser, calllog, config, cover_letter, forms, llm
from . import known as known_mod
from . import profile_md
from . import workday
from .answering import answer_fields, direct_answer, norm
from .ats import SUPPORTED, adapters
from .config import ROOT
from .db import dedupe_jobs, require_multi_user, sb, user_id
from .db import fresh as db_fresh
from .db import unpark_workday
from .profile import Profile, load_profile
from .resume import select as resume_select
from .resume import tailor
from .resume_picker import pick_resume
from .screening import active_screens, jev_screen, screen

OUT = ROOT / "out"
LOCK = OUT / "runner.lock"
STOP = OUT / "stop"

CONFIRM = re.compile(
    r"thank(s| you) for applying|thank you for (your )?application|"
    r"application (has been |was |is )?(successfully )?(submitted|received|complete)|"
    r"we('ve| have) received your application|successfully submitted (your )?application|"
    r"your application (is|has been) (in|submitted|sent)",
    re.I,
)
_CLOSED_ERR = re.compile(r"Target (page, context or browser )?(has been )?closed|Browser has been closed|has been closed", re.I)


class BrowserClosed(Exception):
    pass


# ---- single-instance lock -------------------------------------------------------------------
def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def running_pid() -> int | None:
    try:
        pid = int(LOCK.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def acquire_lock():
    OUT.mkdir(exist_ok=True)
    pid = running_pid()
    if pid and pid != os.getpid():
        raise SystemExit(f"Another run is already active (pid {pid}). Close it first.")
    LOCK.write_text(str(os.getpid()))
    STOP.unlink(missing_ok=True)


def release_lock():
    try:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()
    except OSError:
        pass


# ---- db helpers -----------------------------------------------------------------------------
def load_bank() -> dict[str, str]:
    bank = {}
    for r in sb().table("answers").select("question_key,answer").execute().data:
        k = r["question_key"].strip()
        # keyword rules ('~a|b') keep their phrases normalised but retain the marker
        bank[("~" + "|".join(norm(x) for x in k[1:].split("|"))) if k.startswith("~") else norm(k)] = r["answer"]
    return bank


def update(job_id: int, **fields):
    sb().table("jobs").update(fields).eq("id", job_id).execute()


def status_of(job_id: int) -> str | None:
    rows = sb().table("jobs").select("status").eq("id", job_id).execute().data
    return rows[0]["status"] if rows else None


def reset_stale() -> int:
    """Jobs left 'filling'/'ready_for_review' by an earlier run have no open tab any more."""
    res = (
        sb()
        .table("jobs")
        .update({"status": "queued", "status_reason": "Reset after an earlier run ended"})
        .in_("status", ["filling", "ready_for_review", "submission_required", "needs_help"])
        .execute()
    )
    return len(res.data)


def queued_count() -> int:
    return (
        sb().table("jobs").select("id", count="exact", head=True).eq("status", "queued").in_("ats", sorted(SUPPORTED))
        .execute().count or 0
    )


def next_job(ids: list[int] | None, exclude: set[int] | None = None) -> dict | None:
    """Claim exactly one job (so a crash never strands a batch). Jobs in `exclude` were already tried this run."""
    while True:
        if ids is not None:
            if not ids:
                return None
            jid = ids.pop(0)
            rows = sb().table("jobs").select("*").eq("id", jid).execute().data
            if not rows:
                print(f"Job {jid} not found - skipped")
                continue
            if rows[0]["status"] in ("submitted", "filling", "submission_required", "needs_help"):  # never refill one that's sent / open
                print(f"Job {jid} ({rows[0]['company']}) is {rows[0]['status']} - skipped")
                continue
        else:
            q = sb().table("jobs").select("*").eq("status", "queued").in_("ats", sorted(SUPPORTED))
            if exclude:
                q = q.not_.in_("id", sorted(exclude))
            rows = []
            try:  # jobs whose tailored resume Rishi already built go first
                rows = q.eq("resume_review", "built").order("updated_at").limit(1).execute().data
            except Exception:
                pass  # migration 002 not run yet
            if not rows:
                q = sb().table("jobs").select("*").eq("status", "queued").in_("ats", sorted(SUPPORTED))
                if exclude:
                    q = q.not_.in_("id", sorted(exclude))
                rows = q.order("fit_tier").order("days_posted").limit(1).execute().data
            if not rows:
                return None
        job = rows[0]
        claim = sb().table("jobs").update(
            {"status": "filling", "status_reason": None, "attempts": job["attempts"] + 1}
        ).eq("id", job["id"])
        if ids is None:
            claim = claim.eq("status", "queued")
        if claim.execute().data:
            return job


# ---- browser helpers ------------------------------------------------------------------------
def browser_alive(ctx: BrowserContext) -> bool:
    try:
        return len(ctx.pages) > 0
    except Exception:
        return False


def _wait_for_form(page: Page) -> bool:
    try:
        page.wait_for_selector("input:not([type=hidden]), textarea, select", timeout=15000)
        page.wait_for_timeout(500)  # let client-side frameworks finish rendering the rest of the form
        return True
    except Exception:
        return False


def _login_wall(page: Page) -> bool:
    return page.locator("input[type=password]:visible").count() > 0


def _has_captcha(page: Page) -> bool:
    return page.locator("iframe[src*='hcaptcha'][style*='visible'], .h-captcha:visible, .g-recaptcha:visible").count() > 0


# ---- one application ------------------------------------------------------------------------
class Steps:
    """Prints how long each stage of a job took: `      [step] posting page loaded      2.91s`."""

    def __init__(self):
        self.t = time.time()

    def __call__(self, name: str):
        now = time.time()
        calllog.note(f"[step] {name:<46} {now - self.t:6.2f}s")
        self.t = now
_BG = ThreadPoolExecutor(max_workers=3)  # cover letters and tailoring drafts run beside the browser work


def _fill_pass(page, pw, fields, state, profile, bank, job, resume, resume_path, key):
    """Answer and fill `fields`; returns (filled, unanswered, uploads)."""
    # Only open dropdowns whose answer isn't already known from the profile (opening each one costs time).
    step = Steps()
    # Every dropdown's options are read first (big search lists like country / school / city come back empty and are
    # typed into), so an answer is always one of the choices: a "start date" rule never answers a Yes / No dropdown.
    need_options = [f for f in fields if f.type == "combobox" and not f.options]
    forms.discover_options(page, need_options)

    cover_fields = [f for f in fields if f.type == "file" and forms.kind_of_file(f) == "cover"]
    cover_text = [f for f in fields if f.type in ("textarea", "text") and re.search(r"cover letter", f.label, re.I)]
    letter_future = None
    if (cover_fields or cover_text) and "letter" not in state:
        state["letter"] = None
        letter_future = _BG.submit(cover_letter.generate, profile, resume, job)  # drafted while the rest is answered

    step("read dropdown options" + (f" ({len(need_options)} opened)" if need_options else " (none needed)"))
    res = answer_fields([f for f in fields if f not in cover_text], profile, bank, job, resume, known=state.get("known"))
    for note in res.notes:
        print(f"    {note}")
    tiers = Counter(res.tiers.values())
    step("answered " + (", ".join(f"{v} {k}" for k, v in tiers.items()) or "nothing") + f", {len(res.unanswered)} left for you")
    if letter_future is not None:
        try:
            state["letter"], state["letter_problems"] = letter_future.result()
        except Exception as e:
            state["letter_problems"] = [f"cover letter failed: {type(e).__name__}"]
        step("waited for cover letter")
    letter = state.get("letter")
    if letter:
        for f in cover_text:
            res.values[f.id], res.tiers[f.id] = letter, "open"

    filled, unanswered, uploads = [], list(res.unanswered), []
    by_id = {f.id: f for f in fields}
    for fid, value in res.values.items():
        f = by_id[fid]
        if forms.fill_field(page, f, value):
            entry = {"label": f.label, "value": value if len(value) < 400 else value[:400] + "…", "tier": res.tiers[fid]}
            if fid in res.confidence:
                entry["confidence"] = res.confidence[fid]
            filled.append(entry)
            state.setdefault("learn", []).append((f, value, res.tiers[fid]))
        else:
            unanswered.append(
                {"id": fid, "label": f.label, "reason": f"couldn't fill (wanted: {value[:80]})", "required": f.required, "options": f.options}
            )

    step(f"typed {len(filled)} answer(s) into the page")
    for f in fields:
        kind = forms.kind_of_file(f)
        if kind == "resume" and state.get("resume_uploaded"):
            continue  # already attached before typing
        if kind == "resume" and forms.upload(page, f, str(resume_path)):
            uploads.append(f"resume ({key})")
        elif kind == "cover" and letter:
            with tempfile.TemporaryDirectory() as d:
                name = re.sub(r"[^A-Za-z0-9]+", "_", profile.full_name).strip("_") or "Cover"
                pdf = cover_letter.render_pdf(pw, letter, Path(d) / f"{name}_Cover_Letter.pdf")
                if forms.upload(page, f, str(pdf)):
                    uploads.append("cover letter")
        elif f.type == "file" and f.required and kind is None:
            unanswered.append({"id": f.id, "label": f.label, "reason": "file upload needs you", "required": True})
        elif f.type == "consent":
            unanswered.append({"id": f.id, "label": f.label, "reason": "checkbox/consent left for you", "required": f.required})
    if uploads:
        step(f"uploaded {', '.join(uploads)}")
    return filled, unanswered, uploads


SCHEMA = {"tailoring": False}  # set at run start: is migration 003 applied?


def check_schema() -> bool:
    try:
        sb().table("jobs").select("match_pct,resume_review").limit(1).execute()
        return True
    except Exception:
        return False


def _save_match(jid: int, info: dict):
    try:
        update(jid, resume_match=info, match_pct=info["pct"])
    except Exception:
        try:  # migration 003 not run yet (no match_pct column)
            update(jid, resume_match=info)
        except Exception as e:  # migration 002 not run yet either
            print(f"    resume match not saved: {type(e).__name__}: {str(e)[:100]}")


def _draft_tailoring(jid: int, job: dict, info: dict, profile_text: str):
    """Background: draft the tailored resume, then hand the job to Resume review (or back to the queue)."""
    try:
        result = tailor.propose(job, info, profile_text)
        if result["edits"]:
            update(jid, resume_edits=result, resume_review="pending",
                   status_reason=f"Tailored draft ready: {result['pct_before']:.0%} -> {result['pct_after']:.0%}, review it")
            print(f"    job {jid}: tailored draft ready ({result['pct_before']:.0%} -> {result['pct_after']:.0%}, "
                  f"{len(result['edits'])} edit(s))")
        else:  # nothing safe to change: fill with the best base resume on the next run
            update(jid, status="queued", resume_edits=result, resume_review="dismissed",
                   status_reason="Tailoring found no safe edits; will use the best base resume")
            print(f"    job {jid}: no safe tailoring edits - back in the queue with the base resume")
    except Exception as e:
        update(jid, status="queued", resume_review="dismissed",
               status_reason=f"Tailoring failed ({type(e).__name__}); will use the best base resume")
        print(f"    job {jid}: tailoring failed: {type(e).__name__}: {str(e)[:100]}")


def _choose_resume(job: dict, profile: Profile) -> tuple[str, Path, dict | None, str]:
    """-> (key, pdf path, match info, action) where action is 'fill' or 'tailor'."""
    built = job.get("resume_file") if job.get("resume_review") == "built" else None
    if built and (config.ROOT / built).exists():
        print(f"    resume: your tailored version ({built})")
        return "tailored", config.ROOT / built, None, "fill"
    info = None
    try:
        info = resume_select.evaluate(job)
    except Exception as e:
        print(f"    resume match skipped: {type(e).__name__}: {str(e)[:100]}")
    if not info:
        key, path = pick_resume(job["role"])
        return key, path, None, "fill"
    key = info["chosen"]
    scores = ", ".join(f"{k} {v['pct']:.0%}" for k, v in info["scores"].items())
    by = f"Jev {info['jev_confidence']:.2f}" if info.get("chosen_by") == "jev" else "score"
    action = "fill"
    if (info["pct"] < config.RESUME_MATCH_THRESHOLD and job.get("resume_review") != "dismissed" and SCHEMA["tailoring"]
            and info.get("tailorable", True)):  # PDF-only resumes can't be tailored: fill with them as they are
        info["addable"] = tailor.addable_terms(info, profile.text)
        action = "tailor" if info["addable"] else "fill"
    print(f"    resume: {key} ({info['pct']:.0%} match, chosen by {by}; {scores})"
          + (f" -> tailoring ({len(info['addable'])} missing skill(s) to add)" if action == "tailor" else ""))
    return key, config.RESUMES[key], info, action


def process(pw, ctx, job: dict, profile: Profile, bank: dict[str, str], known: list[dict] | None = None,
            pending_drafts: list | None = None) -> Page | None:
    """Fill one job. Returns the open page (ready for review), or None if it ended without a usable form
    (skipped, needs manual, failed, or paused for tailoring)."""
    if pending_drafts is None:
        pending_drafts = []
    jid, url, ats = job["id"], job["url"], job["ats"]
    posting_url, form_url = adapters.urls(ats, url)
    page = ctx.new_page()
    if ats != "workday":  # Workday: the person clicks every step; a DOM guard covers the runner's fills
        browser.install_guard(page)

    def give_up(status: str, reason: str):
        update(jid, status=status, status_reason=reason)
        print(f"    {status}: {reason[:110]}")
        try:
            page.close()
        except Exception:
            pass
        return None

    step = Steps()
    try:
        page.goto(posting_url, wait_until="domcontentloaded", timeout=45000)
        if ats == "workday":  # the posting is rendered client-side
            try:
                page.wait_for_selector('[data-automation-id="jobPostingDescription"]', timeout=15000)
            except Exception:
                pass
        page.wait_for_timeout(2500)
        text = page.inner_text("body")
        step("posting page loaded")
        active = active_screens(profile.standard_answers)
        reason = screen(text, active)
        if not reason:
            try:
                reason = jev_screen(text, jid, active)
            except Exception as e:  # Jev unreachable: patterns alone decide
                print(f"    Jev screen unavailable: {str(e)[:80]}")
        step("screened (sponsorship / citizenship / clearance)")
        if reason:
            return give_up("skipped", reason)
        job = {**job, "description": re.sub(r"\s+", " ", text)[:6000]}

        # Decide the resume before touching the form: under the threshold with honest additions available, the job
        # pauses in "tailoring" (no daily slot used) and is filled with the tailored PDF once Rishi builds it.
        key, resume_path, match_info, action = _choose_resume(job, profile)
        step("resume chosen")
        if match_info:
            _save_match(jid, match_info)
        if action == "tailor":
            update(jid, status="tailoring", resume_review=None,
                   status_reason=f"Best resume matches {match_info['pct']:.0%}: drafting a tailored version")
            pending_drafts.append(_BG.submit(_draft_tailoring, jid, job, match_info, profile.text))
            page.close()
            return None

        if ats == "workday":
            return _start_workday(page, job, profile, bank, known, key, resume_path, give_up)

        if form_url and form_url != posting_url:
            page.goto(form_url, wait_until="domcontentloaded", timeout=45000)
        elif not form_url and not adapters.click_through(page, ats):
            return give_up("needs_manual", "Couldn't find the Apply button")
        loaded = _wait_for_form(page)
        for _ in range(2):  # job boards sometimes answer "502 Bad Gateway / come back later": reload and try again
            if loaded or not re.search(r"\b50[234]\b|bad gateway|come back (a little )?later|temporarily unavailable",
                                       page.inner_text("body")[:600], re.I):
                break
            print("    the job board had a server error - reloading")
            page.wait_for_timeout(4000)
            page.reload(wait_until="domcontentloaded", timeout=45000)
            loaded = _wait_for_form(page)
        if not loaded:
            if re.search(r"\b50[234]\b|bad gateway|service unavailable|come back (a little )?later", page.inner_text("body")[:600], re.I):
                # the job board is down, not the posting: stays in the queue for the next run
                return give_up("queued", "The job board was down (server error): tried again next run")
            return give_up("needs_manual", "Application form didn't load")
        if _login_wall(page):
            return give_up("needs_manual", "Login/account required to apply")

        first = forms.extract(page)
        step(f"application form loaded ({len(first)} fields)")
        if sum(1 for f in first if f.type not in ("file", "consent")) < 3:
            return give_up("needs_manual", "Couldn't recognise the application form")

        resume = cover_letter.resume_text(str(resume_path))
        state: dict = {"known": known}
        done: set = set()
        filled, unanswered, uploads = [], [], []
        # The resume goes in before anything is typed: some forms (Greenhouse) read it and reset name / email / phone.
        for f in first:
            if forms.kind_of_file(f) == "resume" and forms.upload(page, f, str(resume_path)):
                uploads.append(f"resume ({key})")
                state["resume_uploaded"] = True
                page.wait_for_timeout(2500)  # let the form finish reading it
                step("resume uploaded")
                first = forms.extract(page)
                break
        # A form can reveal new fields once earlier ones are answered (e.g. race after Hispanic/Latino), so
        # re-read it after each pass and handle whatever is new.
        want_schools = len(profile.education)  # fill every education entry from the profile
        schools_seen = 0
        for pass_no in range(5):
            fields = first if pass_no == 0 else forms.extract(page)
            seen: Counter = Counter()
            new = []
            for f in fields:
                k = (norm(f.label), f.type)
                seen[k] += 1
                f.occ = seen[k]  # 2nd "School" field = 2nd education entry
                if (k, seen[k]) not in done:
                    done.add((k, seen[k]))
                    new.append(f)
                    if forms.is_school_field(f):
                        schools_seen += 1
            if new:
                calllog.note(f"[pass] {pass_no + 1}: {len(new)} new field(s)")
                f_, u_, up_ = _fill_pass(page, pw, new, state, profile, bank, job, resume, resume_path, key)
                filled += f_
                unanswered += u_
                uploads += up_
                page.wait_for_timeout(400)  # give conditional questions a moment to appear
            # the form shows one education block: press "Add another" until every entry has a block
            if 0 < schools_seen < want_schools and forms.click_add_education(page):
                page.wait_for_timeout(900)
                continue
            if not new:
                break

        step("form filled")
        answer_of = {norm(x["label"]): x["value"] for x in filled if x.get("value")}
        for f in forms.unfilled_required(page):
            v = answer_of.get(norm(f.label))
            if v and forms.fill_field(page, f, v):
                print(f"    re-typed {f.label[:60]!r} (the form had cleared it)")
        step("checked every required answer stuck")
        for p in state.get("letter_problems", []):
            unanswered.append({"id": "cover", "label": "Cover letter", "reason": p, "required": False})
        # a question flagged earlier but filled in a later pass is no longer outstanding
        filled_labels = {norm(f["label"]) for f in filled}
        unanswered = [u for u in unanswered if not ("couldn't fill" in u["reason"] and norm(u["label"]) in filled_labels)]

        needs = [u for u in unanswered if u.get("required")]
        if needs:  # answers you save on the dashboard are typed into this tab (see poll_review)
            _HELP[jid] = {"profile": profile, "bank": bank, "checked": 0.0}
            print("    needs your help: " + "; ".join(f"{u['label'][:60]} ({u.get('reason', '')[:40]})" for u in needs[:6]))
        _update_status(
            jid,
            fallback=_ONE_PAGE_FALLBACK,
            status="needs_help" if needs else "ready_for_review",
            status_reason=(f"{len(needs)} required question(s) need your answer: answer them on the dashboard (typed into the "
                           "open form for you, and saved for next time) or in the browser" if needs else None),
            resume_used=key,
            cover_letter=state.get("letter"),
            filled_fields={"fields": filled, "uploads": uploads, "captcha": _has_captcha(page)},
            unanswered=unanswered,
        )
        tiers = Counter(f["tier"] for f in filled)
        print(f"    ready: {len(filled)} filled ({', '.join(f'{v} {k}' for k, v in tiers.items())}), "
              f"{len(needs)} required left for you, uploads={uploads}")
        try:
            learned = known_mod.learn(state.get("learn", []), job.get("company"))
            if learned:
                print(f"    learned {learned} answer(s) for next time")
        except Exception as e:
            print(f"    learning skipped: {type(e).__name__}: {str(e)[:80]}")
        browser.release(page)
        return page
    except Exception as e:
        closed = bool(_CLOSED_ERR.search(str(e)))
        if closed and not browser_alive(ctx):
            update(jid, status="queued", status_reason="Browser was closed while filling")
            raise BrowserClosed() from e
        if closed:
            update(jid, status="queued", status_reason="Tab was closed while filling")
            print("    tab closed - put back in the queue")
            return None
        traceback.print_exc()
        update(jid, status="failed", status_reason=f"{type(e).__name__}: {str(e)[:300]}")
        try:
            page.close()
        except Exception:
            pass
        return None


# ---- Workday: page by page ------------------------------------------------------------------
def _start_workday(page, job, profile, bank, known, key, resume_path, give_up):
    """Apply -> the first Workday page. The person clicks every Save and Continue; each new page is filled when it
    appears (see workday.step). Returns the tracked application, or None."""
    jid = job["id"]
    reason = workday.begin(page, job)
    if reason == "already applied":
        update(jid, status="submitted", status_reason="Workday says you already applied to this job")
        print("    already applied (Workday) - marked submitted")
        page.close()
        return None
    if reason:
        return give_up("needs_manual", reason)
    t = workday.Tracked(job, page, profile, bank, known, resume_path, key, cover_letter.resume_text(str(resume_path)))
    _workday_step(t)
    return t


_STATUS_FALLBACK = {"needs_help": "submission_required", "submission_required": "ready_for_review"}
_ONE_PAGE_FALLBACK = {"needs_help": "ready_for_review"}  # one-page forms never use 'submission_required'


def _update_status(jid: int, fallback: dict | None = None, **fields):
    """Write the job row; on a database without migrations 005/006 fall back to a status it knows."""
    while True:
        try:
            return update(jid, **fields)
        except Exception as e:
            nxt = (fallback or _STATUS_FALLBACK).get(fields.get("status"))
            if "status_check" not in str(e) or not nxt:
                raise
            print(f"    note: run supabase/migrations/006_needs_help.sql (and 005) to get the '{fields['status']}' status")
            fields = {**fields, "status": nxt}


def _workday_step(t: "workday.Tracked") -> str:
    jid = t.job["id"]
    # the person clicks every Sign In / Save and Continue / Submit here, so the form-submit guard stays lifted
    # (workday.py blocks the Next buttons only while the runner is typing)
    browser.release(t.page)
    r = "moved"
    for _ in range(10):  # keep going while pages fill and continue without needing you
        r = workday.step(t, lambda **fields: _update_status(jid, **fields), reload_bank=load_bank)
        if r != "moved":
            break
    if r == "moved":
        r = "waiting"
    if t.learn:
        try:
            n = known_mod.learn(t.learn, t.job.get("company"))
            if n:
                print(f"    learned {n} answer(s) for next time")
        except Exception as e:
            print(f"    learning skipped: {type(e).__name__}: {str(e)[:80]}")
        t.learn.clear()
    if r == "submitted":
        update(jid, status="submitted", status_reason=None)
        print(f"    job {jid}: Workday submission detected - marked submitted")
    return r


def poll_workday(wd: dict[int, "workday.Tracked"]):
    """Fill each Workday application's next page once the person has clicked Save and Continue."""
    for jid, t in list(wd.items()):
        st = status_of(jid)
        if st not in ("submission_required", "needs_help", "ready_for_review", "filling"):  # marked on the dashboard
            del wd[jid]
            continue
        try:
            r = _workday_step(t)
        except Exception as e:
            if _CLOSED_ERR.search(str(e)):
                r = "closed"
            else:
                print(f"    workday job {jid}: {type(e).__name__}: {str(e)[:120]}")
                continue
        if r == "closed":
            update(jid, status="queued", status_reason="Workday tab closed before submitting")
            print(f"    job {jid}: Workday tab closed without submitting - back in the queue")
            del wd[jid]
        elif r == "submitted":
            del wd[jid]


# ---- watching the review tabs ---------------------------------------------------------------
_HELP: dict[int, dict] = {}  # open forms waiting for your answers: job id -> {profile, bank, checked}


def _help_tick(jid: int, page: Page):
    """A form that needs your help: type in answers you've saved on the dashboard since, and once no required question
    is empty (answered here or in the browser) mark it ready for review."""
    h = _HELP.get(jid)
    if not h or time.time() - h["checked"] < 5:
        return
    h["checked"] = time.time()
    bank = load_bank()
    typed = 0
    if bank != h["bank"]:
        h["bank"] = bank
        for f in forms.extract(page):
            if f.type == "file" or forms.read_value(page, f).strip():
                continue
            val = direct_answer(f, h["profile"], bank)
            if val and forms.fill_field(page, f, val):
                typed += 1
        if typed:
            print(f"    job {jid}: typed in {typed} answer(s) you gave on the dashboard")
    left = forms.unfilled_required(page)
    if not left:
        _HELP.pop(jid, None)
        update(jid, status="ready_for_review", status_reason=None, unanswered=[])
        print(f"    job {jid}: every required question is answered now - ready for review")
    elif typed:
        update(jid, unanswered=[{"id": f.id, "label": f.label, "required": True, "options": f.options,
                                 "reason": "still empty"} for f in left])


def poll_review(tracked: dict[int, Page]):
    for jid, page in list(tracked.items()):
        try:
            closed = page.is_closed()
        except Exception:
            closed = True
        if closed:
            if status_of(jid) in ("ready_for_review", "needs_help"):
                update(jid, status="queued", status_reason="Closed without submitting")
                print(f"    job {jid}: tab closed without submitting - back in the queue")
            _HELP.pop(jid, None)
            del tracked[jid]
            continue
        st = status_of(jid)
        if st not in ("ready_for_review", "needs_help"):  # you already marked it on the dashboard
            _HELP.pop(jid, None)
            del tracked[jid]
            continue
        if st == "needs_help":
            try:
                _help_tick(jid, page)
            except Exception as e:
                if _CLOSED_ERR.search(str(e)):
                    continue
                print(f"    job {jid}: help check failed: {type(e).__name__}: {str(e)[:100]}")
        try:
            text = page.inner_text("body", timeout=1500)
        except Exception:
            continue
        if CONFIRM.search(text[:4000]):
            update(jid, status="submitted", status_reason=None)
            print(f"    job {jid}: submission detected - marked submitted")
            del tracked[jid]


def claim_resumed(paused: set[int]) -> list[dict]:
    """Jobs this run paused for tailoring that you've since decided on (Build, or Use base resume): they're queued
    again, so claim them for this run. Jobs you skipped or marked some other way are dropped."""
    if not paused:
        return []
    rows = sb().table("jobs").select("*").in_("id", sorted(paused)).execute().data
    out = []
    for r in rows:
        if r["status"] == "tailoring":
            continue  # still waiting for you in Resume review
        paused.discard(r["id"])
        if r["status"] != "queued":
            continue
        claim = sb().table("jobs").update({"status": "filling", "status_reason": None, "attempts": r["attempts"] + 1}).eq(
            "id", r["id"]).eq("status", "queued").execute()
        if claim.data:
            out.append(r)
    return out


def review_loop(ctx: BrowserContext, tracked: dict[int, Page], wd: dict[int, "workday.Tracked"],
                paused: set[int] | None = None, fill_resumed=None):
    paused = paused if paused is not None else set()
    if paused:
        print(f"\n{len(paused)} job(s) paused for a tailored resume. Build it (or choose Use base resume) in Resume review "
              "and this run fills them right away. Press Stop on the dashboard to end without them.")
    if tracked or wd:
        print(f"\n{len(tracked) + len(wd)} tab(s) open for you. NOTHING has been submitted. Submit each yourself;")
        if wd:
            print(f"{len(wd)} Workday application(s): each page is filled and continued for you; questions it can't "
                  "answer show up as 'Needs your help' on the dashboard.")
        print("submissions are detected automatically. Close the browser or press Stop on the dashboard to end.")
    while (tracked or wd or paused) and not STOP.exists() and browser_alive(ctx):
        poll_review(tracked)
        poll_workday(wd)
        if paused and fill_resumed:
            for job in claim_resumed(paused):
                fill_resumed(job)
        time.sleep(2)
    poll_review(tracked)


# ---- entry point ----------------------------------------------------------------------------
def run(limit: int = 20, ids: list[int] | None = None):
    acquire_lock()
    tracked: dict[int, Page] = {}
    wd: dict[int, workday.Tracked] = {}  # Workday applications waiting for you to click Save and Continue
    try:
        require_multi_user()
        cfg = config.activate(user_id())
        print(f"Signed in as {cfg.email or cfg.name} | files: {cfg.folder.relative_to(ROOT).as_posix()}/ | "
              f"resumes: {', '.join(r.label for r in cfg.resumes) or 'none'}")
        if not cfg.resumes:
            raise SystemExit("No resume yet: add one on the dashboard's My files tab.")
        # The dashboard's Profile tab is the source of truth: refresh profile.md from it before every run.
        synced = profile_md.sync_from_db(config.PROFILE_PATH)
        if synced == "missing":
            raise SystemExit("No profile yet: finish the Profile tab in the dashboard first.")
        print("Profile: " + ("latest from the dashboard" if synced == "synced" else f"local file {config.PROFILE_PATH.name}"))
        unparked = unpark_workday()
        if unparked:
            print(f"Workday is now filled page by page: {unparked} Workday job(s) moved back to the queue")
        stale = reset_stale()
        if stale:
            print(f"Reset {stale} job(s) left over from an earlier run")
        profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
        bank = load_bank()
        SCHEMA["tailoring"] = check_schema()
        if not SCHEMA["tailoring"]:
            print("Note: run supabase/migrations/002 and 003 to enable match % and the Tailoring pause; filling as before.")
        known = known_mod.load_known()
        print(f"Known answers: {len(known)} (saved + learned)")
        pending_drafts: list = []
        run_row = sb().table("runs").insert({"owner_id": user_id()}).execute().data[0]
        pending = list(ids) if ids else None
        dupes = dedupe_jobs()
        if dupes:
            print(f"Skipped {dupes} duplicate listing(s) of postings already handled")
        total = len(ids) if ids else min(limit, queued_count())
        print(f"Plan: {total} job(s)")
        counts: dict[str, int] = {}
        attempted: set[int] = set()
        paused: set[int] = set()  # jobs this run paused for tailoring: filled as soon as you decide in Resume review
        with sync_playwright() as pw:
            ctx = browser.launch(pw)
            try:
                # `total` counts forms actually filled: dead/skipped postings don't use up a slot, and a job already
                # tried in this run (e.g. a tab you closed) is never picked a second time.
                filled_n = 0

                def fill_one(job: dict, label: str) -> bool:
                    """Fill one job; True if a form is now open for you."""
                    db_fresh()  # long runs outlive one access token
                    print(f"{label} {job['company']} - {job['role']} ({job['ats']})")
                    update(job["id"], run_id=run_row["id"])
                    t0, c0, j0 = time.time(), llm.usage.seconds, llm.usage.jev_seconds
                    page = process(pw, ctx, job, profile, bank, known, pending_drafts)
                    took = time.time() - t0
                    chat, jv = llm.usage.seconds - c0, llm.usage.jev_seconds - j0
                    print(f"    timing: {took:.0f}s total | chat models {chat:.0f}s | Jev {jv:.1f}s | "
                          f"browser + other {max(0, took - chat - jv):.0f}s"
                          + ("  (chat calls overlapped)" if chat + jv > took else ""))
                    st = status_of(job["id"]) or "?"
                    counts[st] = counts.get(st, 0) + 1
                    if st == "tailoring":
                        paused.add(job["id"])  # comes back into this run once you decide in Resume review
                    if isinstance(page, workday.Tracked):
                        wd[job["id"]] = page
                    elif page:
                        tracked[job["id"]] = page
                    return bool(page)

                def fill_resumed(job: dict):
                    print(f"\n[resumed] {'tailored resume built' if job.get('resume_review') == 'built' else 'using the base resume'}")
                    fill_one(job, "[resumed]")

                while filled_n < total and len(attempted) < max(total * 4, 10):
                    if STOP.exists():
                        print("Stop requested")
                        break
                    resumed = claim_resumed(paused)  # jobs you just approved come first
                    for job in resumed:
                        fill_resumed(job)
                    if resumed:
                        continue
                    job = next_job(pending, attempted)
                    if not job:
                        break
                    attempted.add(job["id"])
                    if fill_one(job, f"[{filled_n + 1}/{total}]"):
                        filled_n += 1
                    poll_review(tracked)
                    poll_workday(wd)
                if pending_drafts:
                    print(f"Finishing {len(pending_drafts)} tailored resume draft(s)...")
                    for fut in pending_drafts:
                        fut.result()
                print("\nDone:", counts)
                print(calllog.summary())
                print(f"LLM usage: {llm.usage.calls} chat calls, {llm.usage.prompt_tokens} in / {llm.usage.completion_tokens} "
                      f"out tokens, {llm.usage.retries} retries, {llm.usage.invalid_json} invalid-JSON | "
                      f"Jev: {llm.usage.jev_calls} calls, ${llm.usage.jev_cost:.4f}")
                sb().table("runs").update(
                    {"finished_at": datetime.now(timezone.utc).isoformat(), "counts": {**counts, "llm": vars(llm.usage)}}
                ).eq("id", run_row["id"]).execute()
                review_loop(ctx, tracked, wd, paused, fill_resumed)
            except BrowserClosed:
                print("Browser was closed - stopping.")
            finally:
                for jid in list(tracked) + list(wd):  # anything still waiting has no tab once we exit
                    if status_of(jid) in ("ready_for_review", "submission_required", "needs_help"):
                        update(jid, status="queued", status_reason="Runner stopped before it was submitted")
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        release_lock()
