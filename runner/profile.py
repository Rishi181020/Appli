import re
from dataclasses import dataclass, field
from pathlib import Path

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
_ABBR = {m[:3].lower(): i + 1 for i, m in enumerate(MONTHS)}


@dataclass
class Education:
    school: str
    degree: str  # e.g. "Master of Science"
    field: str  # e.g. "Computer Science and Engineering"
    start_year: int
    start_month: int
    end_year: int
    end_month: int
    gpa: str | None = None

    @property
    def level(self) -> str:
        d = self.degree.lower()
        if "master" in d:
            return "Master's Degree"
        if "bachelor" in d:
            return "Bachelor's Degree"
        if "doctor" in d or "ph" in d:
            return "Doctor of Philosophy (Ph.D.)"
        if "associate" in d:
            return "Associate's Degree"
        return self.degree


@dataclass
class Profile:
    text: str  # full markdown; the only source of truth sent to the LLM
    full_name: str
    first_name: str
    last_name: str
    email: str
    phone: str
    city: str
    linkedin: str
    github: str
    standard_answers: dict[str, str] = field(default_factory=dict)
    education: list[Education] = field(default_factory=list)  # most recent first
    country: str = ""  # e.g. "United States" (asked in the profile; else read from "City, ST")
    state: str = ""  # state / province, e.g. "California"


def _profile_education(text: str) -> list[Education]:
    pattern = re.compile(
        r"\*\*(?P<school>[^*\n]+)\*\*\s+[—-]\s+(?P<degree>[^,\n]+),\s*(?P<field>[^\n]+)\n"
        r"\s*(?P<sy>\d{4})-(?P<sm>\d{2})\s+to\s+(?P<ey>\d{4})-(?P<em>\d{2})(?:\s*·\s*GPA\s*(?P<gpa>[\d.]+))?"
    )
    return [
        Education(m["school"].strip(), m["degree"].strip(), m["field"].strip(), int(m["sy"]), int(m["sm"]),
                  int(m["ey"]), int(m["em"]), m["gpa"])
        for m in pattern.finditer(text)
    ]


def _resume_schools(tex_path: Path) -> list[tuple[str, int]]:
    """(official school name, graduation year) for each entry of the resume's Education section."""
    from .resume import tex

    try:
        src = tex_path.read_text(encoding="utf-8")
    except OSError:
        return []
    a = src.find("\\section{Education}")
    b = src.find("\\resumeSubHeadingListEnd", a) if a != -1 else -1
    if a == -1 or b == -1:
        return []
    block, out, pos = src[a:b], [], 0
    for m in re.finditer(r"\\resumeSubheading\b", block):
        groups, idx = [], m.end()
        for _ in range(4):
            o = block.find("{", idx)
            c = tex._match_brace(block, o) if o != -1 else -1
            if c == -1:
                break
            groups.append(block[o + 1 : c])
            idx = c + 1
        if len(groups) == 4:
            years = re.findall(r"\d{4}", groups[3])
            if years:
                out.append((tex.plain(groups[0]), int(years[-1])))
    return out


def load_profile(path: Path, resume_tex: Path | None = None) -> Profile:
    if not path.exists():
        raise SystemExit(f"No profile at {path}. Finish your profile in the dashboard (Profile tab) first.")
    text = path.read_text(encoding="utf-8")

    def find(pattern: str, group: int = 0, flags=0) -> str:
        m = re.search(pattern, text, flags)
        return m.group(group).strip() if m else ""

    name = find(r"^#\s+(.+)$", 1, re.M)
    if not name:
        raise SystemExit(f"{path}: the first line must be '# Your Name'")
    first, _, last = name.partition(" ")
    email = find(r"[\w.+-]+@[\w-]+\.[\w.]+")
    phone = find(r"\+\d[\d ()-]{8,}\d")
    city = find(r"^([A-Za-z .'-]+,\s*[A-Z]{2})\s*·", 1, re.M)
    linkedin = find(r"LinkedIn:\s*(\S+)", 1)
    github = find(r"GitHub:\s*(\S+)", 1)
    country = find(r"^Country:\s*(.+)$", 1, re.M)
    state = find(r"^State(?:/Province)?:\s*(.+)$", 1, re.M)
    if not (country and state):  # older profiles: "Santa Clara, CA" means California, United States
        from .places import from_location

        st, co = from_location(city)
        country, state = country or co, state or st
    answers: dict[str, str] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 2 and set(cells[0]) - set("-"):
            if cells[0] != "Question":
                answers[cells[0]] = cells[1]
    education = _profile_education(text)
    # The resume carries each school's official name (a profile export can garble it, e.g. a club name);
    # match by graduation year and prefer the resume's spelling.
    if resume_tex is not None:
        official = {year: school for school, year in _resume_schools(resume_tex)}
        for e in education:
            e.school = official.get(e.end_year, e.school)
    return Profile(text, name, first, last, email, phone, city, linkedin, github, answers, education, country, state)
