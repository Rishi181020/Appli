"""Rebuild text-based PDFs as selectable, single-column resumes without rewriting their facts."""
import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from pypdf import PdfReader

from .. import config
from . import match, select, tailor

_HEADINGS = re.compile(r"^(?:professional summary|summary|profile|objective|technical skills|skills|"
                       r"core competencies|experience|professional experience|work experience|employment|"
                       r"education|projects|personal projects|research|publications|certifications|"
                       r"awards|leadership|volunteer experience|additional skills)\s*:?$", re.I)


def extract(path: Path) -> str:
    try:
        reader = PdfReader(path)
        if not 1 <= len(reader.pages) <= 10:
            raise ValueError("Use a resume PDF with 1 to 10 pages")
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError("This PDF could not be read. Upload an unlocked, text-based PDF or its Word source.") from e
    if any(len(re.sub(r"\s", "", page)) < 30 for page in pages):
        raise ValueError("This PDF has a scanned or unreadable page. Export a PDF with selectable text or add a Word/LaTeX source; image-only PDFs need OCR first.")
    text = "\n".join(pages)
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines() if line.strip())
    if len(text) > 50000:
        raise ValueError("This PDF contains too much text for a resume")
    return text


def propose(info: dict, profile_text: str, text: str) -> dict:
    evidence = select.evidence_text(profile_text)
    normalized = match.norm_text(evidence)
    equivalents = set(info.get("equivalent_any", []))
    missing = set(info["missing"])
    wanted = [term for term in info["terms"] if term["term"] in missing][:tailor.MAX_SKILLS]
    edits = []
    for term in wanted:
        backed = match.supported(term, normalized)
        shown_as = not backed and term["term"] in equivalents
        edits.append({"id": f"e{len(edits) + 1}", "kind": "skill", "unit_id": "additional_skills",
                      "context": "Existing Skills section", "skill": term["term"], "category": term.get("category"),
                      "required": bool(term.get("required")), "support": "backed" if backed else "shown_as" if shown_as else "not_found",
                      "default": backed or shown_as, "before": "", "after": term["term"],
                      "keywords_added": [term["term"]], "added_words": [term["term"]], "removed_words": [],
                      "evidence": tailor._evidence_snippet(term, evidence) if backed else
                      "Shown under another name in your resumes" if shown_as else ""})
    added = [e["skill"] for e in edits if e["default"]]
    after = match.score(text + ("\nAdditional Skills\n" + ", ".join(added) if added else ""), info["terms"], set(info.get("equivalent", [])))
    return {"mode": "skills", "format": "pdf_rebuild", "base": info["chosen"],
            "pct_before": info["pct"], "pct_after": after["pct"], "threshold": config.RESUME_MATCH_THRESHOLD,
            "edits": edits, "gaps": [e["skill"] for e in edits if not e["default"]], "problems": [],
            "extracted_text": text}


def render(text: str, skills: list[str]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    regular, bold = "Helvetica", "Helvetica-Bold"
    for normal, heavy in ((Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
                          (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))):
        if normal.is_file() and heavy.is_file():
            if "AppliResume" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("AppliResume", str(normal)))
                pdfmetrics.registerFont(TTFont("AppliResumeBold", str(heavy)))
            regular, bold = "AppliResume", "AppliResumeBold"
            break
    body = ParagraphStyle("body", fontName=regular, fontSize=10, leading=13, spaceAfter=4,
                          splitLongWords=True, textColor=colors.HexColor("#18202a"))
    heading = ParagraphStyle("section", parent=body, fontName=bold, fontSize=11, leading=14,
                             spaceBefore=10, spaceAfter=5, keepWithNext=True)
    name = ParagraphStyle("name", parent=body, fontName=bold, fontSize=17, leading=21,
                          alignment=TA_CENTER, spaceAfter=10, keepWithNext=True)
    story = []
    for i, line in enumerate(text.splitlines()):
        if line.strip():
            story.append(Paragraph(escape(line.strip()), name if i == 0 else heading if _HEADINGS.fullmatch(line.strip()) else body))
    if skills:
        story += [Paragraph("Additional Skills", heading), Paragraph(escape(", ".join(skills)), body)]
    output = io.BytesIO()
    SimpleDocTemplate(output, pagesize=letter, leftMargin=42, rightMargin=42, topMargin=36,
                      bottomMargin=36, title="Resume", author="", pageCompression=1).build(story)
    return output.getvalue()


def skills_layout(path: Path) -> dict:
    import pymupdf

    with pymupdf.open(path) as document:
        if len(document) != 1:
            raise ValueError("Use a one-page base resume to preserve its layout. This PDF has more than one page.")
        page = document[0]
        lines = sorted((line for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])),
                       key=lambda line: (line["bbox"][1], line["bbox"][0]))
        text = lambda line: "".join(span["text"] for span in line["spans"]).strip()
        start = next((i for i, line in enumerate(lines) if re.fullmatch(r"(?:technical skills|skills|core competencies|technologies|tech stack|tools)\s*:?", text(line), re.I)), None)
        if start is None:
            raise ValueError("Could not find a Skills section to edit without changing the layout. Add an editable source or a PDF with a Skills heading.")
        end = next((i for i in range(start + 1, len(lines)) if _HEADINGS.fullmatch(text(lines[i]))), len(lines))
        content = lines[start + 1:end]
        if not content:
            raise ValueError("The PDF has no editable Skills content under its heading.")
        left = min(line["bbox"][0] for line in content)
        right = page.rect.width - left
        top = min(line["bbox"][1] for line in content)
        bottom = lines[end]["bbox"][1] - 3 if end < len(lines) else min(page.rect.height - 18, max(line["bbox"][3] for line in content) + 12)
        # Refuse ambiguous columns rather than replacing unrelated material in the same vertical band.
        if any(line["bbox"][0] > page.rect.width * .6 for line in content):
            raise ValueError("This Skills section uses multiple columns. Add an editable source to preserve it reliably.")
        paragraphs = []
        for line in content:
            value = text(line)
            if not value:
                continue
            if re.match(r"^[^:]{1,45}:\s*\S", value) or not paragraphs:
                paragraphs.append(value)
            else:
                paragraphs[-1] += " " + value
        sizes = [span["size"] for line in content for span in line["spans"] if span["text"].strip()]
        font = next(span["font"] for line in content for span in line["spans"] if span["text"].strip())
        return {"rect": [left, top, right, bottom], "paragraphs": paragraphs,
                "font_size": max(sizes), "font": font, "heading": text(lines[start])}


