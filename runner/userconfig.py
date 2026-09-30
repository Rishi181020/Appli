"""Per-person settings: everything that differs between the people who use this app.

Each person gets one git-ignored folder, created by the dashboard's onboarding (never edited by hand):
    users/<Full Name>/appli.json                          what's in the folder (written by the app)
    users/<Full Name>/profile.md                          the profile every model call uses
    users/<Full Name>/<First> Resume <Label>/             one folder per resume: the PDF (+ main.tex = tailoring)
    users/<Full Name>/Cover letter/                       optional: the style sample for generated cover letters
    users/<Full Name>/tailored/<job id>/                  tailored resumes
The folder is found by the Supabase user id stored in appli.json, so several people can share one computer.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USERS = ROOT / "users"


@dataclass
class ResumeCfg:
    key: str
    label: str
    pdf: Path
    tex: Path | None = None
    focus: str = ""
    title_keywords: list[str] = field(default_factory=list)


@dataclass
class PersonCfg:
    # Screening defaults; answers in the person's profile (sponsorship / citizenship / clearance) override these.
    needs_sponsorship: bool = False
    us_person: bool = True
    skip_clearance_jobs: bool = False


@dataclass
class UserConfig:
    folder: Path
    owner_id: str
    email: str
    name: str
    resumes: list[ResumeCfg]
    cover_letter: Path | None = None
    person: PersonCfg = field(default_factory=PersonCfg)

    @property
    def profile_path(self) -> Path:
        return self.folder / "profile.md"

    @property
    def tailored_dir(self) -> Path:
        return self.folder / "tailored"


def _rel(p: Path | None, base: Path) -> str:
    if p is None:
        return ""
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.as_posix()


def _abs(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else base / p


def folder_for(owner_id: str) -> Path | None:
    """The folder whose appli.json belongs to this Supabase user, if this computer has one."""
    if not owner_id or not USERS.exists():
        return None
    for f in USERS.glob("*/appli.json"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("owner_id") == owner_id:
                return f.parent
        except (OSError, json.JSONDecodeError):
            continue
    return None


def load(owner_id: str) -> UserConfig | None:
    folder = folder_for(owner_id)
    if folder is None:
        return None
    d = json.loads((folder / "appli.json").read_text(encoding="utf-8"))
    resumes = [
        ResumeCfg(r["key"], r.get("label") or r["key"].upper(), _abs(folder, r["pdf"]), _abs(folder, r.get("tex")),
                  r.get("focus", ""), list(r.get("title_keywords", [])))
        for r in d.get("resumes", []) if r.get("key") and r.get("pdf")
    ]
    p = d.get("person") or {}
    person = PersonCfg(bool(p.get("needs_sponsorship", False)), bool(p.get("us_person", True)),
                       bool(p.get("skip_clearance_jobs", False)))
    return UserConfig(folder, d["owner_id"], d.get("email", ""), d.get("name", folder.name), resumes,
                      _abs(folder, d.get("cover_letter")), person)


def save(cfg: UserConfig):
    f = cfg.folder
    f.mkdir(parents=True, exist_ok=True)
    data = {
        "owner_id": cfg.owner_id, "email": cfg.email, "name": cfg.name,
        "resumes": [{"key": r.key, "label": r.label, "pdf": _rel(r.pdf, f), "tex": _rel(r.tex, f), "focus": r.focus,
                     "title_keywords": r.title_keywords} for r in cfg.resumes],
        "cover_letter": _rel(cfg.cover_letter, f),
        "person": vars(cfg.person),
    }
    (f / "appli.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
