"""Local app server: serves the dashboard and does everything that needs this computer (files, runs).

Bound to 127.0.0.1 only. Every API call must come from the dashboard's own origin (checked) and carry the signed-in
person's Supabase access token, which is verified with Supabase; each request then acts as that person (their
folder in users/, their rows in the database). The runner itself never submits an application.
"""
import base64
import binascii
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, db, userfiles
from .config import ROOT
from .run import LOCK, OUT, STOP, running_pid

PORT = int(os.getenv("APPLI_PORT", "8765"))
DIST = ROOT / "dashboard" / "dist"
LOG = OUT / "run.log"
RUN_OWNER = OUT / "run.owner"
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}", "localhost:5173", "127.0.0.1:5173"}
MAX_BODY = 32 * 1024 * 1024  # base64 uploads: two 10 MB files at most

_proc: subprocess.Popen | None = None
_lock = threading.Lock()
_files_lock = threading.Lock()  # config.activate() is process-wide: one person's file request at a time
_tokens: dict[str, tuple[float, dict]] = {}  # access token -> (checked at, {id, email})


def _tail(n: int = 60) -> list[str]:
    try:
        return LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _progress(lines: list[str]) -> dict:
    """Parse the runner's log into {current, total, label}."""
    total = current = 0
    label = ""
    for ln in lines:
        m = re.match(r"Plan: (\d+) job", ln)
        if m:
            total = int(m.group(1))
        m = re.match(r"\[(\d+)/(\d+)\] (.+)", ln)
        if m:
            current, total, label = int(m.group(1)), int(m.group(2)), m.group(3)
    return {"current": current, "total": total, "label": label}


def _run_owner() -> str | None:
    try:
        return RUN_OWNER.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def status(owner_id: str) -> dict:
    pid = running_pid()
    if _run_owner() not in (None, owner_id):  # someone else on this computer ran last: don't show their log
        return {"running": pid is not None, "pid": None, "stopping": False, "other_user": pid is not None,
                "progress": {"current": 0, "total": 0, "label": ""}, "log": []}
    log = LOG.read_text(encoding="utf-8", errors="replace").splitlines() if LOG.exists() else []
    return {
        "running": pid is not None,
        "pid": pid,
        "stopping": STOP.exists() and pid is not None,
        "progress": _progress(log),
        "log": log[-40:],
    }


