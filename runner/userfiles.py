"""The file side of onboarding: each person's folder in users/<Full Name>/, filled from the dashboard.

    users/<Full Name>/appli.json                      written here, never by hand
    users/<Full Name>/profile.md                      from the dashboard's Profile (the text every model reads)
    users/<Full Name>/<First> Resume <Label>/         <First> Resume <Label>.pdf (+ main.tex = tailoring)
    users/<Full Name>/Cover letter/                   optional style sample for generated cover letters
    users/<Full Name>/tailored/<job id>/              tailored resumes
"""
import io
import os
import re
import shutil
import zipfile
from pathlib import Path

from . import userconfig
from .userconfig import ROOT, USERS, PersonCfg, ResumeCfg, UserConfig

MAX_UPLOAD = 10 * 1024 * 1024
COVER_TYPES = (".pdf", ".docx", ".tex", ".txt", ".md")


class FileError(ValueError):
    pass


def _safe(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name or "")).strip(" .")


def first_name(cfg: UserConfig) -> str:
    return (cfg.name.split() or ["My"])[0]


def status(owner_id: str) -> dict:
    """What this computer has for the person (for the dashboard)."""
    cfg = userconfig.load(owner_id)
    if cfg is None:
        return {"folder": None, "resumes": [], "cover_letter": None, "profile_md": False,
                "workday": {"email": "", "has_password": False}}
    return {
        "folder": cfg.folder.relative_to(ROOT).as_posix(),
        "name": cfg.name,
        "resumes": [{"key": r.key, "label": r.label, "pdf": r.pdf.name, "pdf_ok": r.pdf.exists(),
                     "tex": bool(r.tex and r.tex.exists()), "focus": r.focus} for r in cfg.resumes],
        "cover_letter": cfg.cover_letter.name if cfg.cover_letter and cfg.cover_letter.exists() else None,
        "profile_md": cfg.profile_path.exists(),
        "workday": workday_status(cfg),
    }


# ---- Workday login: one email + password for every company's Workday site (stored only in this folder) ------------
def workday_status(cfg: UserConfig) -> dict:
    import json

    f = cfg.folder / "workday.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (OSError, ValueError):
        d = {}
    return {"email": d.get("email", ""), "has_password": bool(d.get("password"))}


def save_workday(owner_id: str, email: str, password: str | None):
    from . import config, workday

    cfg = _need(owner_id)
    if not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.]+", (email or "").strip()):
        raise FileError("Enter the email you use for Workday")
    if not password and not workday_status(cfg)["has_password"]:
        raise FileError("Enter the password you use for Workday")
    config.activate(owner_id)
    workday.save_credentials(email, password or None)


def create(owner_id: str, email: str, name: str) -> UserConfig:
    """Make (or rename) the person's folder: users/<Full Name>/."""
    name = _safe(name)
    if not name:
        raise FileError("Enter your name")
    cfg = userconfig.load(owner_id)
    if cfg and cfg.name == name:
        return cfg
    target = USERS / name
    n = 2
    while target.exists() and (cfg is None or target != cfg.folder):
        target = USERS / f"{name} ({n})"  # someone else on this computer has the same name
        n += 1
    if cfg is None:
        cfg = UserConfig(target, owner_id, email, name, [])
    else:  # renamed: move the folder, keep the files
        old = cfg.folder
        shutil.move(str(old), str(target))
        cfg.resumes = [ResumeCfg(r.key, r.label, target / r.pdf.relative_to(old),
                                 target / r.tex.relative_to(old) if r.tex else None, r.focus, r.title_keywords)
                       for r in cfg.resumes]
        cfg.cover_letter = target / cfg.cover_letter.relative_to(old) if cfg.cover_letter else None
        cfg.folder, cfg.name = target, name
    cfg.email = email or cfg.email
    userconfig.save(cfg)
    return cfg


def _need(owner_id: str) -> UserConfig:
    cfg = userconfig.load(owner_id)
    if cfg is None:
        raise FileError("Start with your name first")
    return cfg


def _key(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "resume"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}{n}", n + 1
    return key


