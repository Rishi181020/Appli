import argparse
import collections
import sys

from . import config
from .profile import load_profile
from .resume_picker import pick_resume
from .sheet import read_table


def _activate():
    """Command-line tools act as whoever last signed in on the dashboard of this computer."""
    from .db import user_id

    return config.activate(user_id())


def cmd_import(args):
    """Same as the dashboard's Add jobs > Upload: a CSV or xlsx, columns recognised from the headers."""
    from pathlib import Path

    f = Path(args.file)
    parsed = read_table(f.read_bytes(), f.name, sheet=args.sheet)
    rows = parsed["rows"]
    print(f"Read {len(rows)} unique postings from {f.name} (columns: {parsed['mapping']})")
    for p in parsed["problems"]:
        print("  !", p)
    print("Tiers:", dict(sorted(collections.Counter(r.fit_tier for r in rows).items(), key=lambda kv: str(kv[0]))))
    print("ATS:", dict(collections.Counter(r.ats for r in rows).most_common()))
    print("No link:", sum(1 for r in rows if not r.url))
    if args.dry_run or parsed["problems"]:
        return
    _activate()
    from .db import dedupe_jobs, import_jobs, park_unsupported

    print("Resumes:", dict(collections.Counter(pick_resume(r.role)[0] for r in rows)))
    print(import_jobs(rows))
    print("Duplicate listings skipped:", dedupe_jobs())
    print("Parked unsupported-ATS rows as needs_manual:", park_unsupported())


def cmd_check(_args):
    _activate()
    p = load_profile(config.PROFILE_PATH)
    print(f"Profile: {p.full_name} | {p.email} | {p.phone} | {p.city} | {p.state}, {p.country}")
    print("Standard answers:", p.standard_answers)
    for key, path in config.RESUMES.items():
        print(f"Resume {key}: {'OK' if path.exists() else 'MISSING'} {path.name}")


def cmd_ping(_args):
    """Verify Supabase and OpenRouter credentials without printing any secret."""
    import os

    from . import llm
    from .db import sb, user_id

    for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "OPENROUTER_API_KEY", "LLM_MODEL_FAST", "LLM_MODEL_WRITE",
                 "JEV_MODEL"):
        print(f"{name}: {'set' if os.getenv(name) else 'MISSING'}")
    try:
        uid = user_id()
        cfg = config.activate(uid, required=False)
        print(f"Supabase: signed in (runner session){' | files: ' + cfg.folder.name if cfg else ' | no files on this computer yet'}")
        n = sb().table("jobs").select("id", count="exact").limit(1).execute().count
        row = sb().table("runs").insert({"owner_id": uid}).execute().data[0]  # write test as the signed-in user
        sb().table("runs").delete().eq("id", row["id"]).execute()
        prof = sb().table("profiles").select("updated_at").execute().data
        print(f"Supabase: OK, write access confirmed ({n} of your jobs; profile "
              f"{'saved ' + prof[0]['updated_at'][:10] if prof else 'NOT set up yet: finish it in the dashboard'})")
    except SystemExit as e:
        print(f"Supabase: FAILED - {e}")
    except Exception as e:
        print(f"Supabase: FAILED - {type(e).__name__}: {str(e)[:200]} (has migration 004 been run?)")
    for kind in ("fast", "write"):
        model = config.model(kind)
        try:
            out = llm.chat_text("Reply with the single word: ok", "ping", model=model, max_tokens=400, purpose=f"ping ({kind})")
            print(f"OpenRouter {kind} ({model}): OK -> {out[:30]!r}")
        except Exception as e:
            print(f"OpenRouter {kind} ({model}): FAILED - {type(e).__name__}: {str(e)[:200]}")
    import time

    from . import jev

    jev_name = os.getenv("JEV_MODEL") or "JEV_MODEL not set in .env"
    t = time.time()
    try:
        a = jev.decide("The candidate is authorized to work in the US.",
                       {"q": jev.choice("Is the candidate authorized to work in the US?", {"yes": "Yes", "no": "No"})})
        print(f"Jev ({jev_name}): OK -> {a['q'].get('choice')} in {time.time() - t:.1f}s, ${llm.usage.jev_cost:.6f}")
    except Exception as e:
        print(f"Jev ({jev_name}): FAILED - {str(e)[:200]}")
    from .run import check_schema

    print("Database migrations 002 + 003:", "applied" if check_schema() else "NOT applied (see supabase/migrations)")


