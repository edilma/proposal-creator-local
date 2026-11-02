"""Reusable answer library: human-written answers future drafts pull from.

Separate from the company profile. Each entry has a label/topic, the answer
text, a "last confirmed" date, an optional "valid until" (expiry) date, and
an active/retired status. One answer per label — saving a label that already
exists edits that entry, never creates a duplicate (labels match
case-insensitively).

Freshness: an answer is expired when today is past its valid-until date
(the valid-until day itself still counts as valid), and stale when its
last-confirmed date is older than the threshold
(`PROPOSAL_ANSWER_STALE_DAYS`, default 90 days). Expired or stale answers
are still reused at draft time, but flagged "confirm still current".
"""

from __future__ import annotations

import os
import sqlite3
import datetime as dt
from typing import Mapping

ANSWERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE COLLATE NOCASE,
    answer_text TEXT NOT NULL,
    last_confirmed TEXT NOT NULL,
    valid_until TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

STATUSES = ("active", "retired")

DEFAULT_STALE_DAYS = 90
STALE_DAYS_ENV_VAR = "PROPOSAL_ANSWER_STALE_DAYS"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the answers table if needed (migrates older DB files)."""
    conn.execute(ANSWERS_TABLE_SQL)
    conn.commit()


def stale_days_from_env(environ: Mapping[str, str] | None = None) -> int:
    """Staleness threshold in days; invalid or missing values use the default."""
    raw = (environ if environ is not None else os.environ).get(STALE_DAYS_ENV_VAR, "")
    try:
        days = int(str(raw).strip())
    except (ValueError, TypeError):
        return DEFAULT_STALE_DAYS
    return days if days > 0 else DEFAULT_STALE_DAYS


def today_iso() -> str:
    return dt.date.today().isoformat()


def _parse_date(value: str, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field} must be YYYY-MM-DD (got {value!r}).") from None


def _today(value: str | dt.date | None) -> dt.date:
    if value is None:
        return dt.date.today()
    if isinstance(value, dt.date):
        return value
    return _parse_date(value, "today")


def is_expired(entry: Mapping, today: str | dt.date | None = None) -> bool:
    """True when today is past valid-until (the day itself still counts)."""
    valid_until = entry["valid_until"] if "valid_until" in entry.keys() else None
    if not valid_until:
        return False
    return _parse_date(valid_until, "valid_until") < _today(today)


def is_stale(
    entry: Mapping,
    today: str | dt.date | None = None,
    stale_days: int | None = None,
) -> bool:
    """True when last-confirmed is older than the threshold.

    A missing or unparseable last-confirmed date counts as stale: unknown
    age must flag, never pass silently.
    """
    days = DEFAULT_STALE_DAYS if stale_days is None else stale_days
    try:
        confirmed = _parse_date(entry["last_confirmed"], "last_confirmed")
    except (KeyError, ValueError):
        return True
    return (_today(today) - confirmed).days > days


def freshness(
    entry: Mapping,
    today: str | dt.date | None = None,
    stale_days: int | None = None,
) -> str:
    """One of "expired", "stale", or "fresh" (expired wins when both apply)."""
    if is_expired(entry, today):
        return "expired"
    if is_stale(entry, today, stale_days):
        return "stale"
    return "fresh"


def _clean_label(label: str) -> str:
    label = (label or "").strip()
    if not label:
        raise ValueError("Answer label must not be empty.")
    return label


def _clean_text(answer_text: str) -> str:
    answer_text = (answer_text or "").strip()
    if not answer_text:
        raise ValueError("Answer text must not be empty.")
    return answer_text


def _clean_status(status: str) -> str:
    status = (status or "").strip().lower()
    if status not in STATUSES:
        raise ValueError(f"Status must be one of {', '.join(STATUSES)}.")
    return status


def list_answers(
    conn: sqlite3.Connection, include_retired: bool = True
) -> list[sqlite3.Row]:
    ensure_schema(conn)
    if include_retired:
        return list(conn.execute("SELECT * FROM answers ORDER BY label"))
    return list(
        conn.execute("SELECT * FROM answers WHERE status = 'active' ORDER BY label")
    )


def list_active_answers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list_answers(conn, include_retired=False)


def get_answer(conn: sqlite3.Connection, answer_id: int) -> sqlite3.Row | None:
    ensure_schema(conn)
    return conn.execute(
        "SELECT * FROM answers WHERE id = ?", (answer_id,)
    ).fetchone()


def get_by_label(conn: sqlite3.Connection, label: str) -> sqlite3.Row | None:
    ensure_schema(conn)
    return conn.execute(
        "SELECT * FROM answers WHERE label = ? COLLATE NOCASE",
        ((label or "").strip(),),
    ).fetchone()


def save_answer(
    conn: sqlite3.Connection,
    label: str,
    answer_text: str,
    last_confirmed: str | None = None,
    valid_until: str | None = None,
    status: str | None = None,
) -> sqlite3.Row:
    """Insert or update one answer by label (one answer per topic).

    Saving re-confirms the entry, so an omitted last-confirmed date means
    today. An omitted valid-until/status keeps the existing value on update
    (NULL/"active" on insert); to clear a valid-until, use update_answer
    with "". Returns the stored row.
    """
    ensure_schema(conn)
    label = _clean_label(label)
    answer_text = _clean_text(answer_text)
    confirmed = (last_confirmed or "").strip() or today_iso()
    _parse_date(confirmed, "last_confirmed")
    valid_clean = (valid_until or "").strip() or None
    if valid_clean is not None:
        _parse_date(valid_clean, "valid_until")
    status_clean = _clean_status(status) if status is not None else None

    existing = get_by_label(conn, label)
    if existing is None:
        conn.execute(
            "INSERT INTO answers (label, answer_text, last_confirmed,"
            " valid_until, status) VALUES (?, ?, ?, ?, ?)",
            (label, answer_text, confirmed, valid_clean, status_clean or "active"),
        )
    else:
        conn.execute(
            "UPDATE answers SET answer_text = ?, last_confirmed = ?,"
            " valid_until = ?, status = ?,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (
                answer_text,
                confirmed,
                (
                    existing["valid_until"]
                    if valid_clean is None and valid_until is None
                    else valid_clean
                ),
                existing["status"] if status_clean is None else status_clean,
                existing["id"],
            ),
        )
    conn.commit()
    row = get_by_label(conn, label)
    assert row is not None
    return row


def update_answer(
    conn: sqlite3.Connection,
    answer_id: int,
    label: str | None = None,
    answer_text: str | None = None,
    last_confirmed: str | None = None,
    valid_until: str | None = None,
    status: str | None = None,
) -> sqlite3.Row:
    """Edit one entry by id; only given fields change.

    None leaves a field unchanged; "" clears valid_until or re-confirms
    last-confirmed to today. Returns the stored row.
    """
    ensure_schema(conn)
    existing = get_answer(conn, answer_id)
    if existing is None:
        raise ValueError(f"No answer with id {answer_id}.")
    new_label = _clean_label(label) if label is not None else existing["label"]
    new_text = _clean_text(answer_text) if answer_text is not None else existing["answer_text"]
    new_confirmed = existing["last_confirmed"]
    if last_confirmed is not None:
        new_confirmed = (last_confirmed or "").strip() or today_iso()
        _parse_date(new_confirmed, "last_confirmed")
    new_valid = existing["valid_until"]
    if valid_until is not None:
        new_valid = str(valid_until).strip() or None
        if new_valid is not None:
            _parse_date(new_valid, "valid_until")
    new_status = _clean_status(status) if status is not None else existing["status"]
    try:
        conn.execute(
            "UPDATE answers SET label = ?, answer_text = ?, last_confirmed = ?,"
            " valid_until = ?, status = ?,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (new_label, new_text, new_confirmed, new_valid, new_status, answer_id),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"An answer labeled {new_label!r} already exists.") from exc
    conn.commit()
    row = get_answer(conn, answer_id)
    assert row is not None
    return row


def retire_answer(conn: sqlite3.Connection, answer_id: int) -> sqlite3.Row:
    """Mark one entry retired (kept for history, unused by drafts)."""
    return update_answer(conn, answer_id, status="retired")
