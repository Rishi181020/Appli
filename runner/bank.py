"""The experience bank: everything the candidate has done, in their own words, to build tailored resumes from.

Lives in users/<Name>/bank/ on this computer:
    bank.json   {"entries": [...], "files": [...]}
    files/      the uploaded originals (resumes, cover letters, project write-ups, papers)

An entry is one thing they did: a role, a project, a publication, research, an award, a skill list, or a piece of
narrative (from a cover letter). Its `text` is copied from the source, never rewritten, so every resume line built
from the bank can be traced back to words the candidate actually wrote.
"""
import io
import json
import re
import time
import uuid
import zipfile
from pathlib import Path

from . import config, llm

KINDS = ["experience", "project", "publication", "research", "education", "award", "leadership", "skills", "narrative", "other"]
MAX_UPLOAD = 15 * 1024 * 1024
FILE_TYPES = (".pdf", ".docx", ".tex", ".txt", ".md")


class BankError(ValueError):
    pass


# ---- storage ---------------------------------------------------------------------------------------------------
def _dir() -> Path:
    if config.HOME is None:
        raise BankError("Finish the setup screens first")
    return config.HOME / "bank"


def load() -> dict:
    try:
        d = json.loads((_dir() / "bank.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"entries": d.get("entries", []), "files": d.get("files", [])}


def save(d: dict):
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / "bank.json").write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")


def _clean_entry(e: dict, source: str) -> dict:
    kind = str(e.get("kind") or "other").lower()
    return {
        "id": e.get("id") or uuid.uuid4().hex[:10],
        "kind": kind if kind in KINDS else "other",
        "title": str(e.get("title") or "").strip()[:200],
        "org": str(e.get("org") or "").strip()[:200],
        "location": str(e.get("location") or "").strip()[:120],
        "start": str(e.get("start") or "").strip()[:20],
        "end": str(e.get("end") or "").strip()[:20],
        "link": str(e.get("link") or "").strip()[:300],
        "text": str(e.get("text") or "").strip()[:12000],
        "tags": [str(t).strip() for t in (e.get("tags") or []) if str(t).strip()][:30],
        # bullets the person wrote and approved: used word for word on every resume, most important first
        "pinned": [str(b).strip() for b in (e.get("pinned") or []) if str(b).strip()][:8],
        "source": e.get("source") or source,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }


def upsert_entry(e: dict) -> dict:
    d = load()
    keep_pins = "pinned" not in e  # an editor that doesn't know about pinned bullets mustn't wipe them
    e = _clean_entry(e, e.get("source") or "typed")
    if keep_pins:
        e["pinned"] = next((x.get("pinned", []) for x in d["entries"] if x["id"] == e["id"]), [])
    if not (e["title"] or e["text"]):
        raise BankError("An entry needs a title or some text")
    for i, x in enumerate(d["entries"]):
        if x["id"] == e["id"]:
            d["entries"][i] = e
            break
    else:
        d["entries"].append(e)
    save(d)
    return e


def delete_entry(entry_id: str):
    d = load()
    d["entries"] = [e for e in d["entries"] if e["id"] != entry_id]
    for f in d["files"]:
        f["entries"] = [x for x in f.get("entries", []) if x != entry_id]
    save(d)


def delete_file(name: str):
    """Remove an uploaded file and the entries that came only from it."""
    d = load()
    f = next((x for x in d["files"] if x["name"] == name), None)
    if not f:
        return
    d["files"] = [x for x in d["files"] if x["name"] != name]
    # entries this file only added sentences to (they came from elsewhere first, or other files share them) stay
    shared = {i for x in d["files"] for i in x.get("entries", [])}
    gone = {e["id"] for e in d["entries"] if e["id"] in set(f.get("entries", [])) and e["id"] not in shared and e.get("source") == name}
    d["entries"] = [e for e in d["entries"] if e["id"] not in gone]
    save(d)
    try:
        (_dir() / "files" / name).unlink(missing_ok=True)
    except OSError:
        pass


# ---- reading files ---------------------------------------------------------------------------------------------
def text_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    if ext == ".docx":
        with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
    if ext == ".tex":
        from .resume import tex

        src = path.read_text(encoding="utf-8", errors="replace")
        return tex.document_plain(src) if "\\begin{document}" in src else tex.plain(src)
    return path.read_text(encoding="utf-8", errors="replace")


# ---- splitting a document into entries (the model copies, it doesn't write) ----------------------------------
_SPLIT_SYSTEM = """You split a candidate's document (a resume, cover letter, project write-up or research paper) into
experience entries for their personal experience bank. Copy, never write:
- `text` holds the document's own sentences and bullet points for that entry, copied word for word (fix only broken
  line wraps and hyphenation). Never summarise, rephrase, add, or merge in facts from elsewhere.
- One entry per role, project, publication, research effort, award or leadership position. A resume's skills list is
  one entry of kind "skills" (text: the lines as written). A cover letter's paragraphs that describe real work or
  motivation become kind "narrative" entries (skip greetings, sign-offs and the company-specific opening).
- A research paper is one "publication" entry: title as `title`, venue/journal/conference as `org`, year as `end`,
  authors and the abstract plus the paper's own statements of its contributions and results in `text` (copied).
- Dates as written ("Jan 2026", "2023"); "Present" for ongoing. Leave fields empty when the document doesn't say.
- `tags`: technologies, methods and domains named in that entry's own text (a few words each).
- Skip the candidate's name and contact details.
Return JSON: {"entries": [{"kind": one of %s, "title": str, "org": str, "location": str,
"start": str, "end": str, "link": str, "text": str, "tags": [str]}]}""" % KINDS


def split(text: str, filename: str) -> list[dict]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 40:
        raise BankError(f"No readable text in {filename} (a scanned PDF needs a text version)")
    chunks = [text[i : i + 24000] for i in range(0, min(len(text), 72000), 24000)]  # very long papers: first ~70k chars
    out: list[dict] = []
    for i, chunk in enumerate(chunks):
        res = llm.chat_json(_SPLIT_SYSTEM, f"DOCUMENT ({filename}{', part ' + str(i + 1) if len(chunks) > 1 else ''}):\n{chunk}",
                            model=config.model("write"), max_tokens=12000, purpose=f"bank: read {filename}")
        out += [e for e in res.get("entries") or [] if isinstance(e, dict)]
    return out


def _key(e: dict) -> str:
    """Which entries are the same thing seen in different documents (merged, not duplicated)."""
    n = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()  # noqa: E731
    org = " ".join(n(e["org"]).split()[:3])  # "Miller Center for Global Impact, Santa Clara University" ~ "Miller Center for"
    if e["kind"] in ("experience", "research", "leadership", "education"):
        return f"{e['kind']}|{org or n(e['title'])}"  # one entry per organisation / school
    if e["kind"] == "skills":
        return "skills"
    # projects, publications, awards: the name before any subtitle or "| 2nd Place"
    return f"{e['kind']}|{n(re.split(r'[:|]| - ', e['title'] or '')[0])}"


def merge(entries: list[dict], new: list[dict]) -> tuple[list[dict], list[str]]:
    """Add entries; the same role/project seen again (e.g. in another resume) gets its new sentences appended instead
    of a duplicate entry. -> (entries, ids touched)"""
    by_key = {_key(e): e for e in entries if e["kind"] != "narrative"}
    touched = []
    for e in new:
        k = _key(e)
        old = by_key.get(k) if e["kind"] != "narrative" and e["title"] else None
        if old:
            have = {re.sub(r"\W+", "", s.lower()) for s in re.split(r"(?<=[.!?])\s+|\n", old["text"]) if s.strip()}
            extra = [s for s in re.split(r"(?<=[.!?])\s+|\n", e["text"]) if s.strip() and re.sub(r"\W+", "", s.lower()) not in have]
            if extra:
                old["text"] = (old["text"] + "\n" + "\n".join(extra)).strip()[:12000]
            old["tags"] = sorted(set(old["tags"]) | set(e["tags"]))[:30]
            for f in ("location", "start", "end", "link", "org"):
                old[f] = old[f] or e[f]
            old["updated"] = e["updated"]
            touched.append(old["id"])
        else:
            entries.append(e)
            by_key[k] = e
            touched.append(e["id"])
    return entries, touched


def add_document(filename: str, data: bytes) -> dict:
    """Store an uploaded file, read it and add its entries. -> {"file", "added", "updated"}"""
    ext = Path(filename).suffix.lower()
    if ext not in FILE_TYPES:
        raise BankError(f"Files can be {', '.join(FILE_TYPES)}")
    if len(data) > MAX_UPLOAD:
        raise BankError("That file is over 15 MB")
    folder = _dir() / "files"
    folder.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[<>:"/\\|?*]+', "_", Path(filename).name)
    path = folder / name
    path.write_bytes(data)
    return _ingest(path, name)


def _ingest(path: Path, name: str) -> dict:
    d = load()
    before = {e["id"] for e in d["entries"]}
    new = [_clean_entry(e, name) for e in split(text_of(path), name)]
    d["entries"], touched = merge(d["entries"], new)
    d["files"] = [f for f in d["files"] if f["name"] != name] + [
        {"name": name, "added": time.strftime("%Y-%m-%d %H:%M"), "entries": sorted(set(touched))}]
    save(d)
    return {"file": name, "added": len([t for t in set(touched) if t not in before]),
            "updated": len([t for t in set(touched) if t in before])}


def import_existing() -> dict:
    """Seed the bank from what Appli already has: every resume (LaTeX source, else PDF), the profile and the cover
    letter. Safe to repeat: known entries just gain any new sentences."""
    results = []
    sources: list[tuple[Path, str]] = []
    for key, pdf in config.RESUMES.items():
        src = config.RESUME_TEX.get(key) or pdf
        if src and src.exists():
            sources.append((src, f"resume: {config.RESUME_LABELS.get(key, key)}"))
    if config.PROFILE_PATH and config.PROFILE_PATH.exists():
        sources.append((config.PROFILE_PATH, "profile"))
    if config.COVER_LETTER and config.COVER_LETTER.exists():
        sources.append((config.COVER_LETTER, "cover letter"))
    if not sources:
        raise BankError("Nothing to import yet: add a resume on My files first")
    for path, label in sources:
        try:
            r = _ingest(path, label)
            results.append(r)
        except Exception as e:
            results.append({"file": label, "error": f"{type(e).__name__}: {str(e)[:120]}"})
    return {"imported": results}


def summary() -> dict:
    d = load()
    counts: dict[str, int] = {}
    for e in d["entries"]:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {"entries": len(d["entries"]), "by_kind": counts, "files": len(d["files"])}