def save_resume(owner_id: str, label: str, pdf: bytes | None, tex: bytes | None = None, focus: str | None = None,
                key: str | None = None) -> ResumeCfg:
    """Add a resume, or replace/update one (same key). The PDF is what gets uploaded; main.tex enables tailoring."""
    cfg = _need(owner_id)
    label = _safe(label) or "General"
    existing = next((r for r in cfg.resumes if r.key == key), None) if key else None
    if existing is None and not pdf:
        raise FileError("Add the resume PDF")
    for blob, what in ((pdf, "PDF"), (tex, ".tex")):
        if blob and len(blob) > MAX_UPLOAD:
            raise FileError(f"The {what} is over 10 MB")
    if pdf and not pdf.startswith(b"%PDF"):
        raise FileError("That file isn't a PDF")
    if tex and b"\\begin{document}" not in tex:
        raise FileError("That .tex file has no \\begin{document}: upload the resume's main LaTeX file")

    stem = f"{first_name(cfg)} Resume {label}"
    folder = cfg.folder / stem
    if existing and existing.pdf.parent != folder:  # relabelled: rename its folder
        old = existing.pdf.parent
        if old.exists() and not folder.exists():
            shutil.move(str(old), str(folder))
            existing.pdf = folder / existing.pdf.name
            if existing.tex:
                existing.tex = folder / existing.tex.name
    folder.mkdir(parents=True, exist_ok=True)
    r = existing or ResumeCfg(_key(label, {x.key for x in cfg.resumes}), label, folder / f"{stem}.pdf")
    r.label = label
    if pdf:
        for old in folder.glob("*.pdf"):
            old.unlink()
        r.pdf = folder / f"{stem}.pdf"
        r.pdf.write_bytes(pdf)
    elif existing and r.pdf.name != f"{stem}.pdf" and r.pdf.exists():
        r.pdf = r.pdf.rename(folder / f"{stem}.pdf")
    if tex:
        r.tex = folder / "main.tex"
        r.tex.write_bytes(tex)
    if focus is not None:
        r.focus = focus.strip()
    elif not r.focus:
        r.focus = draft_focus(r.pdf)
    if existing is None:
        cfg.resumes.append(r)
    userconfig.save(cfg)
    return r


def remove_tex(owner_id: str, key: str):
    cfg = _need(owner_id)
    for r in cfg.resumes:
        if r.key == key and r.tex:
            r.tex.unlink(missing_ok=True)
            r.tex = None
    userconfig.save(cfg)


def remove_resume(owner_id: str, key: str):
    cfg = _need(owner_id)
    r = next((x for x in cfg.resumes if x.key == key), None)
    if r is None:
        return
    cfg.resumes.remove(r)
    folder = r.pdf.parent
    if cfg.folder in folder.parents and folder != cfg.folder:  # only ever delete inside this person's folder
        shutil.rmtree(folder, ignore_errors=True)
    userconfig.save(cfg)


def save_cover(owner_id: str, filename: str, data: bytes):
    cfg = _need(owner_id)
    ext = Path(filename).suffix.lower()
    if ext not in COVER_TYPES:
        raise FileError(f"Cover letter must be one of: {', '.join(COVER_TYPES)}")
    if len(data) > MAX_UPLOAD:
        raise FileError("The cover letter is over 10 MB")
    folder = cfg.folder / "Cover letter"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        if old.is_file():
            old.unlink()
    out = folder / ("main.tex" if ext == ".tex" else f"{first_name(cfg)} Cover Letter{ext}")
    out.write_bytes(data)
    cfg.cover_letter = out
    userconfig.save(cfg)
    if not cover_text(out):
        raise FileError("Saved, but no text could be read from it: a text-based PDF, docx, tex or txt works best")


def remove_cover(owner_id: str):
    cfg = _need(owner_id)
    if cfg.cover_letter:
        folder = cfg.cover_letter.parent
        if cfg.folder in folder.parents:
            shutil.rmtree(folder, ignore_errors=True)
    cfg.cover_letter = None
    userconfig.save(cfg)


