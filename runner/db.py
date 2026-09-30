import json
import os
import threading
from dataclasses import asdict
from pathlib import Path

from supabase import Client, create_client

from . import config
from .ats import SUPPORTED
from .sheet import JobRow

_client: Client | None = None
_user_id: str | None = None
_local = threading.local()  # the local server runs each request as the dashboard user who sent it

SESSIONS = config.ROOT / "out" / "sessions"  # git-ignored: the runner's own login per person


def _url_key() -> tuple[str, str]:
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    url = os.getenv("SUPABASE_URL")
    if not (url and key):
        raise SystemExit("Add SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY to .env (the project owner shares them).")
    return url, key


# ---- the runner's own session ----------------------------------------------------------------
# When you sign in on the dashboard it also opens a second, separate session for the runner and hands it to the
# local server, which saves it here. Two sessions means the dashboard and the runner never use up each other's
# refresh tokens, and no password is ever stored.
def session_file(owner_id: str) -> Path:
    return SESSIONS / f"{owner_id}.json"


def save_session(owner_id: str, access_token: str, refresh_token: str, email: str = ""):
    SESSIONS.mkdir(parents=True, exist_ok=True)
    session_file(owner_id).write_text(json.dumps({"access_token": access_token, "refresh_token": refresh_token,
                                                  "email": email}), encoding="utf-8")
    (SESSIONS / "last").write_text(owner_id, encoding="utf-8")


def has_session(owner_id: str) -> bool:
    return session_file(owner_id).exists()


def _owner() -> str | None:
    """Whose run this is: set by the server when it starts the runner; else whoever last signed in here."""
    if os.getenv("APPLI_OWNER"):
        return os.getenv("APPLI_OWNER")
    try:
        return (SESSIONS / "last").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def sb() -> Client:
    """Supabase client signed in AS the person using Appli, so the database's row-level policies limit every query to
    their own jobs, answers and profile. The service-role key is never used."""
    override = getattr(_local, "client", None)
    if override is not None:
        return override
    global _client, _user_id
    if _client is None:
        url, key = _url_key()
        owner = _owner()
        try:
            saved = json.loads(session_file(owner).read_text(encoding="utf-8")) if owner else None
        except (OSError, json.JSONDecodeError):
            saved = None
        if not saved:
            raise SystemExit("The runner isn't connected to your account yet: open the dashboard (start.bat) and sign in.")
        client = create_client(url, key)
        try:
            res = client.auth.set_session(saved["access_token"], saved["refresh_token"])
        except Exception as e:
            raise SystemExit(f"Your saved sign-in has expired ({str(e)[:120]}). Sign out and in again on the dashboard.") from None

        def keep(event, session):  # refresh tokens rotate: save each new one so the next run can still sign in
            if session and event in ("SIGNED_IN", "TOKEN_REFRESHED"):
                save_session(owner, session.access_token, session.refresh_token, saved.get("email", ""))

        client.auth.on_auth_state_change(keep)
        if res.session:
            keep("TOKEN_REFRESHED", res.session)
        _user_id = res.user.id
        _client = client
    return _client


def fresh():
    """Refresh the runner's token if it's close to expiring (called before each job of a long run)."""
    if _client is not None and getattr(_local, "client", None) is None:
        try:
            _client.auth.get_session()
        except Exception:
            pass


def user_id() -> str:
    """The signed-in person's id (the owner_id on everything they create)."""
    override = getattr(_local, "user_id", None)
    if override:
        return override
    sb()
    return _user_id


def verify_token(access_token: str) -> dict | None:
    """Check a dashboard access token with Supabase -> {id, email} or None."""
    url, key = _url_key()
    try:
        res = create_client(url, key).auth.get_user(access_token)
    except Exception:
        return None
    return {"id": res.user.id, "email": res.user.email or ""} if res and res.user else None


class as_user:
    """`with as_user(token, uid):` makes sb() / user_id() act as that dashboard user in this thread (server requests)."""

    def __init__(self, access_token: str, owner_id: str):
        url, key = _url_key()
        client = create_client(url, key)
        client.options.headers["Authorization"] = f"Bearer {access_token}"
        self.client, self.owner_id = client, owner_id

    def __enter__(self):
        _local.client, _local.user_id = self.client, self.owner_id
        return self.client

    def __exit__(self, *exc):
        _local.client = _local.user_id = None


