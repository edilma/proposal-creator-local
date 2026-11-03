"""Email notice intake: parse solicitation notices, match to opportunities.

Two senders only (see decision 022): MFMP (dms.myflorida.com) and OpenGov
(opengov.com). Anything else is skipped, never processed. Parsing is
stdlib `email` plus small format-specific extractors shaped by the
`samples/*.eml` fixtures — formats are never guessed.

A parsed notice is matched to an opportunity: portal number first, then
agency number (stored or printed in an attached document), else a new stub
is created. Every processed message id is recorded in `intake_seen`, so a
repeat delivery never becomes a second opportunity. Non-new-notice mail
(addendums and anything unrecognized) is skipped and logged until the
addendum step is built.
"""

from __future__ import annotations

import datetime as dt
import email
import email.policy
import html as html_lib
import re
import sqlite3
from collections.abc import Callable
from email.message import EmailMessage

MFMP_SENDER = "dms.myflorida.com"
OPENGOV_SENDER = "opengov.com"

MFMP_NEW_PREFIX = "Advertisement Posted - "
OPENGOV_NEW_PREFIX = "New Opportunity Issued by "


def number_core(value: str | None) -> str:
    """Digit core of an identifier, for cross-format matching.

    "DOR RFP #26/27-04", "(RFP) 26/27-04", and "RFP_26-27-04_FINAL" all
    reduce to "262704": spacing, slashes, dashes, and prefixes fall away.
    """
    return "".join(re.findall(r"\d", value or ""))


def normalize_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _bodies(msg: EmailMessage) -> tuple[str, str]:
    """Return (plain_text, html_text) decoded bodies, "" when absent."""
    plain, html_text = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            try:
                content = part.get_content()
            except (ValueError, LookupError):
                continue
            if not isinstance(content, str):
                continue
            if part.get_content_type() == "text/plain" and not plain:
                plain = content
            elif part.get_content_type() == "text/html" and not html_text:
                html_text = content
    else:
        try:
            content = msg.get_content()
        except (ValueError, LookupError):
            content = ""
        if isinstance(content, str):
            if msg.get_content_type() == "text/html":
                html_text = content
            else:
                plain = content
    return plain, html_text


