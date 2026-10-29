"""Drop-folder intake: one stub opportunity record per new file.

The human drops solicitation files into `drop/` (for portals without an API);
`scan_drop` turns each file the database hasn't seen into a stub record with
status 'new'. Matching is by stored document path, so re-scans add nothing.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from .db import DEFAULT_DB_PATH, add_opportunity, connect, init_db

DEFAULT_DROP_DIR = Path("drop")


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
