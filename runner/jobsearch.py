"""Find relevant jobs for one person: search, filter, screen and score, then let them pick what to add.

Sources (all public, no scraping, no account):
  - company job boards: Greenhouse / Lever / Ashby list every opening with its description; Workday is searched per
    title. The companies are the ones already in the person's jobs plus any careers links they add.
  - community new-grad / internship lists (SimplifyJobs on GitHub, updated daily; carries sponsorship flags).
Pipeline: fetch -> filter by the person's search preferences (titles, level, location, age, excluded words) and drop
anything already in their jobs -> rank -> for the top N: read the posting, run the same sponsorship / citizenship /
clearance screen as a run (patterns + Jev), and score it against their resumes (match %).
Results go to users/<Name>/found.json; the dashboard's Find jobs tab shows them and the person ticks what to add.
"""
import html
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config
from .ats import SUPPORTED, detect_ats
from .sheet import JobRow, company_from_url

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
SIMPLIFY = "https://raw.githubusercontent.com/SimplifyJobs/{repo}/dev/.github/scripts/listings.json"


# ---- preferences ----------------------------------------------------------------------------------------------
@dataclass
class Prefs:
    titles: list[str] = field(default_factory=list)  # e.g. Software Engineer, AI Engineer
    levels: list[str] = field(default_factory=lambda: ["new_grad", "entry"])  # intern | new_grad | entry
    locations: list[str] = field(default_factory=list)  # empty = anywhere in your country; "Remote" = remote roles
    days: int = 30  # posted within
    exclude: list[str] = field(default_factory=lambda: ["senior", "sr.", "staff", "principal", "lead", "manager",
                                                        "director", "head of", "architect"])
    sources: list[str] = field(default_factory=lambda: ["boards", "lists"])
    companies: list[str] = field(default_factory=list)  # extra careers links (Greenhouse / Lever / Ashby / Workday)
    score_top: int = 40  # how many of the best candidates get the full screen + resume match


def _prefs_file() -> Path:
    return config.HOME / "search.json"


def load_prefs() -> Prefs:
    try:
        d = json.loads(_prefs_file().read_text(encoding="utf-8"))
        return Prefs(**{k: v for k, v in d.items() if k in Prefs.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return Prefs()


def save_prefs(d: dict) -> Prefs:
    base = asdict(Prefs())
    clean = {}
    for k, default in base.items():
        v = d.get(k, default)
        if isinstance(default, list):
            v = [str(x).strip() for x in (v or []) if str(x).strip()][:60]
        elif isinstance(default, int):
            try:
                v = max(1, min(int(v), 400 if k == "days" else 150))
            except (TypeError, ValueError):
                v = default
        clean[k] = v
    p = Prefs(**clean)
    _prefs_file().write_text(json.dumps(asdict(p), indent=1), encoding="utf-8")
    return p


def suggest_titles(profile_text: str) -> list[str]:
    """Job titles to search for, from the profile and each resume's one-line description (fast model)."""
    from . import llm

    focus = "\n".join(f"- {config.RESUME_LABELS.get(k, k)}: {v}" for k, v in config.RESUME_FOCUS.items())
    out = llm.chat_json(
        "Suggest 4 to 8 job titles this candidate should search for on job boards, exactly as companies write them "
        "(e.g. 'Software Engineer', 'Machine Learning Engineer', 'Forward Deployed Engineer'). No seniority words, no "
        'levels. Return JSON: {"titles": [str]}',
        f"RESUMES:\n{focus}\n\nPROFILE:\n{profile_text[:5000]}", model=config.model("fast"), max_tokens=600,
        purpose="search titles",
    )
    return [t.strip() for t in (out.get("titles") or []) if isinstance(t, str) and t.strip()][:8]


# ---- candidates -----------------------------------------------------------------------------------------------
@dataclass
class Found:
    key: str  # canonical posting link
    company: str
    title: str
    location: str
    url: str
    ats: str
    source: str
    posted: str | None = None  # ISO date
    description: str = ""
    sponsorship: str = ""  # SimplifyJobs flag, when known
    category: str = ""
    # filled in by scoring
    match_pct: float | None = None
    resume: str | None = None
    screened: str | None = None  # reason it was screened out
    match: dict | None = None
    rank: float = 0.0


def _text(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h or "")
    h = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", h)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(h)))).strip()


def _iso(ts) -> str | None:
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc).date().isoformat()
        if isinstance(ts, str) and ts:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OSError):
        pass
    return None


