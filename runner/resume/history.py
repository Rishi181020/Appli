"""Past versions of a tailored resume: each build moves the previous one into <folder>/versions/<time>/ instead of
deleting it, so any of them can be restored."""
import json
import shutil
import time
from pathlib import Path

KEEP = 20  # versions per folder; the oldest go first


def _files(folder: Path) -> list[Path]:
    return [f for f in folder.glob("*") if f.is_file()] if folder.exists() else []


def _version(folder: Path, meta: dict | None, move: bool) -> str | None:
    files = _files(folder)
    if not files:
        return None
    vid = time.strftime("%Y%m%d-%H%M%S")
    target, n = folder / "versions" / vid, 1
    while target.exists():  # two builds in one second
        target, n = folder / "versions" / f"{vid}-{n}", n + 1
    target.mkdir(parents=True)
    for f in files:
        (shutil.move if move else shutil.copy2)(str(f), target / f.name)
    (target / "version.json").write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **(meta or {})}),
                                         encoding="utf-8")
    for old in versions(folder)[KEEP:]:
        shutil.rmtree(folder / "versions" / old["id"], ignore_errors=True)
    return target.name


def snapshot(folder: Path, meta: dict | None = None) -> str | None:
    """Move the current files into a new version, before a build replaces them. None = nothing to keep."""
    return _version(folder, meta, move=True)


def save_copy(folder: Path, meta: dict | None = None) -> str | None:
    """Keep a copy of the current files as a version (they stay current too)."""
    return _version(folder, meta, move=False)


def versions(folder: Path) -> list[dict]:
    """Newest first: [{id, at, files, ...meta}]."""
    out = []
    for d in sorted((folder / "versions").glob("*"), reverse=True) if (folder / "versions").exists() else []:
        try:
            meta = json.loads((d / "version.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        out.append({**meta, "id": d.name, "files": sorted(f.name for f in d.glob("*") if f.name != "version.json")})
    return out


def restore(folder: Path, vid: str) -> None:
    """Make a past version current again; what was current becomes a version itself (nothing is lost)."""
    src = folder / "versions" / str(vid)
    if not vid or any(c in str(vid) for c in "/\\") or ".." in str(vid) or not src.is_dir():
        raise ValueError("Unknown version")
    files = {f.name: f.read_bytes() for f in src.glob("*") if f.is_file() and f.name != "version.json"}
    snapshot(folder, {"note": f"before restoring {vid}"})  # may prune the oldest version: read it first
    for name, data in files.items():
        (folder / name).write_bytes(data)
