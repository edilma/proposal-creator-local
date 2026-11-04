"""MFMP portal fetch: advertisement JSON plus document downloads.

The Vendor Information Portal answers plain GETs with `Accept:
application/json` — no login, no saved-page step. `fetch_advertisement`
returns the ad record; `summarize_advertisement` distills the same summary
shape the OpenGov fetcher produces, so backfill and downloading reuse those
helpers unchanged.

The download endpoint below is the exact XHR URL captured from the
site's Network panel. Downloads need the same request headers as the JSON
call — without them the server answers HTML. Every payload is magic-byte
checked, so a wrong path fails loudly instead of saving an HTML shell as
a PDF.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
from collections.abc import Callable

BASE_URL = "https://vendor.myfloridamarketplace.com"
DETAIL_PATH = "/mfmp/pub/search/bids/detail"
# Exact XHR from the Network panel (verified by the owner in-browser).
DOWNLOAD_BASE = f"{BASE_URL}/mfmp/bids/detail/attachment/download"

JSON_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "Mozilla/5.0",
}

MAGIC = {
    ".pdf": b"%PDF-",
    ".xlsx": b"PK",
    ".xls": bytes((0xD0, 0xCF, 0x11, 0xE0)),
    ".doc": bytes((0xD0, 0xCF, 0x11, 0xE0)),
}


class MfmpError(Exception):
    pass


def fetch_advertisement(
    ad_id: str,
    opener: Callable[[str, dict], bytes] | None = None,
) -> dict:
    """Advertisement record for a numeric MFMP advertisement id."""
    if not re.fullmatch(r"\d+", ad_id or ""):
        raise MfmpError(f"Not an MFMP advertisement id: {ad_id!r}.")
    if opener is None:
        def opener(url: str, headers: dict) -> bytes:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()

    try:
        raw = opener(f"{BASE_URL}{DETAIL_PATH}?id={ad_id}", JSON_HEADERS)
    except MfmpError:
        raise
    except Exception as exc:
        raise MfmpError(f"Could not reach MFMP: {exc}.") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise MfmpError(
            "MFMP did not return JSON — the endpoint may have changed."
        ) from None
    if not isinstance(payload, dict) or "advertisementId" not in payload:
        raise MfmpError("MFMP response has no advertisement record.")
    return payload


def _day(value: object) -> str | None:
    """'2026-11-20T23:00:00.000+00:00' -> '2026-11-20'; else None."""
    if not isinstance(value, str):
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return None
    try:
        return dt.date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def summarize_advertisement(payload: dict) -> dict:
    """Identifiers, dates, and attachments in the shared summary shape."""
    attachments = []
    docs = payload.get("docs")
    if isinstance(docs, list):
        for item in docs:
            if not isinstance(item, dict):
                continue
            attachment_id = item.get("attachmentId")
            filename = (item.get("fileName") or "").strip()
            if attachment_id is None or not filename:
                continue
            attachments.append({
                "kind": "attachment",
                "filename": filename,
                "description": (item.get("description") or "").strip(),
                "attachmentId": attachment_id,
                "url": f"{DOWNLOAD_BASE}?attachmentId={attachment_id}",
            })
    portal_number = payload.get("uniqueName") or None
    return {
        "portal_number": str(portal_number) if portal_number is not None else None,
        "agency_number": payload.get("agencyAdNumber") or None,
        "title": payload.get("title") or None,
        "agency": payload.get("agency") or None,
        "open_date": _day(payload.get("openDate")),
        "close_date": _day(payload.get("closeDate")),
        "url": f"{BASE_URL}/search/bids/detail/{payload.get('advertisementId')}",
        "attachments": attachments,
    }


def check_magic(filename: str, data: bytes) -> None:
    """Reject empty bodies and bodies of the wrong file type."""
    if not data:
        raise MfmpError(f"{filename}: server returned an empty file.")
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    magic = MAGIC.get(suffix)
    if magic is not None and not data.startswith(magic):
        raise MfmpError(
            f"{filename}: server did not return a {suffix} file"
            " (HTML instead of the document — the download path may"
            " have changed)."
        )


def make_opener(
    attachments: list[dict],
    ad_id: str | None = None,
) -> Callable[[str], bytes]:
    """Byte fetcher for `download_attachments`, with magic-byte checks.

    Sends the full proven header set: without it the server answers
    HTML instead of the document.
    """

    names = {
        item.get("url"): item.get("filename", "")
        for item in attachments
    }
    headers = dict(JSON_HEADERS)
    if ad_id is not None:
        headers["Referer"] = f"{BASE_URL}/search/bids/detail/{ad_id}"

    def opener(url: str) -> bytes:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
        except Exception as exc:
            raise MfmpError(str(exc)) from None
        check_magic(names.get(url, ""), data)
        return data

    return opener
