"""Tiny local web page listing opportunities. Stdlib only, binds to loopback."""

from __future__ import annotations

import html
import sqlite3
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect, init_db

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
]


def render_page(rows: list[sqlite3.Row]) -> str:
    def cell(row: sqlite3.Row, key: str) -> str:
        value = row[key] if key in row.keys() else None
        return html.escape(str(value) if value is not None else "—")

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
<style>
body {{ font-family: sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; }}
th {{ background: #f0f0f0; }}
</style></head>
<body>
<h1>Opportunities</h1>
<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    db_path: Path = DEFAULT_DB_PATH

    def do_GET(self) -> None:  # noqa: N802 (stdlib handler naming)
        if self.path not in ("/", "/index.html"):
            self.send_error(404, "Not found")
            return
        with closing(connect(self.db_path)) as conn:
            rows = list(conn.execute("SELECT * FROM opportunities ORDER BY id"))
        page = render_page(rows).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

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
