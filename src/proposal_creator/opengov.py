"""OpenGov portal fetch: parse a saved project page, download attachments.

OpenGov answers scripts with HTTP 403 and sometimes shows human
verification, so the owner saves the project page from their browser
(view-source or plain save both work) and this module takes it from there:
extract the embedded `window.__data` project record, backfill identifiers
and dates, and download the presigned attachment URLs into `drop/opp-<id>/`.

Signed URLs expire — a stale save fails downloads with a "re-save the page"
message, never silently.
"""

from __future__ import annotations

import datetime as dt
import html as html_lib
import json
import re
import sqlite3
import urllib.request
from collections.abc import Callable
from pathlib import Path

WRAPPER_MARKERS = ('class="line-content"', 'class="source-container"')
DATA_BLOB = "window.__data="


class PortalError(Exception):
    pass


def unwrap_view_source(text: str) -> str:
    """Restore the original page from a Chrome view-source save.

    Plain saves pass through untouched; wrapper saves are detected by
    their chrome and rebuilt line by line (tags stripped, entities
    decoded — the signed URLs need their `&amp;` back as `&`).
    """
    if not any(marker in text for marker in WRAPPER_MARKERS):
        return text
    cells = re.findall(r'<td class="line-content">(.*?)</td>', text, re.DOTALL)
    if not cells:
        raise PortalError(
            "This looks like a view-source save but has no source lines in it."
        )
    return "\n".join(
        html_lib.unescape(re.sub(r"<[^>]+>", "", cell)) for cell in cells
    )


def _balanced_end(text: str, start: int) -> int:
    """Index just past the closer matching the opener at `start`."""
    open_c = text[start]
    close_c = "}" if open_c == "{" else "]"
    depth, pos, in_string, escaped = 0, start, False, False
    while pos < len(text):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == open_c:
            depth += 1
        elif char == close_c:
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    raise PortalError("Embedded project data is truncated.")


def extract_project(html_text: str) -> dict:
    """Project record out of saved page HTML (wrapper or plain)."""
    original = unwrap_view_source(html_text)
    blob_at = original.find(DATA_BLOB)
    if blob_at < 0:
        raise PortalError(
            "No embedded OpenGov data found — is this a saved project page?"
        )
    key_at = original.find('"project":', blob_at)
    if key_at < 0:
        raise PortalError("Embedded data has no project record.")
    brace = original.find("{", key_at)
    fragment = original[brace:_balanced_end(original, brace)]
    try:
        project = json.loads(fragment)
    except ValueError:
        raise PortalError("Embedded project data would not parse.") from None
    if not isinstance(project, dict):
        raise PortalError("Embedded project data has an unexpected shape.")
    return project


def _day(value: object) -> str | None:
    """'2026-12-03T21:00:00.000Z' -> '2026-12-03'; anything else -> None."""
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return None
    try:
        return dt.date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def _summary_item(item: dict, kind: str) -> dict:
    return {
        "kind": kind,
        "filename": item.get("filename") or "",
        "url": item.get("url") or "",
        "appendix": item.get("appendixId") or "",
        "extension": item.get("fileExtension") or "",
    }


def summarize_project(project: dict) -> dict:
    """Identifiers, dates, and attachments distilled from a project record.

    Both file sources are enumerated: the uploaded `attachments` list and
    the generated `documentAttachment` record (dict, list, or absent).
    """
    attachments = []
    for item in project.get("attachments") or []:
        if isinstance(item, dict):
            attachments.append(_summary_item(item, "attachment"))
    generated = project.get("documentAttachment")
    if isinstance(generated, dict):
        candidates = [generated]
    elif isinstance(generated, list):
        candidates = generated
    else:
        candidates = []
    for item in candidates:
        if isinstance(item, dict):
            attachments.append(_summary_item(item, "generated"))
    portal_id = project.get("id")
    return {
        "portal_number": str(portal_id) if portal_id is not None else None,
        "agency_number": project.get("financialId") or None,
        "title": project.get("title") or None,
        "open_date": _day(project.get("releaseProjectDate")),
        "close_date": _day(project.get("proposalDeadline")),
        "attachments": attachments,
    }


def safe_filename(
    name: str, appendix: str, extension: str, n: int
) -> str:
    """A filesystem-safe basename; never empty, never a path."""
    base = Path(name or "").name.strip()
    if not base:
        suffix = f".{extension}" if extension else ""
        base = f"attachment-{appendix or n}{suffix}"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip()
    return base or f"attachment-{n}"


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _dedupe_name(filename: str, taken: set[str]) -> str:
    """Append " (2)", " (3)", ... when a batch reuses a filename."""
    if filename.casefold() not in taken:
        taken.add(filename.casefold())
        return filename
    stem, dot, suffix = filename.rpartition(".")
    if not stem:
        stem, dot, suffix = filename, "", ""
    n = 2
    while f"{stem} ({n}){dot}{suffix}".casefold() in taken:
        n += 1
    unique = f"{stem} ({n}){dot}{suffix}"
    taken.add(unique.casefold())
    return unique


def download_attachments(
    attachments: list[dict],
    dest_dir: Path | str,
    opener: Callable[[str], bytes] | None = None,
) -> list[dict]:
    """Fetch each attachment URL into `dest_dir`.

    Returns per-file results ({filename, path, status, error}); a file is
    never half-written, existing names are kept as-is, same-name batch
    mates are numbered, and failures name the fix (re-save the page:
    signed URLs expire).
    """
    opener = opener or _fetch_bytes
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    results = []
    taken: set[str] = set()
    for n, item in enumerate(attachments, 1):
        filename = _dedupe_name(
            safe_filename(
                item.get("filename") or "", item.get("appendix") or "",
                item.get("extension") or "", n,
            ),
            taken,
        )
        path = dest_dir / filename
        kind = item.get("kind") or "attachment"
        if not item.get("url"):
            results.append({
                "filename": filename, "path": str(path), "kind": kind,
                "status": "failed", "error": "no download URL in the page",
            })
            continue
        if path.exists():
            results.append({
                "filename": filename, "path": str(path), "kind": kind,
                "status": "skipped-exists", "error": None,
            })
            continue
        try:
            data = opener(item["url"])
        except Exception as exc:
            results.append({
                "filename": filename, "path": str(path), "kind": kind,
                "status": "failed",
                "error": (
                    f"download failed ({exc}); the page save may be stale —"
                    " re-save it, signed URLs expire."
                ),
            })
            continue
        tmp_path = path.with_name(path.name + ".part")
        try:
            tmp_path.write_bytes(data)
            tmp_path.replace(path)
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            results.append({
                "filename": filename, "path": str(path), "kind": kind,
                "status": "failed", "error": f"cannot write file: {exc}",
            })
            continue
        results.append({
            "filename": filename, "path": str(path), "kind": kind,
            "status": "downloaded", "error": None,
        })
    return results


def apply_portal_data(
    conn: sqlite3.Connection, opp_id: int, summary: dict
) -> dict:
    """Backfill empty identifier/date/title fields; returns what changed."""
    row = conn.execute(
        "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    if row is None:
        raise PortalError(f"No opportunity with id {opp_id}.")
    filled = {
        key: summary[key]
        for key in (
            "portal_number", "agency_number", "title", "open_date",
            "close_date",
        )
        if summary.get(key) and not row[key]
    }
    if filled:
        sets = ", ".join(f"{key} = :{key}" for key in filled)
        filled["id"] = opp_id
        conn.execute(
            f"UPDATE opportunities SET {sets},"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = :id",
            filled,
        )
        conn.commit()
    return {key: value for key, value in filled.items() if key != "id"}
