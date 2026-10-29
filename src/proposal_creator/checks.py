"""Deterministic checks on an opportunity. Pure, local, no model calls.

Levels, worst first: "fail", "warn", "info", "ok". Each check returns a dict
with keys: id, label, result, detail.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any


def _field(opp: Any, name: str) -> Any:
    """Read a field from a dict or sqlite3.Row; None when absent."""
    try:
        return opp[name]
    except (KeyError, IndexError, TypeError):
        return None


def _parse_day(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def check_deadline(opp: Any, today: date | None = None) -> dict:
    today = today or date.today()
    raw = _field(opp, "close_date")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {
            "id": "deadline",
            "label": "Deadline",
            "result": "warn",
            "detail": "No close date recorded.",
        }
    close = _parse_day(raw)
    if close is None:
        return {
            "id": "deadline",
            "label": "Deadline",
            "result": "warn",
            "detail": f"Close date {raw!r} is not a YYYY-MM-DD date.",
        }
    delta = (close - today).days
    if delta < 0:
        return {
            "id": "deadline",
            "label": "Deadline",
            "result": "fail",
            "detail": f"Deadline passed {_plural(-delta, 'day')} ago (closed {close}).",
        }
    if delta == 0:
        return {
            "id": "deadline",
            "label": "Deadline",
            "result": "warn",
            "detail": "Closes today.",
        }
    if delta <= 7:
        return {
            "id": "deadline",
            "label": "Deadline",
            "result": "warn",
            "detail": f"Closes in {_plural(delta, 'day')} ({close}).",
        }
    return {
        "id": "deadline",
        "label": "Deadline",
        "result": "ok",
        "detail": f"{_plural(delta, 'day')} remaining (closes {close}).",
    }


def check_opening(opp: Any, today: date | None = None) -> dict | None:
    """Only speaks up when the solicitation is not open yet; else None."""
    today = today or date.today()
    opened = _parse_day(_field(opp, "open_date"))
    if opened is not None and opened > today:
        return {
            "id": "opening",
            "label": "Opening",
            "result": "info",
            "detail": f"Opens {opened} (not open yet).",
        }
    return None


def check_document(opp: Any) -> dict:
    raw = _field(opp, "document_path")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {
            "id": "document",
            "label": "Document",
            "result": "warn",
            "detail": "No document attached yet.",
        }
    if Path(str(raw)).exists():
        return {
            "id": "document",
            "label": "Document",
            "result": "ok",
            "detail": f"Document found: {raw}.",
        }
    return {
        "id": "document",
        "label": "Document",
        "result": "warn",
        "detail": f"Document not found: {raw}.",
    }


def run_checks(opp: Any, today: date | None = None) -> list[dict]:
    results = [check_deadline(opp, today)]
    opening = check_opening(opp, today)
    if opening is not None:
        results.append(opening)
    results.append(check_document(opp))
    return results


def summarize(results: list[dict]) -> str:
    fails = sum(1 for r in results if r.get("result") == "fail")
    warns = sum(1 for r in results if r.get("result") == "warn")
    if fails:
        text = _plural(fails, "failed")
        if warns:
            text += f", {_plural(warns, 'warning')}"
        return text
    if warns:
        return _plural(warns, "warning")
    return "ok"
