"""Jobs from pasted links: company from the URL / page, role from the page title (plain HTTP, no browser)."""
import html
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

from .ats import detect_ats
from .sheet import JobRow, company_from_url

_URL = re.compile(r"https?://[^\s<>\"',;]+", re.I)
_GENERIC = re.compile(r"^(jobs?|careers?|apply|home|job board|job application|loading.*|)$", re.I)


def _title(url: str) -> str:
    try:
        r = httpx.get(url, timeout=8, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"})
        page = r.text[:200_000]
    except Exception:
        return ""
    for pat in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, page, re.I | re.S)
        if m:
            return re.sub(r"\s+", " ", html.unescape(m.group(1))).strip()
    return ""


def split_title(title: str, company: str) -> tuple[str | None, str]:
    """-> (role, company). Handles 'Job Application for X at Y', 'X at Y', 'Y - X', 'X | Y'."""
    t = title.strip()
    m = re.match(r"(?:job application for\s+)(.+?)\s+at\s+(.+)$", t, re.I) or re.match(r"(.+?)\s+at\s+(.+)$", t, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—|@]\s+", t) if p.strip()]
    if len(parts) >= 2:
        low = company.lower()
        if parts[0].lower() == low or low in parts[0].lower():
            return " - ".join(parts[1:]), parts[0]
        if parts[-1].lower() == low or low in parts[-1].lower():
            return " - ".join(parts[:-1]), parts[-1]
        return parts[0], company
    return (None if _GENERIC.match(t) else t or None), company


def from_links(text: str) -> list[JobRow]:
    urls = list(dict.fromkeys(u.rstrip(").]") for u in _URL.findall(text or "")))[:200]
    with ThreadPoolExecutor(max_workers=8) as ex:
        titles = list(ex.map(_title, urls))
    rows = []
    for url, title in zip(urls, titles):
        company = company_from_url(url)
        role, company = split_title(title, company) if title else (None, company)
        rows.append(JobRow(dedupe_key=url, company=company, role=role, location=None, salary=None, url=url,
                           source="added in dashboard", fit_tier=None, days_posted=None, ats=detect_ats(url)))
    return rows