def start(owner_id: str, limit: int, ids: list[int] | None = None) -> tuple[int, dict]:
    """Start a run for this person: the next `limit` jobs from the queue, or exactly `ids` in the order given."""
    global _proc
    with _lock:
        if running_pid():
            return 409, {"error": "A run is already active. Stop it or close its browser first."}
        if not db.has_session(owner_id):
            return 409, {"error": "The runner isn't connected to your account: sign out and sign in again.",
                         "reconnect": True}
        if config.activate(owner_id, required=False) is None:
            return 409, {"error": "Finish the setup screens first (your resumes and profile)."}
        OUT.mkdir(exist_ok=True)
        STOP.unlink(missing_ok=True)
        RUN_OWNER.write_text(owner_id, encoding="utf-8")
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "APPLI_OWNER": owner_id}
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        args = ["--ids", ",".join(str(i) for i in ids)] if ids else ["--limit", str(limit)]
        _proc = subprocess.Popen(
            [sys.executable, "-m", "runner", "run", *args],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
            creationflags=flags, text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        threading.Thread(target=_tee, args=(_proc,), daemon=True).start()
        what = f"{len(ids)} selected job(s): {', '.join(map(str, ids))}" if ids else f"{limit} applications"
        print(f"\n=== Run started from the dashboard ({what}) ===", flush=True)
        return 200, {"started": True, "pid": _proc.pid}


def _tee(proc: subprocess.Popen):
    """Copy the runner's output to this terminal (live) and to out/run.log (for the dashboard's Activity panel)."""
    with open(LOG, "w", encoding="utf-8") as logf:
        for line in proc.stdout:
            logf.write(line)
            logf.flush()
            print(line, end="", flush=True)
    print("=== Run finished ===", flush=True)


def _b64(value) -> bytes | None:
    """Uploaded file from the dashboard: base64, optionally as a data: URL."""
    if not value:
        return None
    s = str(value)
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        return base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        raise userfiles.FileError("Couldn't read the uploaded file") from None


def health() -> list[dict]:
    """What this computer needs, each with a fix. Cheap checks only; OpenRouter/Jev send one tiny request each."""
    out = []

    def item(ok: bool, what: str, fix: str = ""):
        out.append({"ok": ok, "what": what, "fix": "" if ok else fix})

    item(bool(os.getenv("OPENROUTER_API_KEY")), "OpenRouter API key in .env", "Add OPENROUTER_API_KEY to .env and restart start.bat")
    from . import llm

    for kind in ("fast", "write"):
        try:
            llm.chat_text("Reply with the single word: ok", "ping", model=config.model(kind), max_tokens=400,
                          purpose=f"health ({kind})")
            item(True, f"{kind.title()} model {config.model(kind)}")
        except BaseException as e:  # SystemExit when the key is missing
            item(False, f"{kind.title()} model {config.model(kind)}", str(e)[:160])
    try:
        from . import jev

        jev.decide("x", {"q": jev.choice("Pick yes.", {"yes": "Yes", "no": "No"})}, purpose="health")
        item(True, f"Jev {os.getenv('JEV_MODEL') or ''}".strip())
    except Exception as e:
        item(False, "Jev decision model", str(e)[:160])
    try:
        from .resume import tex

        tex.pdflatex_path()
        item(True, "LaTeX (pdflatex) installed: tailored resumes can be built")
    except Exception:
        item(False, "LaTeX (pdflatex): only needed for tailored resumes",
             "Install MiKTeX (Windows) or TeX Live (Mac/Linux), or upload PDF-only resumes")
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode())

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc not in ALLOWED_HOSTS:
            return False
        return self.headers.get("X-Appli") == "1" and self.headers.get("Host") in ALLOWED_HOSTS

    def _user(self, query: dict | None = None) -> dict | None:
        """The dashboard user behind this request: {id, email, token}, verified with Supabase (cached 5 min)."""
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ((query or {}).get("token") or [""])[0]
        if not token:
            return None
        hit = _tokens.get(token)
        if hit and time.time() - hit[0] < 300:
            return {**hit[1], "token": token}
        user = db.verify_token(token)
        if not user:
            return None
        if len(_tokens) > 200:
            _tokens.clear()
        _tokens[token] = (time.time(), user)
        return {**user, "token": token}

    # ---- GET --------------------------------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        if path.startswith("/api/"):
            query = parse_qs(url.query)
            # PDFs open in a new tab (no custom headers there), so they authenticate with ?token=
            if not (self._origin_ok() or re.fullmatch(r"/api/resume/\d+\.pdf", path)):
                return self._json(403, {"error": "forbidden"})
            user = self._user(query)
            if not user:
                return self._json(401, {"error": "Sign in again"})
            return self._get_api(path, user)
        if not DIST.exists():
            return self._send(503, b"Dashboard not built yet: run start.bat (or npm run build in dashboard/).", "text/plain")
        rel = path.lstrip("/") or "index.html"
        f = (DIST / rel).resolve()
        if DIST.resolve() not in f.parents and f != DIST.resolve() or not f.is_file():
            f = DIST / "index.html"  # single-page app fallback
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        self._send(200, f.read_bytes(), ctype)

    def _get_api(self, path: str, user: dict):
        uid = user["id"]
        m = re.fullmatch(r"/api/resume/(\d+)\.pdf", path)
        if m:
            with _files_lock:
                cfg = config.activate(uid, required=False)
            files = sorted((cfg.tailored_dir / m.group(1)).glob("*.pdf")) if cfg else []
            if not files:
                return self._json(404, {"error": "no tailored resume for this job yet"})
            return self._send(200, files[-1].read_bytes(), "application/pdf")
        if path == "/api/status":
            return self._json(200, status(uid))
        if path == "/api/me":
            with db.as_user(user["token"], uid):
                try:
                    prof = db.sb().table("profiles").select("updated_at").execute().data
                    has_profile, db_ready = bool(prof), True
                except Exception:
                    has_profile, db_ready = False, False
            return self._json(200, {
                **userfiles.status(uid), "email": user["email"], "has_profile": has_profile, "db_ready": db_ready,
                "runner_connected": db.has_session(uid), "legacy_found": userfiles.legacy_found(),
            })
        if path == "/api/health":
            return self._json(200, {"checks": health()})
        return self._json(404, {"error": "not found"})

    # ---- POST -------------------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        if not self._origin_ok():
            return self._json(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "That file is too large (10 MB max)"})
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        user = self._user()
        if not user:
            return self._json(401, {"error": "Sign in again"})
        try:
            with _files_lock, db.as_user(user["token"], user["id"]):
                config.activate(user["id"], required=False)
                return self._post_api(path, body, user)
        except userfiles.FileError as e:
            return self._json(400, {"error": str(e)})
        except SystemExit as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:
            import traceback

            traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {str(e)[:200]}"})

    def _post_api(self, path: str, body: dict, user: dict):
        uid = user["id"]
        if path == "/api/session":  # the runner's own session (a second sign-in done by the dashboard)
            access, refresh = body.get("access_token"), body.get("refresh_token")
            if not (access and refresh):
                return self._json(400, {"error": "access_token and refresh_token are required"})
            same = db.verify_token(access)
            if not same or same["id"] != uid:
                return self._json(403, {"error": "That session belongs to another account"})
            db.save_session(uid, access, refresh, user["email"])
            return self._json(200, {"ok": True})
        if path == "/api/run":
            limit = max(1, min(int(body.get("limit", 20)), 100))
            ids = None
            if body.get("ids") is not None:
                try:
                    ids = list(dict.fromkeys(int(x) for x in body["ids"]))[:100]  # keep your order, drop repeats
                except (TypeError, ValueError):
                    return self._json(400, {"error": "ids must be a list of job ids"})
                if not ids:
                    return self._json(400, {"error": "Select at least one job"})
            code, out = start(uid, limit, ids)
            return self._json(code, out)
        if path == "/api/stop":
            if not running_pid() or _run_owner() not in (None, uid):
                return self._json(200, {"stopped": False})
            STOP.write_text("stop")
            return self._json(200, {"stopped": True})

        # ---- onboarding / My files
        if path == "/api/files/start":
            cfg = userfiles.create(uid, user["email"], str(body.get("name", "")))
            return self._json(200, userfiles.status(uid) | {"folder": cfg.folder.relative_to(ROOT).as_posix()})
        if path == "/api/files/resume":
            r = userfiles.save_resume(uid, str(body.get("label", "")), _b64(body.get("pdf")), _b64(body.get("tex")),
                                      body.get("focus"), body.get("key"))
            return self._json(200, userfiles.status(uid) | {"saved": r.key})
        if path == "/api/files/resume/remove":
            userfiles.remove_resume(uid, str(body.get("key", "")))
            return self._json(200, userfiles.status(uid))
        if path == "/api/files/resume/remove-tex":
            userfiles.remove_tex(uid, str(body.get("key", "")))
            return self._json(200, userfiles.status(uid))
        if path == "/api/files/cover":
            data = _b64(body.get("data"))
            if not data:
                return self._json(400, {"error": "Choose a file"})
            userfiles.save_cover(uid, str(body.get("filename", "cover.pdf")), data)
            return self._json(200, userfiles.status(uid))
        if path == "/api/files/cover/remove":
            userfiles.remove_cover(uid)
            return self._json(200, userfiles.status(uid))
        if path == "/api/legacy/adopt":
            return self._adopt(user)

        # ---- profile
        if path == "/api/profile/sync":
            return self._profile_sync(uid)
        if path == "/api/profile/import":
            return self._profile_import(body)

        # ---- jobs
        if path == "/api/jobs/preview":
            return self._jobs(body, save=False)
        if path == "/api/jobs/import":
            return self._jobs(body, save=True)

        # ---- resume tailoring
        if path == "/api/resume/build":
            return self._resume_build(body)
        if path == "/api/resume/dismiss":
            return self._resume_dismiss(body)
        return self._json(404, {"error": "not found"})

    def _adopt(self, user: dict):
        """Rishi's original files in the project root -> users/<Name>/, and his profile into the database."""
        from . import profile_md

        out = userfiles.adopt_legacy(user["id"], user["email"])
        config.activate(user["id"])
        existing = db.sb().table("profiles").select("owner_id").execute().data
        if not existing:
            data = out["profile_data"]
            db.sb().table("profiles").upsert({"owner_id": user["id"], "data": data, "markdown": out["profile_markdown"]},
                                             on_conflict="owner_id").execute()
            for rule in profile_md.common_rules(data):
                db.sb().table("answers").upsert({**rule, "owner_id": user["id"], "source": "manual"},
                                                on_conflict="owner_id,question_key", ignore_duplicates=True).execute()
        return self._json(200, {"folder": out["folder"], "copied": out["copied"], "profile_saved": not existing})

    def _profile_sync(self, uid: str):
        """After the dashboard saves the profile: write it to this person's users/<Name>/profile.md."""
        from . import profile_md

        if config.PROFILE_PATH is None:
            return self._json(400, {"error": "Start with your name first"})
        status_ = profile_md.sync_from_db(config.PROFILE_PATH)
        return self._json(200, {"status": status_, "path": config.PROFILE_PATH.relative_to(ROOT).as_posix()})

    def _profile_import(self, body: dict):
        """Pre-fill the Profile form from one of this person's resume PDFs (they review everything before saving)."""
        from . import llm
        from .cover_letter import resume_text

        key = body.get("resume_key") or next(iter(config.RESUMES), None)
        pdf = config.RESUMES.get(key) if key else None
        if not pdf or not pdf.exists():
            return self._json(400, {"error": "Add a resume first (My files)."})
        system = (
            "Extract a job applicant's profile from their resume text. Copy facts exactly; never invent. Leave a field "
            "empty if the resume doesn't state it. Dates as YYYY-MM (end may be 'Present'). Return JSON: "
            '{"name": str, "email": str, "phone": str (with country code if shown), "location": "City, ST", '
            '"linkedin": url, "github": url, "website": url, '
            '"education": [{"school": str, "degree": str, "field": str, "start": str, "end": str, "gpa": str}], '
            '"experience": [{"title": str, "company": str, "start": str, "end": str, "bullets": [str]}], '
            '"skills": [{"group": str, "items": "comma, separated"}]}'
        )
        try:
            data = llm.chat_json(system, resume_text(str(pdf))[:12000], model=config.model("write"), max_tokens=6000,
                                 purpose="profile import from resume")
        except Exception as e:
            return self._json(500, {"error": f"Couldn't read the resume: {str(e)[:200]}"})
        return self._json(200, {"data": data, "resume": pdf.name})

    def _jobs(self, body: dict, save: bool):
        """Add jobs: pasted links, or an uploaded CSV/xlsx with a column mapping. preview -> the rows it would add."""
        from dataclasses import asdict

        from . import joblinks, sheet

        if body.get("links"):
            rows, extra = joblinks.from_links(str(body["links"])), {}
        elif body.get("data"):
            parsed = sheet.read_table(_b64(body["data"]), str(body.get("filename", "jobs.csv")),
                                      body.get("mapping") or None, body.get("sheet") or None)
            rows = parsed["rows"]
            extra = {k: parsed[k] for k in ("sheets", "sheet", "headers", "mapping", "problems")}
            if save and parsed["problems"]:
                return self._json(400, {"error": "; ".join(parsed["problems"])})
        else:
            return self._json(400, {"error": "Paste links or choose a file"})
        if not save:
            from .ats import SUPPORTED

            return self._json(200, {**extra, "count": len(rows), "automated": sum(r.ats in SUPPORTED for r in rows),
                                    "rows": [asdict(r) | {"automated": r.ats in SUPPORTED} for r in rows[:50]]})
        if not rows:
            return self._json(400, {"error": "No jobs found to add"})
        result = db.import_jobs(rows)
        result["duplicates_skipped"] = db.dedupe_jobs()
        return self._json(200, result)

    def _resume_build(self, body: dict):
        """Build the tailored PDF from the skills ticked; the paused job goes back to the front of the queue."""
        from .resume import build as resume_build

        try:
            job_id = int(body.get("job_id"))
            accepted = [str(x) for x in body.get("accepted", [])]
        except (TypeError, ValueError):
            return self._json(400, {"error": "job_id and accepted[] are required"})
        rows = db.sb().table("jobs").select("*").eq("id", job_id).execute().data
        if not rows:
            return self._json(404, {"error": "job not found"})
        out = resume_build.build(rows[0], accepted)
        if not out.get("ok"):
            return self._json(200, out)
        if rows[0]["status"] in ("tailoring", "queued", "failed", "needs_manual"):
            db.sb().table("jobs").update(
                {"status": "queued", "status_reason": "Tailored resume ready: filled first on the next run"}
            ).eq("id", job_id).execute()
            out["next"] = "Queued: it will be filled with your tailored resume first on the next run."
        else:
            out["next"] = "Saved. This job was already filled; upload the new PDF yourself if you haven't submitted yet."
        return self._json(200, out)

    def _resume_dismiss(self, body: dict):
        """Skip tailoring: fill the job with the best base resume instead."""
        try:
            job_id = int(body.get("job_id"))
        except (TypeError, ValueError):
            return self._json(400, {"error": "job_id is required"})
        rows = db.sb().table("jobs").select("status").eq("id", job_id).execute().data
        if not rows:
            return self._json(404, {"error": "job not found"})
        upd = {"resume_review": "dismissed"}
        if rows[0]["status"] == "tailoring":
            upd.update(status="queued", status_reason="Tailoring dismissed: will use the best base resume")
        db.sb().table("jobs").update(upd).eq("id", job_id).execute()
        return self._json(200, {"ok": True})


class _ExclusiveServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind the same port and silently steal requests,
    # so never reuse: a second start fails loudly instead.
    allow_reuse_address = False


def serve(open_browser: bool = True):
    OUT.mkdir(exist_ok=True)
    try:
        srv = _ExclusiveServer(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Appli is already running at http://localhost:{PORT} in another window. Use that one "
              f"(or close it first). Exiting.")
        if open_browser:
            webbrowser.open(f"http://localhost:{PORT}")
        return
    url = f"http://localhost:{PORT}"
    print(f"Appli is running at {url}  (Ctrl+C to quit)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