def cover_text(path: Path | None) -> str:
    """Plain text of the person's own cover letter (used as the voice/style sample), or ''."""
    if not path or not path.exists():
        return ""
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from .cover_letter import resume_text

            text = resume_text(str(path))
        elif ext == ".docx":
            with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
        elif ext == ".tex":
            src = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", src, re.S)
            src = re.sub(r"(?<!\\)%.*", "", m.group(1) if m else src)
            src = re.sub(r"\\\\|\\par\b|\\newline\b", "\n", src)
            src = re.sub(r"\\(vspace|hspace)\*?\{[^}]*\}|\\(makelettertitle|makeletterclosing|closing|opening)\b", "", src)
            text = re.sub(r"[{}]", "", re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", "", src))
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()[:5000]


def draft_focus(pdf: Path) -> str:
    """One line on what this resume is aimed at (Jev uses it to pick a resume). Editable in the dashboard."""
    try:
        from . import config, llm
        from .cover_letter import resume_text

        return llm.chat_text(
            "In one line of at most 18 words, say what kind of role this resume targets and its main technologies. "
            "No preamble, no quotes. Example: Backend and full-stack engineering: FastAPI, React, TypeScript, SQL.",
            resume_text(str(pdf))[:6000], model=config.model("fast"), max_tokens=300, purpose="resume focus",
        ).strip().strip('"').splitlines()[0][:200]
    except Exception:
        return ""


# ---- the original single-user layout (Rishi's files in the project root) --------------------------
LEGACY_RESUMES = {
    "swe": ("Rishi Resume SWE", "SWE",
            "General software engineering: backend and full-stack (FastAPI, React/Angular, TypeScript, SQL, REST APIs)."),
    "ai": ("Rishi Resume AI Eng", "AI Eng",
           "AI / ML engineering: RAG and GraphRAG, LLM applications, embeddings, model serving, PyTorch."),
    "fde": ("Rishi Resume FDE", "FDE",
            "Forward-deployed / customer-facing engineering: shipping product with customers, integrations, owning issues end to end."),
}
LEGACY_TITLE_KEYWORDS = {
    "fde": ["forward deployed", "forward-deployed", "deployment strategist", "solutions engineer", "solutions architect",
            "customer engineer", "field engineer", "implementation engineer", "sales engineer", "technical consultant"],
    "ai": ["ai", "ml", "llm", "genai", "nlp", "machine learning", "deep learning", "data scientist", "applied scientist",
           "research engineer", "research scientist", "artificial intelligence", "computer vision", "agentic", "agent",
           "gen ai", "a.i."],
}
LEGACY_PROFILE = ROOT / os.getenv("PROFILE_PATH", "jobright_profile.md")


def _root_dir(name: str) -> Path | None:
    return next((p for p in ROOT.iterdir() if p.is_dir() and p.name.lower() == name.lower()), None)


def legacy_found() -> dict | None:
    """The files of the original single-user setup, if they're still in the project root."""
    resumes = [label for folder, label, _ in LEGACY_RESUMES.values() if _root_dir(folder)]
    if not (LEGACY_PROFILE.exists() and resumes):
        return None
    return {"profile": LEGACY_PROFILE.name, "resumes": resumes, "cover_letter": bool(_root_dir("Cover letter"))}


def adopt_legacy(owner_id: str, email: str) -> dict:
    """COPY the root-level profile, resumes, cover letter, tailored resumes and browser profile into users/<Name>/.
    The originals are left where they are. Returns {folder, copied, profile_data}."""
    from . import profile_md

    if not legacy_found():
        raise FileError("No existing files found in the project folder")
    md = LEGACY_PROFILE.read_text(encoding="utf-8")
    data = profile_md.parse(md)
    cfg = create(owner_id, email, data.get("name") or "Rishi Dixit")
    copied = []

    def copy(src: Path | None, dst: Path):
        if src and src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
                copied.append(dst.relative_to(cfg.folder).as_posix())
            except (OSError, shutil.Error) as e:
                copied.append(f"(skipped {src.name}: {str(e)[:80]})")

    copy(LEGACY_PROFILE, cfg.profile_path)
    resumes = []
    for key, (folder, label, focus) in LEGACY_RESUMES.items():
        src = _root_dir(folder)
        if src is None:
            continue
        dst = cfg.folder / folder
        copy(src, dst)
        pdf = next(iter(sorted(dst.glob("*.pdf"))), None)
        if pdf is None:
            continue
        tex = dst / "main.tex"
        resumes.append(ResumeCfg(key, label, pdf, tex if tex.exists() else None, focus, LEGACY_TITLE_KEYWORDS.get(key, [])))
    cfg.resumes = resumes or cfg.resumes
    cover = _root_dir("Cover letter")
    if cover:
        copy(cover, cfg.folder / "Cover letter")
        cfg.cover_letter = next(iter(sorted((cfg.folder / "Cover letter").glob("*.tex"))), None) or \
            next(iter(sorted((cfg.folder / "Cover letter").glob("*.pdf"))), None)
    copy(ROOT / "resumes" / "tailored", cfg.tailored_dir)
    copy(ROOT / ".browser-profile", cfg.folder / ".browser-profile")  # keeps your site logins/cookies
    cfg.person = PersonCfg(needs_sponsorship=True, us_person=False, skip_clearance_jobs=True)
    userconfig.save(cfg)
    # the profile row keeps your hand-written file word for word, so runs read exactly what they read before;
    # the first save from the Profile tab switches it to the generated layout
    return {"folder": cfg.folder.relative_to(ROOT).as_posix(), "copied": copied, "profile_data": data,
            "profile_markdown": md}
