"""Requirements checklist and per-opportunity evaluations.

Enforces the non-negotiable at the storage layer: a PASS is stored only with
a quote, and any stored quote must appear verbatim in the solicitation text
when that text is available. Quote verification supports plain-text documents
(.txt/.md) and text PDFs; anything else — or a PDF with no extractable text,
such as a scan — stores the quote as unverified.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from pypdf import PdfReader

DEFAULT_REQUIREMENTS_PATH = Path("data/requirements_gov.json")
VERDICTS = ("PASS", "FAIL", "UNKNOWN")
READABLE_SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


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
        text = read_doc_text(opp["document_path"])
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
