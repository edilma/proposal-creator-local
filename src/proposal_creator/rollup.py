"""Overall verdict roll-up: pure logic over stored evaluations + deadline.

No model calls. CAN BID / NEEDS INFO / CANNOT BID is written to the
opportunity's Verdict/Blockers fields at the end of evaluate runs and by the
rollup command.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from .checks import check_deadline
from .db import get_opportunity
from .evals import list_evaluations, list_requirements

CAN_BID = "CAN BID"
NEEDS_INFO = "NEEDS INFO"
CANNOT_BID = "CANNOT BID"

NOTE_CAP = 300


def _optional(entry: object, key: str) -> str | None:
    try:
        value = entry[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    return value


def _short_note(note: object) -> str | None:
    if note is None or not str(note).strip():
        return None
    text = str(note).strip()
    if len(text) > NOTE_CAP:
        return text[:NOTE_CAP].rstrip() + "…"
    return text


def _finish(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def _describe_blockers(entries: list, verb: str, legacy: str) -> str:
    """One readable line per blocking evaluation, joined and finished."""
    enriched = [
        e for e in entries
        if _optional(e, "question") or _optional(e, "note")
    ]
    if not enriched:
        return legacy
    parts = []
    for entry in sorted(entries, key=lambda e: e["requirement_code"]):
        code = entry["requirement_code"]
        question = _optional(entry, "question")
        note = _short_note(_optional(entry, "note"))
        part = f"{verb} {code}"
        if question:
            part += f" ({question})"
        if note:
            part += f": {note}"
        parts.append(part)
    return _finish("; ".join(parts))


def compute_rollup(
    opp: object, evaluations: list, today: date | None = None
) -> tuple[str | None, str]:
    """Return (verdict, blocker). None verdict = no evaluations: write nothing."""
    evaluations = list(evaluations)
    if not evaluations:
        return None, ""
    deadline = check_deadline(opp, today)
    if deadline["result"] == "fail":
        return CANNOT_BID, deadline["detail"]
    failed = sorted(
        (e for e in evaluations if e["verdict"] == "FAIL"),
        key=lambda e: e["requirement_code"],
    )
    if failed:
        codes = ", ".join(e["requirement_code"] for e in failed)
        return CANNOT_BID, _describe_blockers(failed, "Failed", f"Failed: {codes}.")
    unknown = sorted(
        (e for e in evaluations if e["verdict"] == "UNKNOWN"),
        key=lambda e: e["requirement_code"],
    )
    if unknown:
        codes = ", ".join(e["requirement_code"] for e in unknown)
        return NEEDS_INFO, _describe_blockers(
            unknown, "Needs info", f"Needs info: {codes}."
        )
    return CAN_BID, ""


def apply_rollup(
    conn: sqlite3.Connection, opp_id: int, today: date | None = None
) -> tuple[str | None, str]:
    opp = get_opportunity(conn, opp_id)
    if opp is None:
        raise ValueError(f"No opportunity with id {opp_id}.")
    questions = {
        req["code"]: req["question"] for req in list_requirements(conn)
    }
    evaluations = [
        {**dict(row), "question": questions.get(row["requirement_code"])}
        for row in list_evaluations(conn, opp_id)
    ]
    verdict, blocker = compute_rollup(opp, evaluations, today)
    if verdict is not None:
        conn.execute(
            "UPDATE opportunities SET verdict = ?, blockers = ?,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (verdict, blocker, opp_id),
        )
        conn.commit()
    return verdict, blocker
