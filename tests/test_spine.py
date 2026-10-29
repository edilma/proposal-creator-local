"""Slice 1 tests: DB schema/seed and page rendering. Stdlib unittest only."""

import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from proposal_creator import db
from proposal_creator.web import Handler, render_page


class DbTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seed_creates_two_rows_and_is_idempotent(self) -> None:
        self.assertEqual(db.seed_examples(self.db_path), 2)
        self.assertEqual(db.seed_examples(self.db_path), 0)  # second run adds nothing
        with closing(db.connect(self.db_path)) as conn:
            rows = db.list_opportunities(conn)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "sam.gov")
        self.assertEqual(rows[1]["source"], "drop")

    def test_schema_has_expected_columns(self) -> None:
        db.init_db(self.db_path)
        with closing(db.connect(self.db_path)) as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(opportunities)")}
        for expected in (
            "source",
            "external_id",
            "title",
            "agency",
            "open_date",
            "close_date",
            "status",
            "verdict",
            "blockers",
            "document_path",
        ):
            self.assertIn(expected, cols)


class FakeSocket:
    """In-memory stand-in so the handler test needs no real socket."""

    def __init__(self, request_bytes: bytes) -> None:
        self._reader = io.BytesIO(request_bytes)
        self.sent = io.BytesIO()

    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BytesIO:
        return self._reader if "r" in mode else self.sent

    def sendall(self, data: bytes) -> None:
        self.sent.write(data)


def handle_request(db_path: Path, request: bytes) -> str:
    handler_cls = type("BoundHandler", (Handler,), {"db_path": db_path})
    fake = FakeSocket(request)
    handler_cls(fake, ("127.0.0.1", 0), None)
    return fake.sent.getvalue().decode("utf-8")


class PageTest(unittest.TestCase):
    def test_render_escapes_untrusted_text(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (id INTEGER, title TEXT)")
        conn.execute("INSERT INTO t VALUES (1, '<script>alert(1)</script>')")
        row = conn.execute("SELECT * FROM t").fetchone()
        conn.close()
        page = render_page([row])
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_handler_serves_seeded_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            response = handle_request(db_path, b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
        status_line = response.splitlines()[0]
        self.assertIn("200", status_line)
        self.assertIn("IT Help Desk Support Services", response)
        self.assertIn("Web-Based Time Study Platform", response)

    def test_handler_404_on_unknown_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            response = handle_request(db_path, b"GET /nope HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn("404", response.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
