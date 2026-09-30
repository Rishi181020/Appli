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
from .answering import answer_fields, direct_answer, norm
from .ats import SUPPORTED, adapters
from .config import ROOT
from .db import dedupe_jobs, require_multi_user, sb, user_id
from .db import fresh as db_fresh
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
        .in_("status", ["filling", "ready_for_review"])
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
            if rows[0]["status"] in ("submitted", "filling"):  # never refill an application that's already sent
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
    need_options = [f for f in fields if f.type == "combobox" and not f.options and direct_answer(f, profile, bank) is None]
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

        if form_url and form_url != posting_url:
            page.goto(form_url, wait_until="domcontentloaded", timeout=45000)
        elif not form_url and not adapters.click_through(page, ats):
            return give_up("needs_manual", "Couldn't find the Apply button")
        if not _wait_for_form(page):
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
        for p in state.get("letter_problems", []):
            unanswered.append({"id": "cover", "label": "Cover letter", "reason": p, "required": False})
        # a question flagged earlier but filled in a later pass is no longer outstanding
        filled_labels = {norm(f["label"]) for f in filled}
        unanswered = [u for u in unanswered if not ("couldn't fill" in u["reason"] and norm(u["label"]) in filled_labels)]

        needs = [u for u in unanswered if u.get("required")]
        update(
            jid,
            status="ready_for_review",
            status_reason=(f"{len(needs)} required field(s) need you" if needs else None),
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


# ---- watching the review tabs ---------------------------------------------------------------
def poll_review(tracked: dict[int, Page]):
    for jid, page in list(tracked.items()):
        try:
            closed = page.is_closed()
        except Exception:
            closed = True
        if closed:
            if status_of(jid) == "ready_for_review":
                update(jid, status="queued", status_reason="Closed without submitting")
                print(f"    job {jid}: tab closed without submitting - back in the queue")
            del tracked[jid]
            continue
        if status_of(jid) != "ready_for_review":  # you already marked it on the dashboard
            del tracked[jid]
            continue
        try:
            text = page.inner_text("body", timeout=1500)
        except Exception:
            continue
        if CONFIRM.search(text[:4000]):
            update(jid, status="submitted", status_reason=None)
            print(f"    job {jid}: submission detected - marked submitted")
            del tracked[jid]


def review_loop(ctx: BrowserContext, tracked: dict[int, Page]):
    if tracked:
        print(f"\n{len(tracked)} tab(s) open for review. NOTHING has been submitted. Submit each yourself;")
        print("submissions are detected automatically. Close the browser or press Stop on the dashboard to end.")
    while tracked and not STOP.exists() and browser_alive(ctx):
        poll_review(tracked)
        time.sleep(2)
    poll_review(tracked)


# ---- entry point ----------------------------------------------------------------------------
def run(limit: int = 20, ids: list[int] | None = None):
    acquire_lock()
    tracked: dict[int, Page] = {}
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
        with sync_playwright() as pw:
            ctx = browser.launch(pw)
            try:
                # `total` counts forms actually filled: dead/skipped postings don't use up a slot, and a job already
                # tried in this run (e.g. a tab you closed) is never picked a second time.
                filled_n = 0
                while filled_n < total and len(attempted) < max(total * 4, 10):
                    if STOP.exists():
                        print("Stop requested")
                        break
                    job = next_job(pending, attempted)
                    if not job:
                        break
                    attempted.add(job["id"])
                    db_fresh()  # long runs outlive one access token
                    print(f"[{filled_n + 1}/{total}] {job['company']} - {job['role']} ({job['ats']})")
                    update(job["id"], run_id=run_row["id"])
                    t0, c0, j0 = time.time(), llm.usage.seconds, llm.usage.jev_seconds
                    page = process(pw, ctx, job, profile, bank, known, pending_drafts)
                    took = time.time() - t0
                    chat, jv = llm.usage.seconds - c0, llm.usage.jev_seconds - j0
                    print(f"    timing: {took:.0f}s total | chat models {chat:.0f}s | Jev {jv:.1f}s | "
                          f"browser + other {max(0, took - chat - jv):.0f}s"
                          + ("  (chat calls overlapped)" if chat + jv > took else ""))
                    if page:
                        tracked[job["id"]] = page
                        filled_n += 1
                    st = status_of(job["id"]) or "?"
                    counts[st] = counts.get(st, 0) + 1
                    poll_review(tracked)
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
                review_loop(ctx, tracked)
            except BrowserClosed:
                print("Browser was closed - stopping.")
            finally:
                for jid in list(tracked):  # anything still waiting has no tab once we exit
                    if status_of(jid) == "ready_for_review":
                        update(jid, status="queued", status_reason="Runner stopped before it was submitted")
                try:
                    ctx.close()
                except Exception:
                    pass
    finally:
        release_lock()
