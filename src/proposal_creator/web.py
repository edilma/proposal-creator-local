"""Tiny local web pages: opportunity list + per-opportunity detail.

Stdlib only, binds to loopback. Every database value is HTML-escaped.
"""

from __future__ import annotations

import html
import re
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .checks import run_checks, summarize
from .db import DEFAULT_DB_PATH, connect, get_opportunity, init_db
from .evals import list_checklist

HOST = "127.0.0.1"
PORT = 8000

COLUMNS = [
    ("id", "ID"),
    ("source", "Source"),
    ("external_id", "External ID"),
    ("title", "Title"),
    ("agency", "Agency"),
    ("open_date", "Open"),
    ("close_date", "Close"),
    ("status", "Status"),
    ("verdict", "Verdict"),
    ("blockers", "Blockers"),
    ("document_path", "Document"),
    ("checks", "Checks"),
]

DETAIL_FIELDS = [
    ("id", "ID"),
    ("source", "Source"),
    ("external_id", "External ID"),
    ("title", "Title"),
    ("agency", "Agency"),
    ("url", "URL"),
    ("open_date", "Open date"),
    ("close_date", "Close date"),
    ("status", "Status"),
    ("verdict", "Verdict"),
    ("blockers", "Blockers"),
    ("document_path", "Document"),
    ("notes", "Notes"),
    ("created_at", "Created"),
    ("updated_at", "Updated"),
]

STYLE = """
body { font-family: sans-serif; margin: 2rem; max-width: 60rem; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }
th { background: #f0f0f0; }
dt { font-weight: bold; margin-top: 0.6rem; }
dd { margin: 0.2rem 0 0 0; }
.fail { color: #a00; font-weight: bold; }
.warn { color: #a60; font-weight: bold; }
.info { color: #06a; }
.ok { color: #080; }
"""


def _text(value: object) -> str:
    return html.escape(str(value) if value is not None else "—")


def render_page(rows: list[sqlite3.Row]) -> str:
    def cell(row: sqlite3.Row, key: str) -> str:
        if key == "title":
            return (
                f'<a href="/opp/{row["id"]}">'
                f"{html.escape(str(row['title']))}</a>"
            )
        if key == "checks":
            return _text(summarize(run_checks(row)))
        value = row[key] if key in row.keys() else None
        return _text(value)

    header = "".join(f"<th>{label}</th>" for _, label in COLUMNS)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(row, key)}</td>" for key, _ in COLUMNS) + "</tr>"
        for row in rows
    )
    if not rows:
        body = (
            f'<tr><td colspan="{len(COLUMNS)}">No opportunities yet. '
            "Run <code>proposal-creator seed</code> to add the two demo rows.</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Proposal Creator — Opportunities</title>
<style>{STYLE}</style></head>
<body>
<h1>Opportunities</h1>
<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>
</body>
</html>
"""


def render_detail(row: sqlite3.Row, checklist: list[sqlite3.Row]) -> str:
    fields = "".join(
        f"<dt>{label}</dt><dd>{_text(row[key])}</dd>" for key, label in DETAIL_FIELDS
    )
    checks = "".join(
        f'<tr><td class="{c["result"]}">{c["result"].upper()}</td>'
        f"<td>{_text(c['label'])}</td><td>{_text(c['detail'])}</td></tr>"
        for c in run_checks(row)
    )

    def evidence(item: sqlite3.Row) -> str:
        if not item["quote"]:
            return "—"
        return "verified" if item["verified"] else "unverified"

    evals = "".join(
        f"<tr><td>{_text(item['code'])}</td><td>{_text(item['question'])}</td>"
        f"<td>{_text(item['verdict'])}</td><td>{_text(item['quote'])}</td>"
        f"<td>{evidence(item)}</td></tr>"
        for item in checklist
    )
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{_text(row["title"])}</title>
<style>{STYLE}</style></head>
<body>
<p><a href="/">← All opportunities</a></p>
<h1>{_text(row["title"])}</h1>
<dl>{fields}</dl>
<h2>Checks</h2>
<table><thead><tr><th>Level</th><th>Check</th><th>Detail</th></tr></thead>
<tbody>{checks}</tbody></table>
<h2>Evaluations</h2>
<table><thead><tr><th>Code</th><th>Requirement</th><th>Verdict</th>
<th>Quote</th><th>Evidence</th></tr></thead>
<tbody>{evals}</tbody></table>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB_PATH

    def _send_html(self, page: str) -> None:
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (stdlib handler naming)
        if self.path in ("/", "/index.html"):
            with closing(connect(self.db_path)) as conn:
                rows = list(conn.execute("SELECT * FROM opportunities ORDER BY id"))
            self._send_html(render_page(rows))
            return
        match = re.fullmatch(r"/opp/(\d+)", self.path)
        if match:
            with closing(connect(self.db_path)) as conn:
                row = get_opportunity(conn, int(match.group(1)))
                checklist = (
                    list_checklist(conn, int(match.group(1))) if row is not None else []
                )
            if row is None:
                self.send_error(404, "No such opportunity")
            else:
                self._send_html(render_detail(row, checklist))
            return
        self.send_error(404, "Not found")

    def log_message(self, *args: object) -> None:
        pass  # keep the local console quiet


def serve(
    db_path: Path | str = DEFAULT_DB_PATH,
    host: str = HOST,
    port: int = PORT,
) -> HTTPServer:
    init_db(db_path)
    handler = type("BoundHandler", (Handler,), {"db_path": Path(db_path)})
    server = HTTPServer((host, port), handler)
    print(f"Serving opportunities at http://{host}:{server.server_port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return server
