"""LaTeX resume handling: find the editable units, apply edits, flatten to plain text, compile to PDF.

Only two kinds of unit are ever editable: experience/project bullets (\\resumeItem{...}) and the item lists in
the Technical Skills section. Headers, employers, titles, dates, education and links are never touched.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

_MIKTEX = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/MiKTeX/miktex/bin/x64/pdflatex.exe"


@dataclass
class Unit:
    id: str
    kind: str  # "bullet" | "skill"
    start: int  # span of the editable content in the source
    end: int
    text: str  # LaTeX content of the unit
    context: str  # e.g. "Jio Platforms Limited - Software Development Engineer 1" or the skills label


# ---- parsing --------------------------------------------------------------------------------
def _match_brace(s: str, open_idx: int) -> int:
    """Index of the '}' that closes the '{' at open_idx (escaped braces ignored), or -1."""
    depth = 0
    i = open_idx
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _first_group(s: str, after: int) -> str:
    """Content of the first {...} group at or after index `after`."""
    o = s.find("{", after)
    c = _match_brace(s, o) if o != -1 else -1
    return s[o + 1 : c] if c != -1 else ""


def _third_group(s: str, after: int) -> str:
    """Content of the third {...} group starting at `after` (the role in \\resumeSubheading)."""
    idx = after
    for _ in range(3):
        o = s.find("{", idx)
        if o == -1:
            return ""
        c = _match_brace(s, o)
        if c == -1:
            return ""
        idx = c + 1
        last = s[o + 1 : c]
    return last


def parse(src: str) -> list[Unit]:
    body_start = src.find("\\begin{document}")
    if body_start == -1:
        raise ValueError("no \\begin{document} in the .tex file")
    units: list[Unit] = []

    heading_re = re.compile(r"\\(resumeSubheading|resumeProjectHeading)\b")
    headings = [(m.start(), m.end(), m.group(1)) for m in heading_re.finditer(src, body_start)]

    n = 0
    for m in re.finditer(r"\\resumeItem\s*\{", src[body_start:]):
        open_idx = body_start + m.end() - 1
        close_idx = _match_brace(src, open_idx)
        if close_idx == -1:
            continue
        prev = [h for h in headings if h[0] < open_idx]
        ctx = ""
        if prev:
            hs, he, kind = prev[-1]
            name = plain(_first_group(src, he))
            role = plain(_third_group(src, he)) if kind == "resumeSubheading" else ""
            ctx = f"{name} - {role}" if role else name
        n += 1
        units.append(Unit(f"b{n}", "bullet", open_idx + 1, close_idx, src[open_idx + 1 : close_idx], ctx))

    sk = src.find("\\section{Technical Skills}", body_start)
    if sk != -1:
        end = src.find("\\end{itemize}", sk)
        pos = sk
        k = 0
        for line in src[sk:end].splitlines(keepends=True):
            lm = re.match(r"^(\s*\\textbf\{([^}]*)\}\s*)(.+?)(\s*\\\\)?\s*$", line.rstrip("\r\n"))
            if lm and lm.group(3).strip():
                k += 1
                start = pos + lm.end(1)
                units.append(Unit(f"s{k}", "skill", start, start + len(lm.group(3)), lm.group(3), plain(lm.group(2)).rstrip(": ")))
            pos += len(line)
    return units


def apply_edits(src: str, units: list[Unit], edits: dict[str, str]) -> str:
    """Replace the spans of the edited units. Units never overlap, so any subset of edits is safe."""
    by_id = {u.id: u for u in units}
    out = src
    for uid in sorted(edits, key=lambda i: by_id[i].start, reverse=True):
        u = by_id[uid]
        out = out[: u.start] + edits[uid] + out[u.end :]
    return out


# ---- flattening -----------------------------------------------------------------------------
def plain(tex: str) -> str:
    s = tex
    s = re.sub(r"\\allowbreak\s*", "", s)
    s = re.sub(r"\\href\{[^}]*\}", "", s)
    s = re.sub(r"\\textcolor\{[^}]*\}", "", s)
    s = re.sub(r"\\([%&_$#])", r"\1", s)
    s = re.sub(r"\\[ ,;]", " ", s)
    s = s.replace("\\\\", " ").replace("$|$", "|").replace("$\\times$", "x").replace("--", "-").replace("~", " ")
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", s).strip()


def document_plain(src: str) -> str:
    a = src.find("\\begin{document}")
    b = src.find("\\end{document}")
    return plain(src[a + len("\\begin{document}") : b if b != -1 else len(src)])


# ---- validation of a proposed replacement ---------------------------------------------------
_ALLOWED_MACROS = {"textbf", "textit", "texttt", "allowbreak"}
_ESCAPES = set("%&_$#")


def latex_problems(fragment: str) -> list[str]:
    """Why this fragment is unsafe to splice into the resume (empty list = fine)."""
    problems = []
    depth = 0
    i = 0
    while i < len(fragment):
        c = fragment[i]
        if c == "\\":
            m = re.match(r"\\([a-zA-Z]+)", fragment[i:])
            if m:
                if m.group(1) not in _ALLOWED_MACROS:
                    problems.append(f"uses \\{m.group(1)}")
                i += len(m.group(0))
                continue
            nxt = fragment[i + 1 : i + 2]
            if nxt not in _ESCAPES and nxt not in "\\ ,{}":
                problems.append(f"unexpected escape \\{nxt}")
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                problems.append("unbalanced braces")
                break
        elif c in "%&#" or (c in "_$" and "\\" != fragment[i - 1 : i]):
            problems.append(f"unescaped {c}")
        i += 1
    if depth > 0:
        problems.append("unbalanced braces")
    return sorted(set(problems))


# ---- compiling ------------------------------------------------------------------------------
def pdflatex_path() -> str:
    found = shutil.which("pdflatex")
    if found:
        return found
    if _MIKTEX.exists():
        return str(_MIKTEX)
    raise RuntimeError("pdflatex not found. Install MiKTeX or add pdflatex to PATH.")


@dataclass
class Compiled:
    ok: bool
    pages: int
    pdf: bytes | None
    log: str


def compile_tex(src: str, timeout: int = 240) -> Compiled:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "main.tex").write_text(src, encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(
                [pdflatex_path(), "--enable-installer", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                cwd=d, capture_output=True, timeout=timeout, creationflags=flags,
            )
        except subprocess.TimeoutExpired:
            return Compiled(False, 0, None, "pdflatex timed out")
        out = proc.stdout.decode("utf-8", errors="replace")
        pdf_path = Path(d) / "main.pdf"
        if proc.returncode != 0 or not pdf_path.exists():
            errs = [ln for ln in out.splitlines() if ln.startswith("!")]
            return Compiled(False, 0, None, "\n".join(errs[:4]) or out[-400:])
        data = pdf_path.read_bytes()
        return Compiled(True, len(PdfReader(pdf_path).pages), data, "")


# ---- markup <-> LaTeX (what the model sees and writes) --------------------------------------
# JSON and backslashes do not mix (a model writing "\textbf" produces a tab plus "extbf"), so units go to the
# model as light markup - **bold**, *italic*, `code` - and come back through from_markup, which escapes properly.
def to_markup(tex: str) -> str:
    s = re.sub(r"\\allowbreak\s*", "", tex)
    s = re.sub(r"\\textbf\{([^{}]*)\}", r"**\1**", s)
    s = re.sub(r"\\textit\{([^{}]*)\}", r"*\1*", s)
    s = re.sub(r"\\texttt\{([^{}]*)\}", r"`\1`", s)
    s = re.sub(r"\\([%&_$#])", r"\1", s)
    s = re.sub(r"\\ ", " ", s)
    return s.strip()


def is_editable(unit: "Unit") -> bool:
    """A unit is editable only if it round-trips through the markup without leftover LaTeX."""
    m = to_markup(unit.text)
    return "\\" not in m and "{" not in m and "}" not in m


def from_markup(markup: str) -> str:
    s = markup.strip()
    s = re.sub(r"([%&_$#])", r"\\\1", s)
    s = re.sub(r"\*\*(.+?)\*\*", lambda m: "\\textbf{" + m.group(1) + "}", s)
    s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", lambda m: "\\textit{" + m.group(1) + "}", s)
    s = re.sub(r"`(.+?)`", lambda m: "\\texttt{" + m.group(1) + "}", s)
    return s


def markup_problems(markup: str) -> list[str]:
    """Reasons a model-written unit cannot be safely converted (empty = fine)."""
    problems = []
    if "\\" in markup:
        problems.append("contains a backslash")
    if "{" in markup or "}" in markup:
        problems.append("contains braces")
    if markup.count("**") % 2:
        problems.append("unbalanced **bold**")
    if markup.count("`") % 2:
        problems.append("unbalanced `code`")
    if "\n" in markup.strip():
        problems.append("contains a line break")
    return problems