def import_jobs(rows: list[JobRow]) -> dict:
    """Insert new jobs only; existing rows keep their status. A posting already present under another link
    spelling (or listed twice) is not inserted again."""
    require_multi_user()
    existing = all_jobs("dedupe_key,url,ats")
    seen_keys = {r["dedupe_key"] for r in existing}
    seen_canon = {canonical(r["ats"], r["url"]) for r in existing if r["url"]}
    new = []
    for r in rows:
        c = canonical(r.ats, r.url)
        if r.dedupe_key in seen_keys or (c and c in seen_canon):
            continue
        seen_keys.add(r.dedupe_key)
        if c:
            seen_canon.add(c)
        new.append(r)
    payload = []
    for r in new:
        d = {**asdict(r), "owner_id": user_id()}
        if not r.url:
            d["status"], d["status_reason"] = "needs_manual", "No apply link"
        elif r.ats not in SUPPORTED:
            d["status"], d["status_reason"] = "needs_manual", f"{r.ats} applications aren't automated yet"
        payload.append(d)
    for i in range(0, len(payload), 200):
        sb().table("jobs").insert(payload[i : i + 200]).execute()
    return {"inserted": len(new), "already_present": len(rows) - len(new)}


def park_unsupported() -> int:
    """Move still-queued jobs on unsupported ATSs to needs_manual so they never use a daily slot."""
    res = (
        sb()
        .table("jobs")
        .update({"status": "needs_manual", "status_reason": "ATS not automated yet"})
        .eq("status", "queued")
        .not_.in_("ats", sorted(SUPPORTED))
        .execute()
    )
    return len(res.data)


def unpark_workday() -> int:
    """Workday jobs parked as 'not automated yet' before Workday was supported go back to the queue (once)."""
    res = (
        sb()
        .table("jobs")
        .update({"status": "queued", "status_reason": "Workday: filled page by page, you click Save and Continue"})
        .eq("status", "needs_manual")
        .eq("ats", "workday")
        .or_("status_reason.ilike.%automated yet%,status_reason.ilike.%not automated%")
        .execute()
    )
    return len(res.data)


def canonical(ats: str | None, url: str | None) -> str | None:
    """One key per posting, however the link is spelled (/apply, /application?embed=true, trailing slash...)."""
    if not url:
        return None
    from .ats import adapters

    return adapters.urls(ats or "other", url)[0].lower().rstrip("/")


def all_jobs(columns: str) -> list[dict]:
    out, page = [], 0
    while True:
        batch = sb().table("jobs").select(columns).order("id").range(page * 1000, page * 1000 + 999).execute().data
        out += batch
        if len(batch) < 1000:
            return out
        page += 1


def dedupe_jobs() -> int:
    """Mark repeat listings of the same posting as skipped, keeping the one furthest along (never touches
    anything that is submitted or has a tab open)."""
    groups: dict[str, list[dict]] = {}
    for r in all_jobs("id,ats,url,status"):
        if r["ats"] in SUPPORTED:
            groups.setdefault(canonical(r["ats"], r["url"]), []).append(r)
    rank = {"submitted": 0, "ready_for_review": 1, "filling": 1, "queued": 2, "failed": 3, "needs_manual": 3, "skipped": 4}
    n = 0
    for g in groups.values():
        if len(g) < 2:
            continue
        keep = min(g, key=lambda r: (rank.get(r["status"], 5), r["id"]))
        for r in g:
            if r is not keep and r["status"] in ("queued", "failed", "needs_manual"):
                sb().table("jobs").update(
                    {"status": "skipped", "status_reason": f"Duplicate of job #{keep['id']} (same posting, different link)"}
                ).eq("id", r["id"]).execute()
                n += 1
    return n


def require_multi_user():
    """Stop with a clear message if migration 004 (owner per row) hasn't been run yet."""
    try:
        sb().table("jobs").select("owner_id").limit(1).execute()
        sb().table("profiles").select("owner_id").limit(1).execute()
    except SystemExit:
        raise
    except Exception:
        raise SystemExit("Run supabase/migrations/004_multi_user.sql in the Supabase SQL editor first "
                         "(it gives every row an owner so several people can share the project).") from None
