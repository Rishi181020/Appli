from urllib.parse import urlparse

# host suffix -> ats name. Order matters: first match wins.
_HOSTS = [
    ("greenhouse.io", "greenhouse"),
    ("ashbyhq.com", "ashby"),
    ("lever.co", "lever"),
    ("workable.com", "workable"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("myworkdayjobs.com", "workday"),
    ("icims.com", "icims"),
    ("oraclecloud.com", "oracle"),
    ("paylocity.com", "paylocity"),
]

# ATSs with an adapter. Everything else becomes needs_manual in v1.
SUPPORTED = {"greenhouse", "ashby", "lever", "workable"}


def detect_ats(url: str | None) -> str:
    if not url:
        return "none"
    host = (urlparse(url).netloc or "").lower()
    for suffix, name in _HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return name
    return "other"
