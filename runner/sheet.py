"""Turn an uploaded job list (CSV or xlsx) into job rows.

Columns are recognised from their headers (company / role / url / location / tier ...); the dashboard shows the
guess and lets the person fix it before importing. In an xlsx, a URL cell that is a hyperlink ("Apply") is read
from the link target; plain-text URLs work too.
"""
import csv
import io
import re
from dataclasses import dataclass

import openpyxl

from .ats import detect_ats


@dataclass
class JobRow:
    dedupe_key: str
    company: str
    role: str | None
    location: str | None
    salary: str | None
    url: str | None
    source: str | None
    fit_tier: int | None
    days_posted: int | None
    ats: str


FIELDS = ["company", "role", "url", "location", "tier", "days_posted", "salary", "source"]
REQUIRED = ["company", "url"]
# header words that identify each field (first match wins, checked in this order)
_SYNONYMS = {
    "url": ["apply", "url", "link", "application", "posting"],
    "company": ["company", "employer", "organization", "organisation", "firm"],
    "role": ["role", "title", "position", "job"],
    "location": ["location", "city", "office", "where"],
    "tier": ["tier", "fit", "priority", "rank"],
    "days_posted": ["days since", "days posted", "age", "posted"],
    "salary": ["salary", "pay", "compensation"],
    "source": ["source"],
}


def _tier(value) -> int | None:
    m = re.match(r"\s*(\d)", str(value or ""))
    return int(m.group(1)) if m else None


def _int(value) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    m = re.match(r"\s*(\d+)", str(value or ""))
    return int(m.group(1)) if m else None


def _text(value) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s or None


def _url(value) -> str | None:
    s = _text(value)
    return s if s and re.match(r"https?://", s, re.I) else None


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """field -> header, from header names."""
    out: dict[str, str] = {}
    used: set[str] = set()
    for fld in ("url", "company", "role", "location", "tier", "days_posted", "salary", "source"):
        for word in _SYNONYMS[fld]:
            h = next((h for h in headers if h not in used and word in h.lower()), None)
            if h:
                out[fld] = h
                used.add(h)
                break
    return out


def _table_xlsx(data: bytes, sheet: str | None) -> tuple[list[str], list[str], list[list]]:
    """-> (sheet names, headers, rows). URL-ish cells keep their hyperlink target."""
    # Not read_only: hyperlink targets are unavailable in read-only mode.
    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
    headers = [str(c.value).strip() if c.value is not None else "" for c in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2):
        rows.append([(c.hyperlink.target if c.hyperlink and c.hyperlink.target else c.value) for c in row])
    return wb.sheetnames, headers, rows


def _table_csv(data: bytes) -> tuple[list[str], list[list]]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    all_rows = [r for r in reader]
    if not all_rows:
        return [], []
    return [h.strip() for h in all_rows[0]], all_rows[1:]


def read_table(data: bytes, filename: str, mapping: dict[str, str] | None = None, sheet: str | None = None) -> dict:
    """Parse an uploaded file. -> {sheets, sheet, headers, mapping, sample, rows: [JobRow], problems}"""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        sheets, headers, table = _table_xlsx(data, sheet)
        sheet = sheet if sheet in sheets else sheets[0]
    else:
        sheets, (headers, table) = [], _table_csv(data)
    mapping = {k: v for k, v in (mapping or guess_mapping(headers)).items() if v in headers}
    idx = {h: i for i, h in enumerate(headers)}
    jobs: list[JobRow] = []
    seen: set[str] = set()
    for raw in table:
        rec = {fld: (raw[idx[h]] if idx[h] < len(raw) else None) for fld, h in mapping.items()}
        company, url = _text(rec.get("company")), _url(rec.get("url"))
        if not company and url:
            company = company_from_url(url)
        if not company:  # blank trailing rows
            continue
        role, location = _text(rec.get("role")), _text(rec.get("location"))
        key = url or f"{company}|{role}|{location}"
        if key in seen:
            continue
        seen.add(key)
        jobs.append(JobRow(
            dedupe_key=key, company=company, role=role, location=location, salary=_text(rec.get("salary")),
            url=url, source=_text(rec.get("source")), fit_tier=_tier(rec.get("tier")),
            days_posted=_int(rec.get("days_posted")), ats=detect_ats(url),
        ))
    problems = [f"Pick the column for {f}" for f in REQUIRED if f not in mapping]
    return {"sheets": sheets, "sheet": sheet, "headers": headers, "mapping": mapping, "rows": jobs, "problems": problems}


# ---- company name from a job link ----------------------------------------------------------------
_HOSTED = [  # ATS hosts where the company is the first path segment or the subdomain
    (r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)", 1),
    (r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)", 1),
    (r"jobs\.ashbyhq\.com/([\w.%-]+)", 1),
    (r"apply\.workable\.com/([\w-]+)", 1),
    (r"([\w-]+)\.(?:wd\d+\.)?myworkdayjobs\.com", 1),
    (r"([\w-]+)\.bamboohr\.com", 1),
    (r"([\w-]+)\.recruitee\.com", 1),
    (r"jobs\.smartrecruiters\.com/([\w-]+)", 1),
    (r"([\w-]+)\.breezy\.hr", 1),
    (r"([\w-]+)\.icims\.com", 1),
]


def company_from_url(url: str) -> str:
    for pat, g in _HOSTED:
        m = re.search(pat, url, re.I)
        if m:
            slug = re.sub(r"%20|[-_.]+", " ", m.group(g)).strip()
            return slug.title() if slug.islower() else slug
    m = re.match(r"https?://(?:www\.|careers\.|jobs\.)?([\w-]+)\.", url, re.I)
    return m.group(1).title() if m else "Unknown company"
