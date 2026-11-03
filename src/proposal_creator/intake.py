"""Drop-folder intake: stub records plus per-opportunity document folders.

The human drops solicitation files into `drop/` (for portals without an API).
`scan_drop` turns each file the database hasn't seen into a stub record with
status 'new'; `scan_folders` attaches files inside `drop/opp-<id>/` folders
to the matching opportunity. Matching is by stored document path, so
re-scans add nothing. Legacy single-document records are adopted into the
documents table on first scan.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .db import DEFAULT_DB_PATH, add_opportunity, connect, get_opportunity, init_db
from .evals import read_doc_text
from .notices import normalize_text, number_core

DEFAULT_DROP_DIR = Path("drop")

OPP_FOLDER = re.compile(r"opp-(\d+)\Z")


def _store_path(path: Path, drop_dir: Path) -> str:
    """Store the path relative to the drop dir's parent when possible.

    Keeps the database portable (no machine-specific absolute paths) while the
    check for file existence still resolves from the project root.
    """
    try:
        return str(path.relative_to(drop_dir.parent))
    except ValueError:
        return str(path)


def scan_drop(
    drop_dir: Path | str = DEFAULT_DROP_DIR,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple[list[dict], list[str]]:
    """Scan the folder; return (added stub dicts, skipped path strings)."""
    drop_dir = Path(drop_dir)
    init_db(db_path)
    if not drop_dir.is_dir():
        return [], []
    added: list[dict] = []
    skipped: list[str] = []
    with closing(connect(db_path)) as conn:
        adopt_legacy_paths(conn)
        known = {
            r["document_path"]
            for r in conn.execute(
                "SELECT document_path FROM opportunities WHERE document_path IS NOT NULL"
            )
        }
        for path in sorted(drop_dir.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            stored = _store_path(path, drop_dir)
            if stored in known:
                skipped.append(stored)
                continue
            stub = {
                "source": "drop",
                "external_id": None,
                "title": path.stem,
                "agency": None,
                "url": None,
                "open_date": None,
                "close_date": None,
                "status": "new",
                "verdict": None,
                "blockers": None,
                "document_path": stored,
                "notes": "Stub from drop-folder intake; dates and details need review.",
            }
            add_opportunity(conn, stub)
            known.add(stored)
            added.append(stub)
    return added, skipped


def add_document(
    conn: sqlite3.Connection, opp_id: int, path: str, source: str = "drop"
) -> bool:
    """Register one document on an opportunity. True when newly added."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO documents (opportunity_id, path, source)"
        " VALUES (?, ?, ?)",
        (opp_id, path, source),
    )
    conn.commit()
    return cursor.rowcount > 0


def adopt_legacy_paths(conn: sqlite3.Connection) -> int:
    """Register pre-documents-table `document_path` values. Returns count."""
    adopted = 0
    for row in conn.execute(
        "SELECT id, document_path FROM opportunities"
        " WHERE document_path IS NOT NULL"
    ):
        have = conn.execute(
            "SELECT 1 FROM documents WHERE opportunity_id = ?", (row["id"],)
        ).fetchone()
        if have is None and add_document(conn, row["id"], row["document_path"]):
            adopted += 1
    return adopted


def confirm_documents(conn: sqlite3.Connection, opp_id: int) -> int:
    """Flag documents printing the opportunity's agency number.

    A document is confirmed as belonging to the opportunity when the
    digit core of the stored agency number appears in its text
    (spacing/slashes/dashes-insensitive). Returns newly confirmed count.
    """
    row = get_opportunity(conn, opp_id)
    if row is None:
        return 0
    core = number_core(row["agency_number"])
    if len(core) < 4:
        return 0
    confirmed = 0
    for doc in conn.execute(
        "SELECT id, path FROM documents"
        " WHERE opportunity_id = ? AND confirmed IS NULL",
        (opp_id,),
    ):
        text = read_doc_text(doc["path"])
        if text and core in normalize_text(text):
            conn.execute(
                "UPDATE documents SET confirmed = 1 WHERE id = ?", (doc["id"],)
            )
            confirmed += 1
    conn.commit()
    return confirmed


def list_documents(conn: sqlite3.Connection, opp_id: int) -> list:
    """Documents for an opportunity; legacy path shown until adopted."""
    rows: list = list(
        conn.execute(
            "SELECT * FROM documents WHERE opportunity_id = ? ORDER BY path",
            (opp_id,),
        )
    )
    if rows:
        return rows
    opp = get_opportunity(conn, opp_id)
    if opp is not None and opp["document_path"]:
        return [{
            "id": None,
            "opportunity_id": opp_id,
            "path": opp["document_path"],
            "source": "drop",
            "confirmed": None,
            "added_at": None,
        }]
    return []


def scan_folders(
    drop_dir: Path | str = DEFAULT_DROP_DIR,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> tuple[list[dict], list[str]]:
    """Attach `drop/opp-<id>/` files; return (attached, skipped) details."""
    drop_dir = Path(drop_dir)
    init_db(db_path)
    if not drop_dir.is_dir():
        return [], []
    attached: list[dict] = []
    skipped: list[str] = []
    with closing(connect(db_path)) as conn:
        adopt_legacy_paths(conn)
        for folder in sorted(drop_dir.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            match = OPP_FOLDER.fullmatch(folder.name)
            if match is None:
                skipped.append(f"{folder.name}/ (not opp-<id>)")
                continue
            opp_id = int(match.group(1))
            if get_opportunity(conn, opp_id) is None:
                skipped.append(f"{folder.name}/ (no opportunity {opp_id})")
                continue
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.name.startswith("."):
                    continue
                stored = _store_path(path, drop_dir)
                if add_document(conn, opp_id, stored):
                    attached.append({"opportunity_id": opp_id, "path": stored})
                else:
                    skipped.append(stored)
            confirm_documents(conn, opp_id)
    return attached, skipped
