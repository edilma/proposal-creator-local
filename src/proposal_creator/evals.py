"""Requirements checklist and per-opportunity evaluations.

Enforces the non-negotiable at the storage layer: a PASS is stored only with
a quote, and any stored quote must appear verbatim in the solicitation text
when that text is available. Quote verification supports plain-text documents
(.txt/.md), text PDFs, and .xlsx workbooks; anything else — or a file with no
extractable text, such as a scan — stores the quote as unverified.
"Verbatim" is tested on normalized forms (lowercase, alphanumeric only) so PDF
extraction artifacts — stray spaces, line-break hyphens, punctuation noise —
don't cause false refusals. The match itself is still an exact substring:
only formatting is forgiven, never words or numbers.
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader

DEFAULT_REQUIREMENTS_PATH = Path("data/requirements_gov.json")
VERDICTS = ("PASS", "FAIL", "UNKNOWN", "N/A")
READABLE_SUFFIXES = {".txt", ".md", ".markdown", ".pdf", ".xlsx"}


def normalize(text: str) -> str:
    """Lowercase alphanumeric only.

    Strips case, whitespace, punctuation, and hyphens so extraction
    formatting can't break the match. Words and numbers must still match
    exactly — this is normalization for an exact substring test, not fuzzy
    matching: no thresholds, no edit distance, no token ratios.
    """
    return "".join(ch for ch in text.lower() if ch.isalnum())


def quote_in_text(quote: str | None, text: str | None) -> bool:
    if not quote or text is None:
        return False
    clean = normalize(quote)
    return bool(clean) and clean in normalize(text)


def read_doc_text(document_path: str | None) -> str | None:
    if not document_path:
        return None
    path = Path(str(document_path))
    if path.suffix.lower() not in READABLE_SUFFIXES:
        return None
    if path.suffix.lower() == ".pdf":
        return _read_pdf_text(path)
    if path.suffix.lower() == ".xlsx":
        return _read_xlsx_text(path)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_pdf_text(path: Path) -> str | None:
    # Broad catch: malformed, encrypted, and image-only PDFs fail in varied
    # ways, and all of them mean the same thing here — unverifiable.
    try:
        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(str(path)).pages
        )
    except Exception:
        return None
    return text if text.strip() else None


def _read_xlsx_text(path: Path) -> str | None:
    """Cell text of every sheet, rows joined (stdlib zipfile + ElementTree).

    Shared strings, inline strings, and raw values (numbers, dates);
    anything malformed reads as unverifiable, like a bad PDF.
    """
    try:
        with zipfile.ZipFile(path) as book:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in book.namelist():
                strings_root = ET.fromstring(
                    book.read("xl/sharedStrings.xml")
                )
                for item in strings_root.iter():
                    if item.tag.endswith("}si"):
                        shared.append("".join(
                            node.text or ""
                            for node in item.iter()
                            if node.tag.endswith("}t")
                        ))
            sheets = sorted(
                name for name in book.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            lines = []
            for sheet in sheets:
                sheet_root = ET.fromstring(book.read(sheet))
                for row in sheet_root.iter():
                    if not row.tag.endswith("}row"):
                        continue
                    cells = []
                    for cell in row:
                        if not cell.tag.endswith("}c"):
                            continue
                        cells.append(_cell_text(cell, shared))
                    line = " ".join(filter(None, cells)).strip()
                    if line:
                        lines.append(line)
    except Exception:
        return None
    return "\n".join(lines) if lines else None


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    """Text of one spreadsheet cell: shared, inline, or raw value."""
    for child in cell:
        if child.tag.endswith("}is"):
            return "".join(
                node.text or ""
                for node in child.iter() if node.tag.endswith("}t")
            ).strip()
        if child.tag.endswith("}v") and child.text:
            value = child.text.strip()
            if cell.get("t") == "s":
                try:
                    return shared[int(value)]
                except (ValueError, IndexError):
                    return ""
            return value
    return ""


def read_opp_text(conn: sqlite3.Connection, opp_id: int) -> str | None:
    """Combined readable text of every document on an opportunity.

    Registered documents (path order) joined; a pre-documents-table
    `document_path` is read when no rows exist yet. Unreadable files are
    skipped; None when nothing readable remains. For a single-document
    opportunity this is exactly `read_doc_text` of that file.
    """
    opp = conn.execute(
        "SELECT document_path FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    if opp is None:
        return None
    rows = conn.execute(
        "SELECT path FROM documents WHERE opportunity_id = ? ORDER BY path",
        (opp_id,),
    ).fetchall()
    paths = [r["path"] for r in rows] or [opp["document_path"]]
    texts = []
    for path in paths:
        text = read_doc_text(path)
        if text:
            texts.append(text)
    return "\n\n".join(texts) if texts else None


def load_requirements(
    conn: sqlite3.Connection, req_path: Path | str = DEFAULT_REQUIREMENTS_PATH
) -> int:
    items = json.loads(Path(req_path).read_text(encoding="utf-8"))
    added = 0
    for item in items:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO requirements (code, category, question, guidance)"
            " VALUES (:code, :category, :question, :guidance)",
            {
                "code": item["code"],
                "category": item.get("category", ""),
                "question": item.get("question", ""),
                "guidance": item.get("guidance"),
            },
        )
        added += cursor.rowcount
    conn.commit()
    return added


def list_requirements(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM requirements ORDER BY code"))


def list_evaluations(conn: sqlite3.Connection, opp_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM evaluations WHERE opportunity_id = ?"
            " ORDER BY requirement_code",
            (opp_id,),
        )
    )


def list_checklist(conn: sqlite3.Connection, opp_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT r.code, r.category, r.question,"
            " e.verdict, e.quote, e.verified, e.note, e.evaluated_by"
            " FROM requirements r LEFT JOIN evaluations e"
            " ON e.requirement_code = r.code AND e.opportunity_id = ?"
            " ORDER BY r.code",
            (opp_id,),
        )
    )


def set_evaluation(
    conn: sqlite3.Connection,
    opp_id: int,
    code: str,
    verdict: str,
    quote: str | None = None,
    note: str | None = None,
    evaluated_by: str = "human",
) -> sqlite3.Row:
    verdict = str(verdict).upper()
    if verdict not in VERDICTS:
        raise ValueError(f"Verdict must be one of {', '.join(VERDICTS)}.")
    known = conn.execute(
        "SELECT code FROM requirements WHERE code = ?", (code,)
    ).fetchone()
    if known is None:
        raise ValueError(f"Unknown requirement code: {code}.")
    opp = conn.execute(
        "SELECT document_path FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    if opp is None:
        raise ValueError(f"No opportunity with id {opp_id}.")
    quote = quote.strip() if isinstance(quote, str) else None
    if verdict == "PASS" and not quote:
        raise ValueError("PASS requires a verbatim quote from the solicitation.")
    verified = None
    if quote:
        text = read_opp_text(conn, opp_id)
        if text is not None:
            if not quote_in_text(quote, text):
                raise ValueError(
                    "Quote not found verbatim in the solicitation document;"
                    " refusing to store it."
                )
            verified = 1
    conn.execute(
        "INSERT INTO evaluations"
        " (opportunity_id, requirement_code, verdict, quote, verified,"
        "  note, evaluated_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(opportunity_id, requirement_code) DO UPDATE SET"
        " verdict = excluded.verdict, quote = excluded.quote,"
        " verified = excluded.verified, note = excluded.note,"
        " evaluated_by = excluded.evaluated_by",
        (opp_id, code, verdict, quote or None, verified, note, evaluated_by),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM evaluations WHERE opportunity_id = ? AND requirement_code = ?",
        (opp_id, code),
    ).fetchone()
    assert row is not None
    return row
