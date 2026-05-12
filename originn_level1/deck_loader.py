"""
Deck loader — turn an uploaded file into the deck text the extractor reads.

Supported formats:
  .pptx  →  python-pptx, extracts slide titles + bullets + tables + speaker notes
  .pdf   →  pypdf, extracts page-by-page text
  .md    →  read as UTF-8
  .txt   →  read as UTF-8

Output is a single normalized string with slide/page boundaries marked so the
LLM extractor can preserve the structural context (which question lives on
which slide). Empty results raise — never silently ship a blank deck to the LLM.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


class UnsupportedDeckFormat(Exception):
    pass


class EmptyDeckError(Exception):
    pass


# ─────────────────────────────────────────────
# PPTX
# ─────────────────────────────────────────────

def _extract_pptx(content: bytes) -> str:
    """
    Walk every shape on every slide, extracting text from:
      - title placeholders
      - body / content placeholders (bullets etc.)
      - text boxes
      - tables (cell by cell)
      - speaker notes
    """
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation(io.BytesIO(content))
    out_lines: list[str] = []

    for i, slide in enumerate(prs.slides, start=1):
        out_lines.append(f"\n=== Slide {i} ===")

        # Title (if any)
        title = None
        if slide.shapes.title and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        if title:
            out_lines.append(f"# {title}")

        # All other shapes
        for shape in slide.shapes:
            # Skip title — already handled
            if slide.shapes.title is shape:
                continue

            # Text frames (body, text boxes)
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs).strip()
                    if text:
                        prefix = "  " * max(0, para.level) + "• "
                        out_lines.append(prefix + text)

            # Tables
            if shape.has_table:
                for row in shape.table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells)
                    if row_text.strip("| "):
                        out_lines.append("  " + row_text)

        # Speaker notes
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            notes_text = notes_frame.text.strip() if notes_frame else ""
            if notes_text:
                out_lines.append("[Speaker notes]: " + notes_text)

    return "\n".join(out_lines).strip()


# ─────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────

def _extract_pdf(content: bytes) -> str:
    import pypdf

    reader = pypdf.PdfReader(io.BytesIO(content))
    out_lines: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        out_lines.append(f"\n=== Page {i} ===")
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            logger.warning("PDF page %d extraction failed: %s", i, exc)
            text = ""
        if text.strip():
            out_lines.append(text.strip())
    return "\n".join(out_lines).strip()


# ─────────────────────────────────────────────
# Plain text / markdown
# ─────────────────────────────────────────────

def _extract_text(content: bytes) -> str:
    try:
        return content.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise UnsupportedDeckFormat(
            "File appears to be binary but extension suggests text. "
            "Save as UTF-8 .txt or .md."
        ) from exc


# ─────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────

_EXTRACTORS = {
    ".pptx": _extract_pptx,
    ".pdf":  _extract_pdf,
    ".md":   _extract_text,
    ".txt":  _extract_text,
}


def load_deck(filename: str, content: bytes) -> str:
    """
    Detect format from filename suffix and extract text. Raises:
      UnsupportedDeckFormat — extension we don't handle
      EmptyDeckError        — extraction returned no text
    """
    ext = Path(filename).suffix.lower()
    if ext not in _EXTRACTORS:
        raise UnsupportedDeckFormat(
            f"Unsupported file type {ext!r}. "
            f"Supported: {', '.join(sorted(_EXTRACTORS))}"
        )

    text = _EXTRACTORS[ext](content)
    if not text.strip():
        raise EmptyDeckError(
            f"No text could be extracted from {filename!r}. "
            "The file may be image-only or password-protected."
        )

    # Minimum length check — protect against a 1-character deck slipping through
    if len(text) < 200:
        raise EmptyDeckError(
            f"Extracted only {len(text)} chars from {filename!r}. "
            "Deck may be mostly images; consider re-exporting with text content."
        )

    return text


SUPPORTED_EXTENSIONS = tuple(_EXTRACTORS.keys())