def _age_days(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        return (datetime.now(timezone.utc).date() - datetime.fromisoformat(iso).date()).days
    except ValueError:
        return None


def _workday_posted(s: str) -> str | None:
    """'Posted Today' / 'Posted Yesterday' / 'Posted 11 Days Ago' / 'Posted 30+ Days Ago' -> ISO date."""
    s = (s or "").lower()
    days = 0 if "today" in s else 1 if "yesterday" in s else None
    m = re.search(r"(\d+)\+?\s*days?", s)
    if m:
        days = int(m.group(1))
    if days is None:
        return None
    return datetime.fromtimestamp(time.time() - days * 86400, tz=timezone.utc).date().isoformat()


def _key(url: str) -> str:
    from .db import canonical

    return canonical(detect_ats(url), url) or url.lower().rstrip("/")


# ---- source: SimplifyJobs lists -------------------------------------------------------------------------------
def from_lists(levels: list[str], log) -> list[Found]:
    now = datetime.now(timezone.utc)
    repos = []
    if {"new_grad", "entry"} & set(levels):
        repos.append(("New-Grad-Positions", "New-grad list"))
    if "intern" in levels:
        for y in sorted({now.year, now.year + 1}):
            repos.append((f"Summer{y}-Internships", "Internship list"))
    out, seen = [], set()
    with httpx.Client(timeout=60, follow_redirects=True, headers=UA) as c:
        for repo, label in repos:
            try:
                r = c.get(SIMPLIFY.format(repo=repo))
                if r.status_code != 200:
                    continue
                items = r.json()
            except Exception as e:
                log(f"  {label}: couldn't load ({type(e).__name__})")
                continue
            n = 0
            for x in items:
                if not (x.get("active") and x.get("is_visible", True) and x.get("url")) or x["url"] in seen:
                    continue
                seen.add(x["url"])
                n += 1
                out.append(Found(_key(x["url"]), x.get("company_name") or company_from_url(x["url"]), x.get("title") or "",
                                 "; ".join(x.get("locations") or []), x["url"], detect_ats(x["url"]), label,
                                 _iso(x.get("date_posted")), "", x.get("sponsorship") or "", x.get("category") or ""))
            log(f"  {label} ({repo}): {n} open postings")
    return out


# ---- source: company job boards -------------------------------------------------------------------------------
_BOARD_PATTERNS = {
    "greenhouse": re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)", re.I),
    "lever": re.compile(r"lever\.co/([\w.-]+)", re.I),
    "ashby": re.compile(r"ashbyhq\.com/([\w.%-]+)", re.I),
    "workday": re.compile(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", re.I),
}


def board_of(url: str) -> tuple[str, str] | None:
    """A careers/posting link -> (ats, board id). Workday's id is 'tenant|wdN|site'."""
    ats = detect_ats(url)
    m = _BOARD_PATTERNS.get(ats, re.compile("x^")).search(url or "")
    if not m:
        return None
    if ats == "workday":
        return ats, "|".join(m.groups())
    if m.group(1).lower() in ("embed", "v1", "boards", "jobs"):
        return None
    return ats, m.group(1)


def company_boards(extra: list[str]) -> list[tuple[str, str, str]]:
    """(ats, board id, company name) for every company in the person's jobs, plus the careers links they added."""
    from .db import all_jobs

    boards: dict[tuple[str, str], str] = {}
    for j in all_jobs("company,url,ats"):
        b = board_of(j.get("url") or "")
        if b and b not in boards:
            boards[b] = j["company"]
    for u in extra:
        b = board_of(u)
        if b and b not in boards:
            boards[b] = company_from_url(u)
    return [(a, i, name) for (a, i), name in boards.items()]


def _board_jobs(c: httpx.Client, ats: str, bid: str, company: str, titles: list[str]) -> list[Found]:
    out = []
    if ats == "greenhouse":
        r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{bid}/jobs", params={"content": "true"})
        for j in r.json().get("jobs", []) if r.status_code == 200 else []:
            u = j.get("absolute_url") or ""
            out.append(Found(_key(u), j.get("company_name") or company, j.get("title", ""), (j.get("location") or {}).get("name", ""),
                             u, "greenhouse", "Company board", _iso(j.get("first_published") or j.get("updated_at")),
                             _text(j.get("content", ""))[:8000]))
    elif ats == "lever":
        r = c.get(f"https://api.lever.co/v0/postings/{bid}", params={"mode": "json"})
        for j in r.json() if r.status_code == 200 and isinstance(r.json(), list) else []:
            u = j.get("hostedUrl") or ""
            cat = j.get("categories") or {}
            lists = " ".join(f"{x.get('text', '')}: {_text(x.get('content', ''))}" for x in j.get("lists") or [])
            out.append(Found(_key(u), company, j.get("text", ""), cat.get("location") or "", u, "lever", "Company board",
                             _iso(j.get("createdAt")), f"{j.get('descriptionPlain', '')}\n{lists}"[:8000]))
    elif ats == "ashby":
        r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{bid}")
        for j in r.json().get("jobs", []) if r.status_code == 200 else []:
            if j.get("isListed") is False:
                continue
            u = j.get("jobUrl") or ""
            loc = j.get("location") or ""
            if j.get("isRemote") and "remote" not in loc.lower():
                loc = f"{loc} (Remote)".strip()
            out.append(Found(_key(u), company, j.get("title", ""), loc, u, "ashby", "Company board", _iso(j.get("publishedAt")),
                             (j.get("descriptionPlain") or "")[:8000]))
    elif ats == "workday":
        tenant, wd, site = bid.split("|")
        host = f"https://{tenant}.{wd}.myworkdayjobs.com"
        seen = set()
        for q in titles[:4] or ["engineer"]:
            r = c.post(f"{host}/wday/cxs/{tenant}/{site}/jobs",
                       json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": q})
            for j in r.json().get("jobPostings", []) if r.status_code == 200 else []:
                path = j.get("externalPath") or ""
                if not path or path in seen:
                    continue
                seen.add(path)
                u = f"{host}/{site}{path}"
                out.append(Found(_key(u), company, j.get("title", ""), j.get("locationsText") or "", u, "workday",
                                 "Company board", _workday_posted(j.get("postedOn", ""))))
    return out


def from_boards(boards: list[tuple[str, str, str]], titles: list[str], log) -> list[Found]:
    out = []

    def one(b):
        ats, bid, name = b
        try:
            with httpx.Client(timeout=15, follow_redirects=True, headers=UA) as c:
                return _board_jobs(c, ats, bid, name, titles)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, jobs in enumerate(ex.map(one, boards), 1):
            out += jobs
            if i % 50 == 0 or i == len(boards):
                log(f"  company boards: {i}/{len(boards)} read, {len(out)} openings so far")
    return out


# ---- filtering ------------------------------------------------------------------------------------------------
# same role, different words: both the target titles and the posting titles are rewritten to one spelling first
_CANON = [
    (r"\bmachine[- ]learning\b", "ml"), (r"\bartificial intelligence\b|\ba\.i\.", "ai"), (r"\bgen(erative)? ?ai\b", "ai"),
    (r"\bsoftware development engineer\b|\bsde\b|\bswe\b", "software engineer"), (r"\bdeveloper\b", "engineer"),
    (r"\bfull[- ]stack\b", "fullstack"), (r"\bback[- ]end\b", "backend"), (r"\bfront[- ]end\b", "frontend"),
]
_INTERN = re.compile(r"\bintern(ship)?\b|\bco-?op\b", re.I)
_NEW_GRAD = re.compile(r"new ?grad|university|graduate|early[- ]career|entry[- ]level|\bjunior\b|\bjr\.?\b|\bassociate\b|"
                       r"campus|\b(20\d\d)\b|\b(i|1)\b\s*$|engineer i\b|rotational|residency", re.I)
_REMOTE = re.compile(r"remote|anywhere|distributed", re.I)


def _words(s: str) -> list[str]:
    s = s.lower()
    for pat, rep in _CANON:
        s = re.sub(pat, rep, s)
    return [w for w in re.findall(r"[a-z0-9+#]+", s) if w not in ("and", "of", "the", "for", "a", "an")]


def title_matches(title: str, targets: list[str]) -> bool:
    """Every word of one target title appears in the posting's title ('Software Engineer' matches
    'Software Engineer II, Payments'; 'Machine Learning Engineer' matches 'ML Engineer, New Grad')."""
    have = set(_words(title))
    return any(ws and set(ws) <= have for ws in (_words(t) for t in targets))


def level_ok(f: Found, levels: list[str]) -> bool:
    if not levels:
        return True
    intern = bool(_INTERN.search(f.title)) or f.source == "Internship list"
    if intern:
        return "intern" in levels
    if "new_grad" in levels and (_NEW_GRAD.search(f.title) or f.source == "New-grad list"):
        return True
    return "entry" in levels  # not an internship, no seniority word (the exclude list takes those out)


def location_ok(loc: str, prefs: Prefs, country: str, state: str) -> bool:
    from .places import US_STATES, canonical_country

    if not loc:
        return True
    if prefs.locations:
        return any((_REMOTE.search(loc) if p.lower() == "remote" else p.lower() in loc.lower()) for p in prefs.locations)
    if _REMOTE.search(loc):
        return True
    if canonical_country(country) == "united states":  # anywhere in the US
        # two-letter codes only as ", CA" (case-sensitive: "or", "in", "me" are words); full names any case
        return bool(re.search(r",\s*(" + "|".join(US_STATES) + r")\b|\b(US|USA)\b", loc)
                    or re.search(r"united states|" + "|".join(re.escape(v) for v in US_STATES.values()), loc, re.I))
    return country.lower() in loc.lower() if country else True


def keep(f: Found, prefs: Prefs, country: str, state: str, active_screens: frozenset[str]) -> str | None:
    """None = keep; else why it was filtered out (for the counts)."""
    if prefs.titles and not title_matches(f.title, prefs.titles):
        return "title"
    low = f" {f.title.lower()} "
    if any(re.search(rf"(?<![a-z]){re.escape(x.lower())}(?![a-z])", low) for x in prefs.exclude):
        return "excluded word"
    if not level_ok(f, prefs.levels):
        return "level"
    if not location_ok(f.location, prefs, country, state):
        return "location"
    age = _age_days(f.posted)
    if age is not None and age > prefs.days:
        return "too old"
    if "no_sponsor" in active_screens and f.sponsorship == "Does Not Offer Sponsorship":
        return "no sponsorship"
    if "citizen" in active_screens and f.sponsorship == "U.S. Citizenship is Required":
        return "citizens only"
    return None


def rank(f: Found, prefs: Prefs) -> float:
    age = _age_days(f.posted)
    score = 0.0
    score += 3 if f.ats in SUPPORTED else 0  # Appli can fill it
    score += max(0, 2 - (age or 30) / 15)  # newer first
    score += 1 if f.sponsorship == "Offers Sponsorship" else 0
    # early-career titles first when that's what the person is after
    if {"new_grad", "intern"} & set(prefs.levels) and (_NEW_GRAD.search(f.title) or _INTERN.search(f.title)
                                                        or f.source in ("New-grad list", "Internship list")):
        score += 2
    score += 1 if any(" ".join(_words(t)) == " ".join(_words(f.title)) for t in prefs.titles) else 0
    return score


# ---- scoring: read the posting, screen it, match it against the resumes -----------------------------------------
def describe(f: Found, c: httpx.Client) -> str:
    """Posting text via the ATS's public API (or the page itself)."""
    u = f.url
    try:
        if f.ats == "greenhouse":
            m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+).*?jobs/(\d+)", u) or re.search(r"for=([\w-]+).*?token=(\d+)", u)
            if m:
                r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}", params={"content": "true"})
                if r.status_code == 200:
                    return _text(r.json().get("content", ""))
        elif f.ats == "lever":
            m = re.search(r"lever\.co/([\w.-]+)/([\w-]{20,})", u)
            if m:
                r = c.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
                if r.status_code == 200:
                    j = r.json()
                    return j.get("descriptionPlain", "") + "\n" + " ".join(
                        f"{x.get('text', '')}: {_text(x.get('content', ''))}" for x in j.get("lists") or [])
        elif f.ats == "workday":
            m = re.match(r"https?://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)(/job/.+)$", u.split("?")[0])
            if m:
                t, wd, site, path = m.groups()
                r = c.get(f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/{site}{path}")
                if r.status_code == 200:
                    return _text(r.json().get("jobPostingInfo", {}).get("jobDescription", ""))
        elif f.ats == "ashby":
            m = re.search(r"ashbyhq\.com/([\w.%-]+)/([\w-]{20,})", u)
            if m:
                r = c.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
                if r.status_code == 200:
                    for j in r.json().get("jobs", []):
                        if j.get("id") == m.group(2) or m.group(2) in (j.get("jobUrl") or ""):
                            return j.get("descriptionPlain") or ""
        r = c.get(u)
        return _text(r.text)[:8000] if r.status_code == 200 else ""
    except Exception:
        return ""


def score(items: list[Found], active: frozenset[str], log) -> None:
    from .resume import select
    from .screening import jev_screen, screen

    def one(f: Found):
        with httpx.Client(timeout=15, follow_redirects=True, headers=UA) as c:
            if len(f.description) < 300:
                f.description = describe(f, c)[:8000]
        text = f.description
        if len(text) < 300:
            return f  # nothing to read: shown without a match %
        reason = screen(text, active)
        if not reason:
            try:
                reason = jev_screen(text, None, active)
            except Exception:
                pass
        if reason:
            f.screened = reason
            return f
        try:
            info = select.evaluate({"company": f.company, "role": f.title, "description": text, "id": None})
        except Exception:
            info = None
        if info:
            f.match_pct, f.resume, f.match = info["pct"], info["chosen"], info
        return f

    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, f in enumerate(ex.map(one, items), 1):
            what = (f"screened out: {f.screened[:70]}" if f.screened else
                    f"match {f.match_pct:.0%} ({config.RESUME_LABELS.get(f.resume, f.resume)})" if f.match_pct is not None
                    else "no posting text")
            log(f"[{i}/{len(items)}] {f.company} - {f.title}: {what}")


# ---- the whole search -------------------------------------------------------------------------------------------
def _found_file() -> Path:
    return config.HOME / "found.json"


def load_found() -> dict:
    try:
        return json.loads(_found_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_found(d: dict):
    _found_file().write_text(json.dumps(d), encoding="utf-8")


def search(log=print) -> dict:
    from .db import all_jobs
    from .profile import load_profile
    from .screening import active_screens

    prefs = load_prefs()
    profile = load_profile(config.PROFILE_PATH, config.PRIMARY_TEX)
    if not prefs.titles:
        prefs.titles = suggest_titles(profile.text)
        save_prefs(asdict(prefs))
        log(f"Search titles (suggested from your profile; edit them on Find jobs): {', '.join(prefs.titles)}")
    active = active_screens(profile.standard_answers)
    log(f"Searching for: {', '.join(prefs.titles)} | levels {', '.join(prefs.levels) or 'any'} | "
        f"{', '.join(prefs.locations) or 'anywhere in ' + (profile.country or 'your country')} | last {prefs.days} days")

    found: list[Found] = []
    if "lists" in prefs.sources:
        found += from_lists(prefs.levels, log)
    if "boards" in prefs.sources:
        boards = company_boards(prefs.companies)
        log(f"  reading {len(boards)} company job boards...")
        found += from_boards(boards, prefs.titles, log)

    have = set()
    for j in all_jobs("url,dedupe_key"):
        if j.get("url"):
            have.add(_key(j["url"]))
        have.add((j.get("dedupe_key") or "").lower().rstrip("/"))
    counts: dict[str, int] = {"fetched": len(found)}
    kept, seen = [], set()
    for f in found:
        if f.key in seen:
            continue
        seen.add(f.key)
        if f.key in have:
            counts["already in your jobs"] = counts.get("already in your jobs", 0) + 1
            continue
        why = keep(f, prefs, profile.country, profile.state, active)
        if why:
            counts[why] = counts.get(why, 0) + 1
            continue
        f.rank = rank(f, prefs)
        kept.append(f)
    kept.sort(key=lambda f: (-f.rank, f.company))
    counts["matched"] = len(kept)
    log("Filtered: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    top = kept[: prefs.score_top]
    log(f"Reading, screening and scoring the top {len(top)}...")
    score(top, active, log)
    for f in top:
        f.rank += (f.match_pct or 0) * 5 - (10 if f.screened else 0)
    results = sorted(top, key=lambda f: -f.rank) + kept[prefs.score_top:]
    out = {
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prefs": asdict(prefs),
        "counts": counts,
        "results": [{**{k: v for k, v in asdict(f).items() if k not in ("description", "match")},
                     "fillable": f.ats in SUPPORTED, "scored": i < len(top), "added": False,
                     "match": f.match} for i, f in enumerate(results[:300])],
    }
    save_found(out)
    log(f"Done: {len(kept)} relevant posting(s); top {len(top)} scored. Open Find jobs to pick.")
    return out


def to_rows(results: list[dict]) -> list[JobRow]:
    """Picked results -> job rows. Better matches get a better tier, so the queue fills them first."""
    rows = []
    for r in results:
        pct = r.get("match_pct")
        tier = None if pct is None else 1 if pct >= config.RESUME_MATCH_THRESHOLD else 2 if pct >= 0.6 else 3
        rows.append(JobRow(dedupe_key=r["url"], company=r["company"], role=r["title"] or None,
                           location=r.get("location") or None, salary=None, url=r["url"],
                           source=f"Find jobs: {r.get('source', '')}".strip(), fit_tier=tier,
                           days_posted=_age_days(r.get("posted")), ats=r["ats"]))
    return rows