def preserve_layout(path: Path, chosen: list[dict]) -> bytes:
    import pymupdf

    layout = skills_layout(path)
    if not chosen:
        return path.read_bytes()
    paragraphs = list(layout["paragraphs"])
    for edit in chosen:
        hints = tailor._LINE_HINTS.get(edit.get("category"), ())
        index = next((i for hint in hints for i, paragraph in enumerate(paragraphs)
                      if hint in paragraph.partition(":")[0].lower()), len(paragraphs) - 1)
        paragraphs[index] = paragraphs[index].rstrip().rstrip(".") + ", " + edit["skill"]
    html = "".join("<p>" + ("<b>" + escape(p.partition(":")[0]) + ":</b> " + escape(p.partition(":")[2].strip())
                              if ":" in p else escape(p)) + "</p>" for p in paragraphs)
    size = layout["font_size"]
    minimum = min(size, 9.0)
    family = "serif" if any(name in layout["font"].lower() for name in ("cambria", "times", "cmr", "cmb")) else "sans-serif"
    css = f"body {{margin:0;padding:0;}} p {{font-family:{family};font-size:{size}pt;line-height:1.05;color:#000;margin:0 0 1.5pt;padding:0;}}"
    # Use the original font family when its full font is available (subset fonts may lack new skill letters).
    archive = None
    # Cambria's TTC includes a math font whose ligatures can receive incorrect
    # Unicode mappings in MuPDF. Use a standalone serif font for reliable ATS text.
    normal = "times.ttf" if family == "serif" else "arial.ttf"
    heavy = "timesbd.ttf" if family == "serif" else "arialbd.ttf"
    fonts = Path("C:/Windows/Fonts")
    if (fonts / normal).is_file() and (fonts / heavy).is_file():
        archive = pymupdf.Archive(str(fonts))
        css += f"@font-face {{font-family:original;src:url('{normal}');}} @font-face {{font-family:original;src:url('{heavy}');font-weight:bold;}} p {{font-family:original;}}"
    with pymupdf.open(path) as document:
        page = document[0]
        area = pymupdf.Rect(layout["rect"])
        page.add_redact_annot(area, fill=(1, 1, 1))
        page.apply_redactions(images=0, graphics=0)
        spare, scale = page.insert_htmlbox(area, html, css=css, archive=archive, scale_low=minimum / size)
        if spare < 0:
            raise ValueError("Selected skills do not fit in the existing Skills section on one page. Deselect a few skills and build again. Your original layout and experience will not be compressed or removed.")
        # MuPDF can emit supplementary Unicode scalars as five-digit hex values.
        # PDF ToUnicode maps require UTF-16BE (surrogate pairs), which readers such
        # as pypdf otherwise reject as odd-length strings.
        for font in page.get_fonts(full=True):
            kind, value = document.xref_get_key(font[0], "ToUnicode")
            if kind != "xref":
                continue
            xref = int(value.split()[0])
            cmap = document.xref_stream(xref)
            def utf16(m):
                code = int(m[1], 16)
                return b"<" + chr(code).encode("utf-16-be").hex().upper().encode() + b">" if 0xFFFF < code <= 0x10FFFF else m[0]
            fixed = re.sub(rb"<([0-9A-Fa-f]{5,6})>", utf16, cmap)
            if fixed != cmap:
                document.update_stream(xref, fixed)
        output = document.tobytes(garbage=4, deflate=True)
    return output


def build(draft: dict, accepted: list[str], target: Path) -> dict:
    from .. import config
    from .build import file_stem

    chosen = [e for e in draft["edits"]["edits"] if e["id"] in accepted]
    skills = [e["skill"] for e in chosen]
    try:
        data = preserve_layout(config.RESUMES[draft["match"]["chosen"]], chosen)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    reader = PdfReader(io.BytesIO(data))
    rendered = "\n".join(page.extract_text() or "" for page in reader.pages)
    if len(reader.pages) != 1:
        return {"ok": False, "error": "The tailored resume must stay on one page. Deselect some skills and rebuild."}
    if any(not match.term_present(match.norm_text(rendered), {"term": skill, "aliases": []}) for skill in skills):
        return {"ok": False, "error": "A selected skill could not be rendered reliably. Add an editable source instead."}
    info = draft["match"]
    score = match.score(rendered, info["terms"], set(info.get("equivalent", [])))
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{file_stem(draft['job'].get('role'))}.pdf"
    path.write_bytes(data)
    return {"ok": True, "file": str(path.relative_to(config.ROOT)), "pct_after": score["pct"],
            "added_words": skills, "removed_words": [], "pages": 1, "preserved_pdf_layout": True}