def _mfmp_field(html_text: str, label: str) -> str | None:
    match = re.search(
        r"<b>\s*" + re.escape(label) + r":</b>\s*(.*?)\s*(?:<br>|</p>|</font>)",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return html_lib.unescape(text).strip() or None


def _mfmp_link(html_text: str, label: str) -> tuple[str | None, str | None]:
    """Return (link_text, href) for the link inside an MFMP field."""
    match = re.search(
        r"<b>\s*" + re.escape(label) + r":</b>\s*(.*?)\s*(?:<br>|</p>|</font>)",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, None
    link = re.search(
        r'<a\s+href="([^"]+)">(.*?)</a>', match.group(1),
        re.IGNORECASE | re.DOTALL,
    )
    if not link:
        return None, None
    return (
        html_lib.unescape(re.sub(r"<[^>]+>", "", link.group(2))).strip() or None,
        html_lib.unescape(link.group(1)).strip() or None,
    )


def _iso_date(value: str | None) -> str | None:
    """'Aug 20, 2026' -> '2026-08-20'; anything unparseable -> None."""
    if not value:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_mfmp(
    msg: EmailMessage, message_id: str, subject: str, plain: str, html_text: str
) -> dict:
    notice: dict = {
        "source": "mfmp",
        "message_id": message_id,
        "subject": subject,
        "kind": "new",
        "skip_reason": None,
        "portal_number": None,
        "agency_number": None,
        "title": None,
        "agency": None,
        "open_date": None,
        "close_date": None,
        "contact": None,
        "url": None,
        "summary": None,
    }
    if not subject.startswith(MFMP_NEW_PREFIX):
        # Addendums and anything else wait for the addendum step.
        notice["kind"] = "skip"
        notice["skip_reason"] = f"not a new-advertisement notice: {subject!r}"
        return notice
    notice["portal_number"] = subject[len(MFMP_NEW_PREFIX):].strip() or None
    notice["agency_number"] = _mfmp_field(html_text, "Agency Advertisement Number")
    notice["title"] = _mfmp_field(html_text, "Title")
    notice["agency"] = _mfmp_field(html_text, "Organization")
    notice["open_date"] = _iso_date(_mfmp_field(html_text, "Open Date"))
    notice["close_date"] = _iso_date(_mfmp_field(html_text, "Close Date"))
    contact = _mfmp_field(html_text, "Agency Contact")
    email_addr = _mfmp_field(html_text, "Email")
    if contact or email_addr:
        notice["contact"] = ", ".join(p for p in (contact, email_addr) if p)
    _, href = _mfmp_link(html_text, "Advertisement Number")
    notice["url"] = href
    return notice


def _strip_tags(html_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _opengov_links(html_text: str) -> list[tuple[str, str]]:
    """All (href, anchor_text) pairs in the HTML part, in order."""
    pairs = []
    for match in re.finditer(
        r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html_text,
        re.IGNORECASE | re.DOTALL,
    ):
        pairs.append((
            html_lib.unescape(match.group(1)).strip(),
            _strip_tags(match.group(2)),
        ))
    return pairs


def _parse_opengov(
    msg: EmailMessage, message_id: str, subject: str, plain: str, html_text: str
) -> dict:
    notice: dict = {
        "source": "opengov",
        "message_id": message_id,
        "subject": subject,
        "kind": "new",
        "skip_reason": None,
        "portal_number": None,
        "agency_number": None,
        "title": None,
        "agency": None,
        "open_date": None,
        "close_date": None,
        "contact": None,
        "url": None,
        "summary": None,
    }
    if not subject.startswith(OPENGOV_NEW_PREFIX):
        notice["kind"] = "skip"
        notice["skip_reason"] = f"not a new-opportunity notice: {subject!r}"
        return notice
    rest = subject[len(OPENGOV_NEW_PREFIX):]
    city, _, title = rest.partition(": ")
    notice["agency"] = city.strip() or None
    notice["title"] = title.strip() or None
    summary = re.search(
        r"Here is a summary of the project:(.*?)</div>",
        html_text,
        re.IGNORECASE | re.DOTALL,
    )
    if summary:
        notice["summary"] = _strip_tags(summary.group(1)) or None
    # The view-opportunity link shares its shape with the subscription link;
    # anchor text tells them apart (unverified against a real email: the
    # sample's tracking links were removed).
    view_url = None
    for href, anchor in _opengov_links(html_text):
        if "subscription" in anchor.lower():
            continue
        if "opengov.com/ls/click" in href or "procurement.opengov.com" in href:
            view_url = href
            break
    if view_url is None:
        urls = re.findall(r"https?://\S+", plain)
        view_url = urls[0].rstrip(".,)") if urls else None
    notice["url"] = view_url
    return notice


def parse_eml(raw: bytes) -> dict:
    """Parse raw .eml bytes into a notice dict (never raises on format)."""
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except Exception:
        return {
            "source": "unknown",
            "message_id": "",
            "subject": "",
            "kind": "skip",
            "skip_reason": "unparseable message bytes",
        }
    sender = str(msg.get("From", "") or "")
    message_id = str(msg.get("Message-ID", "") or "").strip()
    subject = str(msg.get("Subject", "") or "").strip()
    plain, html_text = _bodies(msg)
    if MFMP_SENDER in sender:
        return _parse_mfmp(msg, message_id, subject, plain, html_text)
    if OPENGOV_SENDER in sender:
        return _parse_opengov(msg, message_id, subject, plain, html_text)
    return {
        "source": "unknown",
        "message_id": message_id,
        "subject": subject,
        "kind": "skip",
        "skip_reason": f"sender not handled: {sender!r}",
    }


def opengov_project_id(url: str | None) -> str | None:
    """Trailing numeric id of a resolved portal URL (.../projects/309982)."""
    if not url:
        return None
    match = re.search(r"/projects/(\d+)", url)
    return match.group(1) if match else None


def resolve_url(
    url: str | None, opener: Callable[[str], str] | None = None
) -> str | None:
    """Follow redirects to the final URL. None on any failure, never raises."""
    if not url:
        return None
    if opener is None:
        import urllib.request

        def opener(target: str) -> str:
            request = urllib.request.Request(
                target, headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.geturl()

    try:
        return opener(url)
    except Exception:
        return None


def resolve_notice_link(
    notice: dict, opener: Callable[[str], str] | None = None
) -> dict:
    """Follow an OpenGov tracking link; keep the portal URL + project id.

    Unresolvable links are kept as-is — the stub still records them.
    """
    if notice.get("source") != "opengov" or notice.get("kind") != "new":
        return notice
    final = resolve_url(notice.get("url"), opener=opener)
    if final and final != notice.get("url"):
        notice["url"] = final
        project = opengov_project_id(final)
        if project:
            notice["portal_number"] = project
    return notice


def _stored_match(conn: sqlite3.Connection, notice: dict) -> sqlite3.Row | None:
    """Hit on a portal or agency number already stored on a record."""
    for column in ("portal_number", "agency_number"):
        value = notice.get(column)
        if not value:
            continue
        hit = conn.execute(
            f"SELECT * FROM opportunities WHERE {column} = ?", (value,)
        ).fetchone()
        if hit is not None:
            return hit
    return None


def find_match(conn: sqlite3.Connection, notice: dict) -> sqlite3.Row | None:
    """Find the opportunity this notice belongs to, if any.

    Portal number first, then agency number (stored, else printed in an
    attached document of an unnumbered opportunity). Returns None when no
    single opportunity matches.
    """
    hit = _stored_match(conn, notice)
    if hit is not None:
        return hit
    matches = _all_doc_matches(conn, notice)
    if len(matches) != 1:
        return None
    return conn.execute(
        "SELECT * FROM opportunities WHERE id = ?", (matches[0],)
    ).fetchone()


def dedup_key(notice: dict) -> str:
    """Stable identity for a notice: message id, else a content hash.

    Both senders include Message-ID; the hash is a backstop so an id-less
    mail can still only ever create one opportunity.
    """
    if notice.get("message_id"):
        return str(notice["message_id"])
    import hashlib
    import json

    fingerprint = json.dumps(
        {k: notice.get(k) for k in sorted(notice)}, sort_keys=True, default=str
    )
    return "content:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def record_seen(
    conn: sqlite3.Connection,
    notice: dict,
    result: str,
    opportunity_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO intake_seen"
        " (message_id, source, subject, result, opportunity_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            dedup_key(notice),
            notice.get("source") or "unknown",
            (notice.get("subject") or "")[:300],
            result,
            opportunity_id,
        ),
    )
    conn.commit()


def already_seen(conn: sqlite3.Connection, message_id: str) -> bool:
    if not message_id:
        return False
    return (
        conn.execute(
            "SELECT 1 FROM intake_seen WHERE message_id = ?", (message_id,)
        ).fetchone()
        is not None
    )


def apply_notice(
    conn: sqlite3.Connection, notice: dict
) -> tuple[str, int | None]:
    """Match or create the opportunity for a notice; returns (result, opp_id).

    Results: "matched" (numbers backfilled onto an existing record),
    "created" (new stub), "skipped" (addendum/unknown/duplicate class),
    "ambiguous" (several unnumbered records print the agency number —
    left for a human; recorded so it is reported once).
    """
    from .db import add_opportunity

    key = dedup_key(notice)
    if already_seen(conn, key):
        row = conn.execute(
            "SELECT result, opportunity_id FROM intake_seen WHERE message_id = ?",
            (key,),
        ).fetchone()
        return (row["result"], row["opportunity_id"]) if row else ("seen", None)
    if notice.get("kind") != "new":
        record_seen(conn, notice, "skipped")
        return "skipped", None
    hit = _stored_match(conn, notice)
    if hit is None:
        matches = _all_doc_matches(conn, notice)
        if len(matches) > 1:
            record_seen(conn, notice, "ambiguous")
            return "ambiguous", None
        if len(matches) == 1:
            hit = conn.execute(
                "SELECT * FROM opportunities WHERE id = ?", (matches[0],)
            ).fetchone()
    if hit is not None:
        backfill = {
            key: notice[key]
            for key in (
                "portal_number", "agency_number", "url", "open_date",
                "close_date",
            )
            if notice.get(key) and not hit[key]
        }
        if backfill:
            sets = ", ".join(f"{key} = :{key}" for key in backfill)
            backfill["id"] = hit["id"]
            conn.execute(
                f"UPDATE opportunities SET {sets},"
                " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
                " WHERE id = :id",
                backfill,
            )
            conn.commit()
        record_seen(conn, notice, "matched", hit["id"])
        return "matched", hit["id"]
    opp_id = add_opportunity(conn, {
        "source": notice["source"],
        "external_id": None,
        "title": notice.get("title") or notice.get("subject") or "Untitled notice",
        "agency": notice.get("agency"),
        "url": notice.get("url"),
        "open_date": notice.get("open_date"),
        "close_date": notice.get("close_date"),
        "status": "new",
        "verdict": None,
        "blockers": None,
        "document_path": None,
        "portal_number": notice.get("portal_number"),
        "agency_number": notice.get("agency_number"),
        "notes": _stub_note(notice),
    })
    record_seen(conn, notice, "created", opp_id)
    return "created", opp_id


def _all_doc_matches(conn: sqlite3.Connection, notice: dict) -> list[int]:
    """Ids of unnumbered opportunities printing the agency number."""
    from .evals import read_doc_text

    core = number_core(notice.get("agency_number"))
    if len(core) < 4:
        return []
    found = []
    for row in conn.execute(
        "SELECT * FROM opportunities"
        " WHERE portal_number IS NULL AND agency_number IS NULL"
        " ORDER BY id"
    ):
        paths = [r["path"] for r in conn.execute(
            "SELECT path FROM documents WHERE opportunity_id = ? ORDER BY path",
            (row["id"],),
        )]
        if not paths and row["document_path"]:
            paths = [row["document_path"]]
        if any(
            (text := read_doc_text(path)) and core in normalize_text(text)
            for path in paths
        ):
            found.append(row["id"])
    return found


def _stub_note(notice: dict) -> str:
    bits = [f"From {notice['source']} notice {notice.get('subject') or ''}."]
    if notice.get("summary"):
        bits.append("Summary: " + notice["summary"])
    if notice.get("contact"):
        bits.append("Contact: " + notice["contact"])
    return " ".join(bits)[:2000]