def cmd_rescore(args):
    """Recompute resume match % for existing jobs with the current ATS scoring. Never fills or changes status."""
    from playwright.sync_api import sync_playwright

    from . import calllog
    from .ats import adapters
    from .db import all_jobs, sb
    from .resume import select

    _activate()
    if args.ids:
        ids = {int(x) for x in args.ids.split(",")}
        jobs = [j for j in all_jobs("id,company,role,ats,url,status,match_pct") if j["id"] in ids]
    else:
        wanted = set(args.status.split(","))
        jobs = [j for j in all_jobs("id,company,role,ats,url,status,match_pct") if j["status"] in wanted and j["url"]]
    jobs = jobs[: args.limit]
    print(f"Rescoring {len(jobs)} job(s){' (dry run: nothing saved)' if args.dry_run else ''}")
    rows = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        for j in jobs:
            page = b.new_page()
            try:
                page.goto(adapters.urls(j["ats"], j["url"])[0], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2000)
                job = {**j, "description": " ".join(page.inner_text("body").split())[:6000]}
            except Exception as e:
                print(f"  {j['company']}: couldn't load posting ({type(e).__name__})")
                page.close()
                continue
            page.close()
            print(f"- {j['company']} - {j['role']}")
            info = select.evaluate(job)
            if not info:
                print("    no usable requirements found")
                continue
            old = j.get("match_pct")
            rows.append((j["company"], old, info["pct"], info["exact_pct"]))
            print(f"    {('–' if old is None else f'{old:.0%}')} -> {info['pct']:.0%} (exact wording {info['exact_pct']:.0%}), "
                  f"resume {info['chosen']} | shown under another name: {', '.join(info['equivalent']) or '-'} | "
                  f"missing: {', '.join(t for t in info['missing'] if t not in info['equivalent']) or '-'}")
            if not args.dry_run:
                sb().table("jobs").update({"resume_match": info, "match_pct": info["pct"]}).eq("id", j["id"]).execute()
        b.close()
    if rows:
        print("\nBefore -> after:")
        for c, old, new, exact in rows:
            print(f"  {c[:22]:22} {('–' if old is None else f'{old:.0%}'):>5} -> {new:4.0%}  (exact {exact:.0%})")
    print(calllog.summary())


def cmd_screen(args):
    """Read each queued posting (no form, no account) and skip the ones you can't take: no sponsorship, US citizens
    only, clearance, closed. Patterns first, then Jev. Nothing else changes."""
    import re

    from playwright.sync_api import sync_playwright

    from .ats import adapters
    from .db import sb, unpark_workday
    from .screening import active_screens, jev_screen, screen

    _activate()
    unpark_workday()  # Workday jobs parked before Workday was supported
    profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
    active = active_screens(profile.standard_answers)
    q = sb().table("jobs").select("id,company,role,ats,url").eq("status", "queued")
    if args.ats != "all":
        q = q.eq("ats", args.ats)
    jobs = [j for j in q.order("fit_tier").order("id").limit(args.limit).execute().data if j["url"]]
    print(f"Screening {len(jobs)} queued {args.ats} posting(s) for: {', '.join(sorted(active)) or 'closed postings only'}")
    skipped = 0
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        for i, j in enumerate(jobs, 1):
            print(f"[{i}/{len(jobs)}] {j['company']} - {j['role']}", flush=True)
            page = b.new_page()
            try:
                page.goto(adapters.urls(j["ats"], j["url"])[0], wait_until="domcontentloaded", timeout=45000)
                if j["ats"] == "workday":
                    try:
                        page.wait_for_selector('[data-automation-id="jobPostingDescription"]', timeout=15000)
                    except Exception:
                        pass
                page.wait_for_timeout(2000)
                text = re.sub(r"\s+", " ", page.inner_text("body"))
            except Exception as e:
                print(f"    couldn't load ({type(e).__name__}): left in the queue")
                page.close()
                continue
            page.close()
            reason = screen(text, active)
            if not reason:
                try:
                    reason = jev_screen(text, j["id"], active)
                except Exception as e:
                    print(f"    Jev unavailable: {str(e)[:80]}")
            if reason:
                skipped += 1
                print(f"    skipped: {reason[:120]}")
                if not args.dry_run:
                    sb().table("jobs").update({"status": "skipped", "status_reason": reason}).eq("id", j["id"]).eq(
                        "status", "queued").execute()
        b.close()
    print(f"Done: {skipped} of {len(jobs)} skipped{' (dry run: nothing saved)' if args.dry_run else ''}")


