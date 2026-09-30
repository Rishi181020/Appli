"""Where the application form lives for each supported ATS.

Extraction and filling are generic (runner/forms.py); adapters only know how to get from the
posting URL to the form. Nothing here ever submits an application.
"""
import re

from playwright.sync_api import Page


def _base(url: str) -> str:
    return url.split("?")[0].split("#")[0].rstrip("/")


def urls(ats: str, url: str) -> tuple[str, str | None]:
    """(posting_url, form_url). form_url None means 'click through from the posting page'."""
    if ats == "greenhouse":
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url) or re.search(
            r"greenhouse\.io/([\w-]+)/jobs/(\d+)", url
        )
        if m:
            canon = f"https://job-boards.greenhouse.io/{m.group(1)}/jobs/{m.group(2)}"
            return canon, canon
        return url, url
    if ats == "lever":
        base = re.sub(r"/apply$", "", _base(url))
        return base, base + "/apply"
    if ats == "ashby":
        base = re.sub(r"/application$", "", _base(url))
        return base, base + "/application"
    if ats == "workable":
        base = re.sub(r"/apply$", "", _base(url))
        return base + "/", base + "/apply/"
    return url, None  # smartrecruiters and anything else: click through


def click_through(page: Page, ats: str) -> bool:
    """For ATSs whose form isn't at a predictable URL: follow the Apply / I'm interested link."""
    label = re.compile(r"i.?m interested|apply( now| for this job)?$", re.I)
    for role in ("link", "button"):
        loc = page.get_by_role(role, name=label)
        if loc.count():
            first = loc.first
            href = first.get_attribute("href") if role == "link" else None
            if href and href.startswith("http"):
                page.goto(href, wait_until="domcontentloaded", timeout=30000)
            else:
                first.click(timeout=5000)
            return True
    return False
