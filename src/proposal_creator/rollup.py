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
from .evals import list_evaluations

CAN_BID = "CAN BID"
NEEDS_INFO = "NEEDS INFO"
CANNOT_BID = "CANNOT BID"


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
        e["requirement_code"] for e in evaluations if e["verdict"] == "FAIL"
    )
    if failed:
        return CANNOT_BID, f"Failed: {', '.join(failed)}."
    unknown = sorted(
        e["requirement_code"] for e in evaluations if e["verdict"] == "UNKNOWN"
    )
    if unknown:
        return NEEDS_INFO, f"Needs info: {', '.join(unknown)}."
    return CAN_BID, ""


def apply_rollup(
    conn: sqlite3.Connection, opp_id: int, today: date | None = None
) -> tuple[str | None, str]:
    opp = get_opportunity(conn, opp_id)
    if opp is None:
        raise ValueError(f"No opportunity with id {opp_id}.")
    verdict, blocker = compute_rollup(opp, list_evaluations(conn, opp_id), today)
    if verdict is not None:
        conn.execute(
            "UPDATE opportunities SET verdict = ?, blockers = ?,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (verdict, blocker, opp_id),
        )
        conn.commit()
    return verdict, blocker
