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
from urllib.parse import parse_qs

from . import answers as answers_lib
from .checks import run_checks, summarize
from .db import DEFAULT_DB_PATH, connect, get_opportunity, init_db
from .draft import DEFAULT_DRAFTS_DIR, draft_paths
from .evals import list_checklist
from .intake import list_documents

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
    ("portal_number", "Portal number"),
    ("agency_number", "Agency number"),
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
pre { white-space: pre-wrap; word-wrap: break-word; background: #fafafa;
  padding: 0.6rem; border: 1px solid #eee; }
textarea { width: 100%; box-sizing: border-box; }
input[type=text], input[type=date] { width: 100%; box-sizing: border-box; }
.error { color: #a00; font-weight: bold; }
"""


def _text(value: object) -> str:
    return html.escape(str(value) if value is not None else "—")


def _link_or_text(value: object) -> str:
    """Clickable portal link for http(s) URLs, escaped text otherwise."""
    if value is None or not str(value).strip():
        return "—"
    target = str(value).strip()
    if target.startswith(("http://", "https://")):
        quoted = html.escape(target, quote=True)
        return f'<a href="{quoted}">{html.escape(target)}</a>'
    return html.escape(target)


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
<p><a href="/answers">Answer library</a></p>
<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>
</body>
</html>
"""


def _freshness_cell(entry: sqlite3.Row, stale_days: int) -> str:
    state = answers_lib.freshness(entry, stale_days=stale_days)
    if state == "expired":
        return '<span class="fail">EXPIRED — confirm still current</span>'
    if state == "stale":
        return '<span class="warn">STALE — confirm still current</span>'
    return '<span class="ok">current</span>'


def render_answers(
    rows: list[sqlite3.Row],
    stale_days: int | None = None,
    error: str | None = None,
) -> str:
    days = answers_lib.DEFAULT_STALE_DAYS if stale_days is None else stale_days
    err = f'<p class="error">{_text(error)}</p>' if error else ""
    body = ""
    for row in rows:
        status_opts = "".join(
            f'<option value="{s}"{" selected" if row["status"] == s else ""}>'
            f"{s}</option>"
            for s in answers_lib.STATUSES
        )
        form_id = f"answer-{row['id']}"
        body += (
            "<tr>"
            # Inputs point at the row's form by id: a form element may not
            # wrap table cells, but form="" association is valid HTML.
            f'<td><input type="text" name="label" form="{form_id}"'
            f' value="{_text(row["label"])}"></td>'
            f'<td><textarea name="answer_text" form="{form_id}" rows="3">'
            f"{_text(row['answer_text'])}</textarea></td>"
            f'<td><input type="date" name="last_confirmed" form="{form_id}"'
            f' value="{_text(row["last_confirmed"])}"></td>'
            f'<td><input type="date" name="valid_until" form="{form_id}"'
            f' value="{_text(row["valid_until"] or "")}"></td>'
            f'<td><select name="status" form="{form_id}">'
            f"{status_opts}</select></td>"
            f"<td>{_freshness_cell(row, days)}</td>"
            "<td>"
            f'<form id="{form_id}" method="post" action="/answers/update">'
            f'<input type="hidden" name="id" value="{row["id"]}">'
            '<button type="submit">Save</button> '
            '<button type="submit" formaction="/answers/retire">Retire</button>'
            "</form>"
            "</td>"
            "</tr>"
        )
    if not rows:
        body = (
            '<tr><td colspan="7">No saved answers yet. Add one below — future'
            " drafts will reuse it wherever it fits.</td></tr>"
        )
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Answer library</title>
<style>{STYLE}</style></head>
<body>
<p><a href="/">← All opportunities</a></p>
<h1>Answer library</h1>
<p>Reusable answers for future drafts, separate from the company profile.
One answer per label — saving a label that exists edits it.
Answers flagged EXPIRED or STALE are still reused, but marked
"confirm still current" in the draft.</p>
<p>Stale means last confirmed more than {days} days ago
(<code>{answers_lib.STALE_DAYS_ENV_VAR}</code> to change).</p>
{err}
<table><thead><tr><th>Label</th><th>Answer text</th><th>Last confirmed</th>
<th>Valid until</th><th>Status</th><th>Freshness</th><th></th></tr></thead>
<tbody>{body}</tbody></table>
<h2>Add an answer</h2>
<form method="post" action="/answers/add">
<p><label>Label<br><input type="text" name="label"></label></p>
<p><label>Answer text<br><textarea name="answer_text" rows="4"></textarea></label></p>
<p><label>Last confirmed<br><input type="date" name="last_confirmed"
value="{answers_lib.today_iso()}"></label></p>
<p><label>Valid until (optional)<br><input type="date" name="valid_until"></label></p>
<p><button type="submit">Add answer</button></p>
</form>
</body>
</html>
"""


def _render_save_answer_form(opp_id: int) -> str:
    return (
        "<h2>Save to answer library</h2>"
        "<p>Store a reusable answer from this draft. Saved as-is — no model call."
        ' Saving a label that exists edits it. <a href="/answers">Manage library</a>.</p>'
        f'<form method="post" action="/opp/{opp_id}/save-answer">'
        '<p><label>Label<br><input type="text" name="label"></label></p>'
        '<p><label>Answer text<br><textarea name="answer_text" rows="4">'
        "</textarea></label></p>"
        "<p><button type=\"submit\">Save answer</button></p>"
        "</form>"
    )


def _render_draft_section(opp_id: int, drafts_dir: Path | str | None) -> str:
    base = Path(drafts_dir) if drafts_dir is not None else DEFAULT_DRAFTS_DIR
    draft_path, comm_path = draft_paths(base, opp_id)

    def read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    draft_text, comm_text = read(draft_path), read(comm_path)
    if draft_text is None and comm_text is None:
        return (
            "<h2>Draft</h2>"
            f"<p>No draft yet. Run <code>proposal-creator draft {opp_id}</code>.</p>"
        )
    parts = ["<h2>Draft</h2>", "<p>A draft only: a human reviews and submits.</p>"]
    if draft_text is not None:
        parts.append(
            f"<h3>Response draft</h3><p><code>{_text(draft_path)}</code></p>"
            f"<pre>{_text(draft_text)}</pre>"
        )
    if comm_text is not None:
        parts.append(
            "<h3>Commitments checklist</h3>"
            f"<p><code>{_text(comm_path)}</code></p>"
            f"<pre>{_text(comm_text)}</pre>"
        )
    return "".join(parts)


def _render_documents_section(opp_id: int, documents: list | None) -> str:
    if not documents:
        return (
            "<h2>Documents</h2>"
            f"<p>No documents yet. Drop files in <code>drop/opp-{opp_id}/</code>"
            " then run <code>proposal-creator intake</code>.</p>"
        )
    rows = "".join(
        f"<tr><td>{_text(doc['path'])}</td><td>{_text(doc['source'])}</td>"
        f"<td>{'confirmed' if doc['confirmed'] else '—'}</td></tr>"
        for doc in documents
    )
    return (
        "<h2>Documents</h2>"
        "<table><thead><tr><th>Path</th><th>Source</th>"
        "<th>Agency #</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p>Drop more files in <code>drop/opp-{opp_id}/</code>"
        " then run <code>proposal-creator intake</code>.</p>"
    )


def render_detail(
    row: sqlite3.Row,
    checklist: list[sqlite3.Row],
    drafts_dir: Path | str | None = None,
    documents: list | None = None,
) -> str:
    def field(key: str) -> str:
        if key == "url":
            return _link_or_text(row[key])
        try:
            return _text(row[key])
        except (KeyError, IndexError):
            return "—"

    fields = "".join(
        f"<dt>{label}</dt><dd>{field(key)}</dd>" for key, label in DETAIL_FIELDS
    )
    if documents is None:
        documents = (
            [{
                "path": row["document_path"],
                "source": "drop",
                "confirmed": None,
            }]
            if row["document_path"]
            else []
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
    draft_section = _render_draft_section(row["id"], drafts_dir)
    save_form = _render_save_answer_form(row["id"])
    docs_section = _render_documents_section(row["id"], documents)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>{_text(row["title"])}</title>
<style>{STYLE}</style></head>
<body>
<p><a href="/">← All opportunities</a></p>
<h1>{_text(row["title"])}</h1>
<dl>{fields}</dl>
{docs_section}
<h2>Checks</h2>
<table><thead><tr><th>Level</th><th>Check</th><th>Detail</th></tr></thead>
<tbody>{checks}</tbody></table>
<h2>Evaluations</h2>
<table><thead><tr><th>Code</th><th>Requirement</th><th>Verdict</th>
<th>Quote</th><th>Evidence</th></tr></thead>
<tbody>{evals}</tbody></table>
{draft_section}
{save_form}
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

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _send_error_page(self, message: str, back: str) -> None:
        page = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>Error</title><style>{STYLE}</style></head><body>"
            f'<p class="error">{_text(message)}</p>'
            f'<p><a href="{back}">← Back</a></p>'
            "</body></html>"
        )
        body = page.encode("utf-8")
        self.send_response(400)
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
        if self.path == "/answers":
            with closing(connect(self.db_path)) as conn:
                rows = answers_lib.list_answers(conn)
            self._send_html(
                render_answers(rows, stale_days=answers_lib.stale_days_from_env())
            )
            return
        match = re.fullmatch(r"/opp/(\d+)", self.path)
        if match:
            with closing(connect(self.db_path)) as conn:
                row = get_opportunity(conn, int(match.group(1)))
                checklist = (
                    list_checklist(conn, int(match.group(1))) if row is not None else []
                )
                documents = (
                    list_documents(conn, int(match.group(1)))
                    if row is not None
                    else []
                )
            if row is None:
                self.send_error(404, "No such opportunity")
            else:
                self._send_html(render_detail(row, checklist, documents=documents))
            return
        self.send_error(404, "Not found")

    def _form_fields(self) -> dict[str, str]:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {
            key: values[0] if values else ""
            for key, values in parse_qs(raw, keep_blank_values=True).items()
        }

    def do_POST(self) -> None:  # noqa: N802 (stdlib handler naming)
        fields = self._form_fields()
        if self.path == "/answers/add":
            try:
                with closing(connect(self.db_path)) as conn:
                    answers_lib.save_answer(
                        conn,
                        fields.get("label", ""),
                        fields.get("answer_text", ""),
                        last_confirmed=fields.get("last_confirmed") or None,
                        valid_until=fields.get("valid_until") or None,
                    )
            except ValueError as exc:
                self._send_error_page(str(exc), "/answers")
                return
            self._redirect("/answers")
            return
        if self.path == "/answers/update":
            try:
                answer_id = int(fields.get("id", ""))
            except ValueError:
                self._send_error_page("Missing answer id.", "/answers")
                return
            try:
                with closing(connect(self.db_path)) as conn:
                    answers_lib.update_answer(
                        conn,
                        answer_id,
                        label=fields.get("label", ""),
                        answer_text=fields.get("answer_text", ""),
                        last_confirmed=fields.get("last_confirmed") or None,
                        valid_until=fields.get("valid_until"),
                        status=fields.get("status") or None,
                    )
            except ValueError as exc:
                self._send_error_page(str(exc), "/answers")
                return
            self._redirect("/answers")
            return
        if self.path == "/answers/retire":
            try:
                answer_id = int(fields.get("id", ""))
            except ValueError:
                self._send_error_page("Missing answer id.", "/answers")
                return
            try:
                with closing(connect(self.db_path)) as conn:
                    answers_lib.retire_answer(conn, answer_id)
            except ValueError as exc:
                self._send_error_page(str(exc), "/answers")
                return
            self._redirect("/answers")
            return
        match = re.fullmatch(r"/opp/(\d+)/save-answer", self.path)
        if match:
            opp_id = int(match.group(1))
            with closing(connect(self.db_path)) as conn:
                if get_opportunity(conn, opp_id) is None:
                    self.send_error(404, "No such opportunity")
                    return
                try:
                    # Plain storage of the owner's text — no model call.
                    answers_lib.save_answer(
                        conn,
                        fields.get("label", ""),
                        fields.get("answer_text", ""),
                    )
                except ValueError as exc:
                    self._send_error_page(str(exc), f"/opp/{opp_id}")
                    return
            self._redirect(f"/opp/{opp_id}")
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
