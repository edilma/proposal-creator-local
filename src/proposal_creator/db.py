"""SQLite storage for opportunities. One file, no server (see decision 002)."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

DEFAULT_DB_PATH = Path(os.environ.get("PROPOSAL_DB", "data/opportunities.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT,
    title TEXT NOT NULL,
    agency TEXT,
    url TEXT,
    open_date TEXT,
    close_date TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    verdict TEXT,
    blockers TEXT,
    document_path TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source, external_id)
);
"""

SEED_EXAMPLES = [
    {
        "source": "sam.gov",
        "external_id": "DEMO-0001",
        "title": "IT Help Desk Support Services (example)",
        "agency": "Department of Veterans Affairs (example)",
        "url": None,
        "open_date": "2026-10-01",
        "close_date": "2026-11-20",
        "status": "new",
        "verdict": None,
        "blockers": None,
        "document_path": None,
        "notes": "Seed example row for slice 1 demo data.",
    },
    {
        "source": "drop",
        "external_id": "DEMO-0002",
        "title": "Web-Based Time Study Platform (example)",
        "agency": "Florida Department of Revenue (example)",
        "url": None,
        "open_date": "2026-10-15",
        "close_date": "2026-12-05",
        "status": "new",
        "verdict": None,
        "blockers": None,
        "document_path": None,
        "notes": "Seed example row for slice 1 demo data.",
    },
]


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> Path:
    db_path = Path(db_path)
    with closing(connect(db_path)) as conn:
        conn.executescript(SCHEMA)
    return db_path


def add_opportunity(conn: sqlite3.Connection, opp: dict) -> int:
    columns = [
        "source",
        "external_id",
        "title",
        "agency",
        "url",
        "open_date",
        "close_date",
        "status",
        "verdict",
        "blockers",
        "document_path",
        "notes",
    ]
    placeholders = ", ".join(f":{c}" for c in columns)
    cursor = conn.execute(
        f"INSERT OR IGNORE INTO opportunities ({', '.join(columns)})"
        f" VALUES ({placeholders})",
        {c: opp.get(c) for c in columns},
    )
    conn.commit()
    return cursor.lastrowid or 0


def list_opportunities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM opportunities ORDER BY id"))


def seed_examples(db_path: Path | str = DEFAULT_DB_PATH) -> int:
    """Insert the two demo rows; skips ones already present. Returns rows added."""
    init_db(db_path)
    added = 0
    with closing(connect(db_path)) as conn:
        for opp in SEED_EXAMPLES:
            if add_opportunity(conn, opp):
                added += 1
    return added
