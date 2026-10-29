"""Slice 2 tests: deterministic checks, detail view, check command."""

import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path

from helpers import handle_request
from proposal_creator import checks, db
from proposal_creator.__main__ import main as cli_main

TODAY = date(2026, 10, 29)


def opp(**fields: object) -> dict:
    base = {"close_date": None, "open_date": None, "document_path": None}
    base.update(fields)
    return base


class DeadlineTest(unittest.TestCase):
    def test_past_deadline_fails(self) -> None:
        result = checks.check_deadline(opp(close_date="2026-10-27"), TODAY)
        self.assertEqual(result["result"], "fail")
        self.assertIn("2 days ago", result["detail"])

    def test_closing_today_warns(self) -> None:
        result = checks.check_deadline(opp(close_date="2026-10-29"), TODAY)
        self.assertEqual(result["result"], "warn")
        self.assertIn("today", result["detail"].lower())

    def test_closing_within_a_week_warns(self) -> None:
        result = checks.check_deadline(opp(close_date="2026-11-01"), TODAY)
        self.assertEqual(result["result"], "warn")
        self.assertIn("3 days", result["detail"])

    def test_distant_deadline_passes(self) -> None:
        result = checks.check_deadline(opp(close_date="2026-11-28"), TODAY)
        self.assertEqual(result["result"], "ok")
        self.assertIn("30 days", result["detail"])

    def test_missing_close_date_warns(self) -> None:
        result = checks.check_deadline(opp(), TODAY)
        self.assertEqual(result["result"], "warn")

    def test_malformed_close_date_warns(self) -> None:
        result = checks.check_deadline(opp(close_date="sometime soon"), TODAY)
        self.assertEqual(result["result"], "warn")


class OpeningTest(unittest.TestCase):
    def test_future_opening_is_info(self) -> None:
        result = checks.check_opening(opp(open_date="2026-11-05"), TODAY)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["result"], "info")

    def test_past_or_missing_opening_is_silent(self) -> None:
        self.assertIsNone(checks.check_opening(opp(open_date="2026-10-01"), TODAY))
        self.assertIsNone(checks.check_opening(opp(), TODAY))


class DocumentTest(unittest.TestCase):
    def test_missing_document_warns(self) -> None:
        self.assertEqual(checks.check_document(opp())["result"], "warn")

    def test_recorded_but_absent_file_warns(self) -> None:
        result = checks.check_document(opp(document_path="drop/gone.pdf"))
        self.assertEqual(result["result"], "warn")
        self.assertIn("not found", result["detail"])

    def test_existing_file_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "solicitation.txt"
            doc.write_text("scope of work", encoding="utf-8")
            result = checks.check_document(opp(document_path=str(doc)))
        self.assertEqual(result["result"], "ok")


class SummarizeTest(unittest.TestCase):
    def test_all_ok(self) -> None:
        results = [{"result": "ok"}, {"result": "info"}]
        self.assertEqual(checks.summarize(results), "ok")

    def test_counts_warnings_and_failures(self) -> None:
        results = [{"result": "warn"}, {"result": "warn"}]
        self.assertEqual(checks.summarize(results), "2 warnings")
        results = [{"result": "fail"}, {"result": "warn"}, {"result": "ok"}]
        self.assertEqual(checks.summarize(results), "1 failed, 1 warning")


class DetailPageTest(unittest.TestCase):
    def test_detail_shows_fields_and_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            response = handle_request(db_path, b"GET /opp/1 HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn("200", response.splitlines()[0])
        self.assertIn("IT Help Desk Support Services", response)
        self.assertIn("Deadline", response)
        self.assertIn('href="/"', response)

    def test_unknown_id_is_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            response = handle_request(db_path, b"GET /opp/999 HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn("404", response.splitlines()[0])

    def test_non_numeric_id_is_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            response = handle_request(db_path, b"GET /opp/abc HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn("404", response.splitlines()[0])

    def test_list_links_to_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            response = handle_request(db_path, b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
        self.assertIn('href="/opp/1"', response)
        self.assertIn("Checks", response)


class CheckCommandTest(unittest.TestCase):
    def run_cli(self, db_path: Path, *args: str) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = cli_main(["--db", str(db_path), *args])
        return code, out.getvalue()

    def test_check_prints_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            code, out = self.run_cli(db_path, "check", "1")
        self.assertEqual(code, 0)
        self.assertIn("Deadline", out)

    def test_check_unknown_id_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.seed_examples(db_path)
            code, out = self.run_cli(db_path, "check", "999")
        self.assertEqual(code, 1)
        self.assertIn("No opportunity", out)


if __name__ == "__main__":
    unittest.main()