def cmd_find(_args):
    """Search the job boards and new-grad/intern lists with your Find jobs preferences; results go to Find jobs."""
    from . import jobsearch

    _activate()
    jobsearch.search(lambda s: print(s, flush=True))


def cmd_learn(_args):
    """Seed learned answers from forms already filled (factual option questions only)."""
    from . import known
    from .answering import Field
    from .db import all_jobs

    total = 0
    for j in all_jobs("id,company,filled_fields,unanswered"):
        items = []
        for f in (j.get("filled_fields") or {}).get("fields", []):
            if f.get("tier") in ("jev", "choice"):
                # options aren't stored with old fills, so treat the answer itself as the only known option
                items.append((Field("x", f["label"], "select", [f["value"]]), f["value"], f["tier"]))
        total += known.learn(items, j.get("company"))
    print(f"Learned {total} answer(s) from past forms")


def cmd_run(args):
    from .run import run

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    run(limit=args.limit, ids=ids)


def cmd_serve(args):
    from .server import serve

    serve(open_browser=not args.no_browser)


def cmd_resume_check(_args):
    """Parse and compile each base resume's LaTeX: units found, pages, and that it matches the PDF."""
    from io import BytesIO

    from pypdf import PdfReader

    from .resume import tex

    _activate()
    for key, path in config.RESUME_TEX.items():
        try:
            src = path.read_text(encoding="utf-8")
        except OSError:
            print(f"{key}: MISSING {path}")
            continue
        units = tex.parse(src)
        comp = tex.compile_tex(src)
        line = f"{key}: {sum(u.kind == 'bullet' for u in units)} bullets, {sum(u.kind == 'skill' for u in units)} skill lines, "
        if comp.ok:
            ref = PdfReader(str(config.RESUMES[key])).pages[0].extract_text() if config.RESUMES[key].exists() else ""
            new = PdfReader(BytesIO(comp.pdf)).pages[0].extract_text()
            import difflib

            line += f"compiles to {comp.pages} page(s), text {difflib.SequenceMatcher(None, ref, new).ratio():.0%} identical to the PDF"
        else:
            line += f"COMPILE FAILED: {comp.log[:200]}"
        print(line)


