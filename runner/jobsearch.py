"""Find relevant jobs for one person: search, filter, screen and score, then let them pick what to add.

Sources (all public, no scraping, no account):
  - company job boards: Greenhouse / Lever / Ashby list every opening with its description; Workday is searched per
    title. The companies are the ones already in the person's jobs plus any careers links they add.
  - community new-grad / internship lists (SimplifyJobs on GitHub, updated daily; carries sponsorship flags).
  - remote job boards with a public feed (Remote OK, We Work Remotely, Remotive, Working Nomads, Jobspresso,
    Landing.jobs, PowerToFly); each listing is followed to the company's own application form when it has one.
Pipeline: fetch -> filter by the person's search preferences (titles, level, location, age, excluded words) and drop
anything already in their jobs -> rank -> for the top N: read the posting, run the same sponsorship / citizenship /
clearance screen patterns as a run (no Jev), and score it against their resumes (match %).
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
    sources: list[str] = field(default_factory=lambda: ["boards", "lists", "remote"])
    companies: list[str] = field(default_factory=list)  # extra careers links (Greenhouse / Lever / Ashby / Workday)
    score_top: int = 40  # how many of the best candidates get the full screen + resume match
    # filters on what the posting says (known once it's read: they apply to the scored postings)
    work_models: list[str] = field(default_factory=list)  # onsite | hybrid | remote; empty = any
    seniorities: list[str] = field(default_factory=list)  # intern | entry | mid | senior | lead; empty = any
    company_sizes: list[str] = field(default_factory=list)  # startup | mid | large; empty = any
    skip: list[str] = field(default_factory=list)  # screens on top of your profile's: no_sponsor | citizen | clearance
    sponsor_only: bool = False  # only postings that offer sponsorship or whose company has sponsored H-1Bs
    exclude_companies: list[str] = field(default_factory=list)


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
        elif isinstance(default, bool):
            v = v is True
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
    h1b: int | None = None  # H-1B applications the company filed (DOL disclosure data), when loaded


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


# ---- source: remote job boards --------------------------------------------------------------------------------
# Public feeds only. Sites without one (FlexJobs, JustRemote and Virtual Vocations are paid; Arc, Wellfound, Remote.co,
# Skip the Drive, Pangian and Career Vault have no feed) and freelance marketplaces (Upwork, Fiverr, Toptal...) aren't
# searched. A listing links to the board's page: resolve_apply() finds the company's own application form behind it.
def _rss_items(c: httpx.Client, url: str) -> list[dict]:
    from email.utils import parsedate_to_datetime

    out = []
    for it in re.findall(r"<item>(.*?)</item>", c.get(url).text, re.S):
        def tag(t):
            m = re.search(rf"<{t}[^>]*>(.*?)</{t}>", it, re.S)
            return re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", m.group(1).strip(), flags=re.S) if m else ""
        try:
            posted = parsedate_to_datetime(tag("pubDate")).date().isoformat()
        except (TypeError, ValueError):
            posted = None
        out.append({"title": html.unescape(tag("title")), "link": tag("link"), "posted": posted, "region": tag("region"),
                    "creator": tag("dc:creator"), "body": tag("content:encoded") or tag("description")})
    return out


def _remote(loc: str) -> str:
    loc = re.sub(r"\s+", " ", html.unescape(loc or "")).strip(" ,")
    return loc if _REMOTE.search(loc) else ("Remote" + (f" ({loc})" if loc else ""))


def _remoteok(c):
    for x in c.get("https://remoteok.com/api").json()[1:]:  # [0] is the API's terms
        if x.get("url") and x.get("position"):
            yield Found(_key(x["url"]), x.get("company") or "", x["position"], _remote(x.get("location")), x["url"],
                        detect_ats(x["url"]), "Remote OK", _iso(x.get("date")), _text(x.get("description", "")))


def _wwr(c):
    for x in _rss_items(c, "https://weworkremotely.com/remote-jobs.rss"):
        company, _, title = x["title"].partition(": ")
        if title and x["link"]:
            yield Found(_key(x["link"]), company, title, _remote(x["region"]), x["link"], detect_ats(x["link"]),
                        "We Work Remotely", x["posted"], _text(x["body"]))


def _remotive(c):
    for x in c.get("https://remotive.com/api/remote-jobs").json().get("jobs", []):
        if x.get("url"):
            yield Found(_key(x["url"]), x.get("company_name") or "", x.get("title") or "",
                        _remote(x.get("candidate_required_location")), x["url"], detect_ats(x["url"]), "Remotive",
                        _iso(x.get("publication_date")), _text(x.get("description", "")))


def _workingnomads(c):
    for x in c.get("https://www.workingnomads.com/api/exposed_jobs/").json():
        if x.get("url"):
            yield Found(_key(x["url"]), x.get("company_name") or "", x.get("title") or "", _remote(x.get("location")),
                        x["url"], detect_ats(x["url"]), "Working Nomads", _iso(x.get("pub_date")),
                        _text(x.get("description", "")))


def _jobspresso(c):
    for x in _rss_items(c, "https://jobspresso.co/feed/?post_type=job_listing"):
        company, _, loc = html.unescape(x["creator"]).partition("<br>")
        if x["link"]:
            yield Found(_key(x["link"]), company.strip(), x["title"], _remote(re.sub(r"^[^\w]+", "", loc)), x["link"],
                        detect_ats(x["link"]), "Jobspresso", x["posted"], _text(x["body"]))


def _landing(c):
    for page in range(5):
        jobs = c.get("https://landing.jobs/api/v1/jobs", params={"limit": 50, "offset": page * 50}).json()
        for x in jobs:
            if x.get("remote") and x.get("url"):
                slug = re.search(r"/at/([\w-]+)/", x["url"])
                yield Found(_key(x["url"]), slug.group(1).replace("-", " ").title() if slug else "", x.get("title") or "",
                            "Remote", x["url"], detect_ats(x["url"]), "Landing.jobs", _iso(x.get("published_at")),
                            _text(" ".join(x.get(k) or "" for k in ("role_description", "main_requirements", "nice_to_have"))))
        if len(jobs) < 50:
            break


def _powertofly(c):
    for x in c.get("https://powertofly.com/jobs/rss").json().get("items", []):
        if x.get("link") and re.search(r"remote", f"{x.get('type')} {x.get('job_location')}", re.I):
            yield Found(_key(x["link"]), x.get("description") or "", x.get("title") or "", _remote(x.get("job_location")),
                        x["link"], detect_ats(x["link"]), "PowerToFly", _iso(x.get("published_on")))


REMOTE_FEEDS = {"Remote OK": _remoteok, "We Work Remotely": _wwr, "Remotive": _remotive,
                "Working Nomads": _workingnomads, "Jobspresso": _jobspresso, "Landing.jobs": _landing,
                "PowerToFly": _powertofly}


def from_remote(log) -> list[Found]:
    def one(item):
        name, feed = item
        try:
            with httpx.Client(timeout=30, follow_redirects=True, headers=UA) as c:
                return name, list(feed(c)), None
        except Exception as e:
            return name, [], type(e).__name__

    out = []
    with ThreadPoolExecutor(max_workers=len(REMOTE_FEEDS)) as ex:
        for name, jobs, err in ex.map(one, REMOTE_FEEDS.items()):
            log(f"  {name}: " + (f"couldn't load ({err})" if err else f"{len(jobs)} remote postings"))
            out += jobs
    return out


# a posting on an ATS the runner can fill (not a careers home page or an "automation notice")
_ATS_POSTING = re.compile(
    r"https?://(?:job-boards\.|boards\.)?greenhouse\.io/[\w-]+/jobs/\d+[^\s\"'<>]*|"
    r"https?://jobs\.lever\.co/[\w.-]+/[0-9a-f-]{36}[^\s\"'<>]*|"
    r"https?://jobs\.ashbyhq\.com/[\w.%-]+/[0-9a-f-]{36}[^\s\"'<>]*|"
    r"https?://apply\.workable\.com/[\w-]+/j/[0-9A-F]{6,}[^\s\"'<>]*|"
    r"https?://[\w-]+\.wd\d+\.myworkdayjobs\.com/[^\s\"'<>]*/job/[^\s\"'<>]+", re.I)


def resolve_apply(f: Found, c: httpx.Client) -> None:
    """A remote-board listing -> the company's own application form, when the board's page (or the redirect behind its
    link) leads to one the runner can fill."""
    try:
        r = c.get(f.url)
    except Exception:
        return
    for u in [str(r.url), *_ATS_POSTING.findall(r.text[:400_000])]:
        u = html.unescape(u).rstrip("/.\\")
        if detect_ats(u) in SUPPORTED and (u == str(r.url) or _ATS_POSTING.fullmatch(u)):
            f.url, f.ats, f.key = u, detect_ats(u), _key(u)
            return


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
    (r"\bsoftware development engineer\b|\bsde\b|\bswe\b", "software engineer"), (r"\bdeveloper\b|\bengineering\b", "engineer"),
    (r"\bfull[- ]stack\b", "fullstack"), (r"\bback[- ]end\b", "backend"), (r"\bfront[- ]end\b", "frontend"),
]
_INTERN = re.compile(r"\bintern(ship)?\b|\bco-?op\b", re.I)
_NEW_GRAD = re.compile(r"new ?grad|university|graduate|early[- ]career|entry[- ]level|\bjunior\b|\bjr\.?\b|\bassociate\b|"
                       r"campus|\b(20\d\d)\b|\b(i|1)\b\s*$|engineer i\b|rotational|residency", re.I)
_REMOTE = re.compile(r"remote|anywhere|distributed", re.I)
# past early career: "Software Engineer II", "Developer 3", "Mid-level" (job boards list every opening; the lists don't)
_MID = re.compile(r"\b(ii|iii|iv)\b|\b(engineer|developer|scientist|analyst)\s*[2-5]\b|\bmid[- ]?level\b|\bexperienced\b", re.I)
LISTS = ("New-grad list", "Internship list")  # SimplifyJobs: curated to early-career roles


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


# Title families (Jobright retrieves by a title taxonomy): a target title in a family also finds its other titles.
TITLE_FAMILIES = {
    "software": ["Software Engineer", "Software Developer", "Backend Engineer", "Frontend Engineer", "Fullstack Engineer",
                 "Application Developer", "Web Developer", "Mobile Engineer", "iOS Engineer", "Android Engineer",
                 "Product Engineer", "Member of Technical Staff", "Programmer"],
    "data": ["Data Engineer", "Data Analyst", "Analytics Engineer", "Business Intelligence Engineer", "BI Analyst",
             "Data Scientist"],
    "ml": ["ML Engineer", "AI Engineer", "Applied Scientist", "Research Engineer", "Deep Learning Engineer",
           "LLM Engineer", "NLP Engineer", "Computer Vision Engineer", "MLOps Engineer"],
    "infra": ["DevOps Engineer", "Site Reliability Engineer", "SRE", "Infrastructure Engineer", "Platform Engineer",
              "Cloud Engineer", "Systems Engineer"],
    "security": ["Security Engineer", "Application Security Engineer", "Cybersecurity Engineer", "Security Analyst"],
    "fde": ["Forward Deployed Engineer", "Solutions Engineer", "Customer Engineer", "Implementation Engineer"],
    "pm": ["Product Manager", "Technical Product Manager", "Associate Product Manager"],
    "qa": ["QA Engineer", "Test Engineer", "Software Development Engineer in Test", "SDET", "Quality Engineer"],
}


def expand_titles(targets: list[str]) -> list[str]:
    """The targets plus every title of each family one of them belongs to (equal words, any order)."""
    out = list(targets)
    for family in TITLE_FAMILIES.values():
        if any(set(_words(t)) == set(_words(f)) for t in targets for f in family):
            out += [f for f in family if f not in out]
    return out


def level_ok(f: Found, levels: list[str]) -> bool:
    if not levels:
        return True
    intern = bool(_INTERN.search(f.title)) or f.source == "Internship list"
    if intern:
        return "intern" in levels
    if f.source not in LISTS and _MID.search(f.title):
        return False
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
        # SimplifyJobs writes "SF", "NYC", "LA" with no state
        return bool(re.search(r",\s*(" + "|".join(US_STATES) + r")\b|\b(US|USA|SF|NYC|LA)\b", loc)
                    or re.search(r"united states|" + "|".join(re.escape(v) for v in US_STATES.values()), loc, re.I))
    return country.lower() in loc.lower() if country else True


def keep(f: Found, prefs: Prefs, country: str, state: str, active_screens: frozenset[str],
         titles: list[str] | None = None) -> str | None:
    """None = keep; else why it was filtered out (for the counts). titles: the targets with their families."""
    # the SimplifyJobs lists are curated by level, not field (hardware, analyst and marketing roles too): titles apply
    titles = titles if titles is not None else expand_titles(prefs.titles)
    if prefs.titles and not title_matches(f.title, titles):
        return "title"
    low = f" {f.title.lower()} ".replace("member of technical staff", "mts")  # a title, not "staff" seniority
    if any(re.search(rf"(?<![a-z]){re.escape(x.lower())}(?![a-z])", low) for x in prefs.exclude):
        return "excluded word"
    if any(_company(f.company) == _company(x) for x in prefs.exclude_companies):
        return "excluded company"
    if prefs.work_models and _REMOTE.search(f.location or "") and "remote" not in prefs.work_models \
            and not re.search(r"hybrid|on-?site|office", f.location, re.I):
        return "work model"
    if prefs.sponsor_only and f.sponsorship in ("Does Not Offer Sponsorship", "U.S. Citizenship is Required"):
        return "no sponsorship"
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


def freshness(age: int | None) -> float:
    """About 2.3 for a posting from today, halving its lead each week toward 1 (Jobright multiplies by the same idea)."""
    return 1 + 1.3 * 0.5 ** ((30 if age is None else max(0, age)) / 7)


def _company(name: str) -> str:
    return re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|labs)\b\.?|[^a-z0-9]+", "", (name or "").lower())


def rank(f: Found, prefs: Prefs, feedback: dict | None = None) -> float:
    """relevance x freshness x boost: a perfect match from last month doesn't outrank everything posted today."""
    if f.screened:
        return 0.0
    relevance = 1.0
    relevance += 1 if any(" ".join(_words(t)) == " ".join(_words(f.title)) for t in prefs.titles) else 0
    # early-career titles first when that's what the person is after
    if {"new_grad", "intern"} & set(prefs.levels) and (_NEW_GRAD.search(f.title) or _INTERN.search(f.title)
                                                        or f.source in ("New-grad list", "Internship list")):
        relevance += 1
    overall = ((f.match or {}).get("breakdown") or {}).get("pct", f.match_pct)
    if overall is not None:  # read and scored: the match decides most of it
        relevance *= 0.5 + overall
    boost = 1.5 if f.ats in SUPPORTED else 1.0  # Appli can fill it
    boost *= 3.0 if f.source in LISTS else 1.0  # SimplifyJobs postings first: really early-career, unlike many board openings
    boost *= 1.1 if f.sponsorship == "Offers Sponsorship" or (f.h1b or 0) > 0 else 1.0
    ignored = list((feedback or {}).get("ignored", {}).values())
    if any(_company(x.get("company", "")) == _company(f.company) for x in ignored):
        boost *= 0.5  # you said "not interested" to a job at this company
    if any(_words(x.get("title", "")) == _words(f.title) for x in ignored):
        boost *= 0.7
    return round(relevance * freshness(_age_days(f.posted)) * boost, 4)


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
    from .screening import screen

    def one(f: Found):
        with httpx.Client(timeout=15, follow_redirects=True, headers=UA) as c:
            if len(f.description) < 300:
                f.description = describe(f, c)[:8000]
        text = f.description
        if len(text) < 300:
            return f  # nothing to read: shown without a match %
        reason = screen(text, active)  # patterns only: no Jev while searching
        if reason:
            f.screened = reason
            return f
        try:
            info = select.evaluate({"company": f.company, "role": f.title, "description": text, "id": None}, use_jev=False)
        except Exception:
            info = None
        if info:
            f.match_pct, f.resume, f.match = info["pct"], info["chosen"], info
        return f

    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, f in enumerate(ex.map(one, items), 1):
            overall = ((f.match or {}).get("breakdown") or {}).get("pct", f.match_pct)
            what = (f"screened out: {f.screened[:70]}" if f.screened else
                    f"match {overall:.0%} ({config.RESUME_LABELS.get(f.resume, f.resume)})" if overall is not None
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


# ---- after reading a posting: the filters that need what it says ------------------------------------------------
def post_filter(f: Found, prefs: Prefs) -> str | None:
    """None = keep; else why. Only for scored postings: an unread one passes (nothing known yet)."""
    q = (f.match or {}).get("quals") or {}
    model = q.get("work_model") or ("remote" if _REMOTE.search(f.location or "") else None)
    if prefs.work_models and model and model not in prefs.work_models:
        return "work model"
    # a SimplifyJobs listing is curated early-career: trusted over the AI's read of the posting's level
    if prefs.seniorities and f.source not in LISTS and q.get("seniority") and q["seniority"] not in prefs.seniorities:
        return "seniority"
    # a board opening the posting itself pitches past early career, when that's all the person searches for
    if f.source not in LISTS and not prefs.seniorities and prefs.levels and set(prefs.levels) <= {"intern", "new_grad", "entry"} \
            and (q.get("seniority") in ("mid", "senior", "lead") or (q.get("min_yoe") or 0) >= 3):
        return "seniority"
    if prefs.company_sizes and q.get("company_size") and q["company_size"] not in prefs.company_sizes:
        return "company size"
    if prefs.sponsor_only and f.sponsorship != "Offers Sponsorship" and not (f.h1b or 0) > 0:
        return "no sponsorship"
    return None


# ---- your feedback: like / not interested / applied -------------------------------------------------------------
FEEDBACK = {"like": "liked", "ignore": "ignored", "apply": "applied"}


def set_feedback(key: str, action: str) -> dict:
    """Mark one result. Not interested hides it (and down-weights its company and title from then on); applied keeps
    it out of later searches; liked jobs are what "similar jobs" compares against. action "clear" undoes any of them."""
    if action not in (*FEEDBACK, "clear"):
        raise ValueError("Unknown action")
    found = load_found()
    r = next((x for x in found.get("results", []) if x["key"] == key), None)
    if r is None:
        raise ValueError("That result is gone: search again")
    fb = found.setdefault("feedback", {})
    for bucket in FEEDBACK.values():
        fb.setdefault(bucket, {}).pop(key, None)
    r["feedback"] = None
    if action != "clear":
        fb[FEEDBACK[action]][key] = {"company": r["company"], "title": r["title"], "url": r["url"],
                                     "terms": [t["term"] for t in (r.get("match") or {}).get("terms") or []]}
        r["feedback"] = FEEDBACK[action]
    save_found(found)
    return found


# ---- H-1B history (public DOL LCA disclosure files) -------------------------------------------------------------
H1B_PAGE = "https://www.dol.gov/agencies/eta/foreign-labor/performance"


def _h1b_file() -> Path:
    return config.HOME / "h1b.json"


def h1b_counts() -> dict[str, int]:
    try:
        return json.loads(_h1b_file().read_text(encoding="utf-8")).get("companies", {})
    except (OSError, ValueError, AttributeError):
        return {}


def load_h1b(source: str, log=print) -> int:
    """Read a DOL LCA disclosure file (.xlsx or .csv, a path or a link from H1B_PAGE) once: certified H-1B filings per
    employer go to users/<Name>/h1b.json. Returns how many employers. Loading another file adds to the counts."""
    import csv
    import io
    import tempfile

    if re.match(r"https?://", source):
        log(f"Downloading {source} (these files are large: a few minutes)...")
        tmp = Path(tempfile.mkdtemp()) / Path(source.split("?")[0]).name
        with httpx.stream("GET", source, timeout=600, follow_redirects=True, headers=UA) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        path = tmp
    else:
        path = Path(source)
    counts = h1b_counts()

    def tally(rows):
        header = [str(h or "").strip().upper() for h in next(rows)]
        col = {h: i for i, h in enumerate(header)}
        emp, status, visa = col.get("EMPLOYER_NAME"), col.get("CASE_STATUS"), col.get("VISA_CLASS")
        if emp is None:
            raise ValueError("Not an LCA disclosure file: no EMPLOYER_NAME column")
        for n, row in enumerate(rows, 1):
            if status is not None and not str(row[status] or "").lower().startswith("certified"):
                continue
            if visa is not None and str(row[visa] or "").upper() not in ("H-1B", ""):
                continue
            name = _company(str(row[emp] or ""))
            if name:
                counts[name] = counts.get(name, 0) + 1
            if n % 200_000 == 0:
                log(f"  {n:,} filings read")

    if path.suffix.lower() == ".csv":
        with io.open(path, encoding="utf-8", errors="replace", newline="") as fh:
            tally(csv.reader(fh))
    else:
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True)
        try:
            tally(wb.active.iter_rows(values_only=True))
        finally:
            wb.close()
    _h1b_file().write_text(json.dumps({"source": source, "companies": counts}), encoding="utf-8")
    return len(counts)