def cmd_tailor(args):
    """Dry run of resume matching + tailoring for one job (no form is touched)."""
    from playwright.sync_api import sync_playwright

    from .ats import adapters
    from .db import sb
    from .resume import build, select, tailor

    _activate()
    job = sb().table("jobs").select("*").eq("id", args.id).execute().data[0]
    posting_url = adapters.urls(job["ats"], job["url"])[0]
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        page = b.new_page()
        page.goto(posting_url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        job["description"] = " ".join(page.inner_text("body").split())[:6000]
        b.close()
    print(f"{job['company']} - {job['role']}  ({len(job['description'])} chars of posting)")
    info = select.evaluate(job)
    if not info:
        print("No usable match (posting text too short or no terms found).")
        return
    print("terms:", ", ".join(("*" if t["required"] else "") + t["term"] for t in info["terms"]))
    for k, s in info["scores"].items():
        print(f"  {k:4} {s['pct']:.0%}  missing: {', '.join(s['missing']) or '-'}")
    print(f"best: {info['chosen']} at {info['pct']:.0%} (threshold {config.RESUME_MATCH_THRESHOLD:.0%})")
    if info["pct"] >= config.RESUME_MATCH_THRESHOLD and not args.force:
        print("At or above the threshold: no tailoring needed. (--force to draft one anyway)")
        return
    profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
    result = tailor.propose(job, info, profile.text)
    print(f"\ntailored match: {result['pct_before']:.0%} -> {result['pct_after']:.0%}")
    for e in result["edits"]:
        print(f"  [{e['id']}] {e['context'][:40]}: + {', '.join(e['added_words']) or '-'} | - {', '.join(e['removed_words']) or '-'}")
        print(f"        before: {e['before'][:110]}\n        after:  {e['after'][:110]}\n        evidence: {e['evidence'][:80]}")
    print("gaps (not on any resume, NOT added):", ", ".join(result["gaps"]) or "-")
    for p in result["problems"]:
        print("  problem:", p.get("kind"), "|", p.get("detail") or "; ".join(p.get("reasons", p.get("terms", []))))
    if args.build and result["edits"]:
        job["resume_edits"], job["resume_match"] = result, info
        out = build.build(job, [e["id"] for e in result["edits"]])
        print("\nbuild:", {k: v for k, v in out.items() if k not in ("added_words", "removed_words")})


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="runner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_imp = sub.add_parser("import", help="Add jobs from a CSV/xlsx file (the dashboard's Add jobs does the same)")
    p_imp.add_argument("file")
    p_imp.add_argument("--sheet", help="xlsx sheet name (default: the first)")
    p_imp.add_argument("--dry-run", action="store_true", help="Parse and summarize only")
    p_imp.set_defaults(fn=cmd_import)
    sub.add_parser("check", help="Verify profile and resume files").set_defaults(fn=cmd_check)
    sub.add_parser("ping", help="Test Supabase and OpenRouter credentials").set_defaults(fn=cmd_ping)
    p_run = sub.add_parser("run", help="Fill the next batch of applications (stops before Submit)")
    p_run.add_argument("--limit", type=int, default=20)
    p_run.add_argument("--ids", help="Comma-separated job ids to run instead of the queue (e.g. 3,15,24)")
    p_run.set_defaults(fn=cmd_run)
    p_srv = sub.add_parser("serve", help="Start the dashboard + Run button server (http://localhost:8765)")
    p_srv.add_argument("--no-browser", action="store_true")
    p_srv.set_defaults(fn=cmd_serve)
    p_sc = sub.add_parser("screen", help="Skip queued postings you can't take (sponsorship / citizenship / clearance)")
    p_sc.add_argument("--ats", default="workday", help="workday (default), greenhouse, ... or all")
    p_sc.add_argument("--limit", type=int, default=1000)
    p_sc.add_argument("--dry-run", action="store_true")
    p_sc.set_defaults(fn=cmd_screen)
    sub.add_parser("find", help="Search for relevant jobs (results appear on the dashboard's Find jobs tab)").set_defaults(
        fn=cmd_find)
    sub.add_parser("learn", help="Seed learned answers from forms already filled").set_defaults(fn=cmd_learn)
    p_rs = sub.add_parser("rescore", help="Recompute match % for existing jobs (no filling, no status change)")
    p_rs.add_argument("--status", default="ready_for_review,queued,tailoring", help="Comma-separated statuses to rescore")
    p_rs.add_argument("--ids", help="Comma-separated job ids instead of --status")
    p_rs.add_argument("--limit", type=int, default=100)
    p_rs.add_argument("--dry-run", action="store_true", help="Print the new scores without saving them")
    p_rs.set_defaults(fn=cmd_rescore)
    sub.add_parser("resume-check", help="Compile your LaTeX resumes and report editable lines").set_defaults(fn=cmd_resume_check)
    p_tl = sub.add_parser("tailor", help="Dry run: score the resumes for one job and draft a tailoring")
    p_tl.add_argument("--id", type=int, required=True)
    p_tl.add_argument("--force", action="store_true", help="Draft a tailoring even if the match is already >= threshold")
    p_tl.add_argument("--build", action="store_true", help="Also build the PDF with every proposed edit")
    p_tl.set_defaults(fn=cmd_tailor)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