# ---- the daily digest: new strong matches for the dashboard's activity panel --------------------------------------
def digest(previous: dict, out: dict) -> dict:
    """Strong matches in this search that the last one didn't have."""
    seen = {r["key"] for r in previous.get("results", [])}
    new = [r for r in out["results"] if r["key"] not in seen and not r.get("screened")
           and ((r.get("match") or {}).get("breakdown") or {}).get("band") == "strong"]
    return {"at": out["searched_at"], "count": len(new),
            "jobs": [{"key": r["key"], "company": r["company"], "title": r["title"]} for r in new[:5]]}


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
    active = active_screens(profile.standard_answers) | frozenset(prefs.skip)
    previous = load_found()
    feedback = previous.get("feedback") or {}
    titles = expand_titles(prefs.titles)
    if len(titles) > len(prefs.titles):
        log(f"  with related titles: {', '.join(titles[len(prefs.titles):])}")
    log(f"Searching for: {', '.join(prefs.titles)} | levels {', '.join(prefs.levels) or 'any'} | "
        f"{', '.join(prefs.locations) or 'anywhere in ' + (profile.country or 'your country')} | last {prefs.days} days")

    found: list[Found] = []
    if "lists" in prefs.sources:
        found += from_lists(prefs.levels, log)
    if "boards" in prefs.sources:
        boards = company_boards(prefs.companies)
        log(f"  reading {len(boards)} company job boards...")
        found += from_boards(boards, prefs.titles, log)
    if "remote" in prefs.sources:
        found += from_remote(log)

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
        hidden = ("not interested" if f.key in feedback.get("ignored", {}) else
                  "applied" if f.key in feedback.get("applied", {}) else None)
        why = hidden or keep(f, prefs, profile.country, profile.state, active, titles)
        if why:
            counts[why] = counts.get(why, 0) + 1
            continue
        kept.append(f)
    # remote-board listings that made it through: find the company's own form behind each (ranks it as fillable)
    via_board = [f for f in kept if f.source in REMOTE_FEEDS]
    if via_board:
        log(f"  following {len(via_board)} remote-board listing(s) to the company's application form...")

        def follow(f):
            with httpx.Client(timeout=15, follow_redirects=True, headers=UA) as c:
                resolve_apply(f, c)

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(follow, via_board))
        n = sum(f.ats in SUPPORTED for f in via_board)
        log(f"  {n} of them lead to a form Appli can fill")
        before = len(kept)
        kept = [f for f in kept if not (f.source in REMOTE_FEEDS and f.key in have)]
        if len(kept) < before:
            counts["already in your jobs"] = counts.get("already in your jobs", 0) + before - len(kept)
        uniq: dict[str, Found] = {}
        for f in kept:  # the same posting listed on two boards (or on a board and a company's own board)
            uniq.setdefault(f.key, f)
        kept = list(uniq.values())
    h1b = h1b_counts()
    for f in kept:
        f.h1b = h1b.get(_company(f.company)) if h1b else None
        f.rank = rank(f, prefs, feedback)
    kept.sort(key=lambda f: (-f.rank, f.company))
    counts["matched"] = len(kept)
    log("Filtered: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    top = kept[: prefs.score_top]
    log(f"Reading, screening and scoring the top {len(top)}...")
    score(top, active, log)
    scored = []
    for f in top:
        why = None if f.screened else post_filter(f, prefs)
        if why:
            counts[why] = counts.get(why, 0) + 1
            continue
        f.rank = rank(f, prefs, feedback)  # now with the match
        scored.append(f)
    results = sorted(scored, key=lambda f: -f.rank) + kept[prefs.score_top:]
    liked = feedback.get("liked", {})
    out = {
        "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prefs": asdict(prefs),
        "counts": counts,
        "feedback": feedback,
        "h1b_loaded": bool(h1b),
        "results": [{**{k: v for k, v in asdict(f).items() if k not in ("description", "match")},
                     "fillable": f.ats in SUPPORTED, "scored": i < len(scored), "added": False,
                     "feedback": "liked" if f.key in liked else None, "match": f.match} for i, f in enumerate(results)],
    }
    out["digest"] = digest(previous, out)
    save_found(out)
    log(f"Done: {len(results)} relevant posting(s); top {len(scored)} scored"
        + (f", {out['digest']['count']} new strong match(es)" if out["digest"]["count"] else "") + ". Open Find jobs to pick.")
    return out


SIMPLIFY_CATEGORIES = ("software", "ai/ml")  # Simplify's "Software" and "AI/ML/Data" sections


def simplify_picks(found: list[Found], have: set[str], days: int = 7) -> list[dict]:
    """The startup pull: new-grad list postings from the last `days` days in the software / AI sections, not yet in
    your jobs. No title / location screening: the list is already curated, and a missed opening costs more."""
    out, seen = [], set(have)
    for f in found:
        age = _age_days(f.posted)
        if f.key in seen or age is None or age > days or not f.category.lower().startswith(SIMPLIFY_CATEGORIES):
            continue
        seen.add(f.key)
        out.append(asdict(f))
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
