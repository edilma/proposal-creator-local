"""Slice 4 tests: requirements, evaluations, and the verbatim-quote gate."""

import json
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

from helpers import handle_request
from proposal_creator import db, evals
from proposal_creator.__main__ import main as cli_main

DOC_TEXT = "The Contractor shall maintain SOC 2 Type II compliance.\nWork starts on March 1."

REQS = [
    {
        "code": "CERT-01",
        "category": "certifications",
        "question": "Required certifications held?",
        "guidance": "Compare to profile.",
    }
]


def make_db(tmp: str, doc_text: str | None = DOC_TEXT) -> tuple[Path, int]:
    """Seeded DB + one opportunity; returns (db_path, opp_id). Doc is .txt."""
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        req_path = Path(tmp) / "reqs.json"
        req_path.write_text(json.dumps(REQS), encoding="utf-8")
        evals.load_requirements(conn, req_path)
        doc = Path(tmp) / "sol.txt"
        opp: dict = {
            "source": "drop",
            "external_id": None,
            "title": "Test RFP",
            "agency": None,
            "url": None,
            "open_date": None,
            "close_date": None,
            "status": "new",
            "verdict": None,
            "blockers": None,
            "document_path": str(doc) if doc_text is not None else None,
            "notes": None,
        }
        if doc_text is not None:
            doc.write_text(doc_text, encoding="utf-8")
        opp_id = db.add_opportunity(conn, opp)
    return db_path, opp_id


class QuoteTest(unittest.TestCase):
    def test_verbatim_match(self) -> None:
        self.assertTrue(
            evals.quote_in_text("maintain SOC 2 Type II compliance.", DOC_TEXT)
        )

    def test_whitespace_differences_still_match(self) -> None:
        self.assertTrue(
            evals.quote_in_text("compliance.\nWork  starts", DOC_TEXT + "\n")
        )

    def test_missing_quote_fails(self) -> None:
        self.assertFalse(evals.quote_in_text("CMMC Level 2 required.", DOC_TEXT))

    def test_empty_quote_fails(self) -> None:
        self.assertFalse(evals.quote_in_text("  ", DOC_TEXT))
        self.assertFalse(evals.quote_in_text("", DOC_TEXT))

    def test_read_doc_text_supports_txt_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "a.txt"
            txt.write_text("hello", encoding="utf-8")
            self.assertEqual(evals.read_doc_text(str(txt)), "hello")
            pdf = Path(tmp) / "a.pdf"
            pdf.write_bytes(b"%PDF-1.4 fake")
            self.assertIsNone(evals.read_doc_text(str(pdf)))
            self.assertIsNone(evals.read_doc_text(str(Path(tmp) / "gone.txt")))
            self.assertIsNone(evals.read_doc_text(None))


class GateTest(unittest.TestCase):
    def test_pass_with_verbatim_quote_stored_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                row = evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS",
                    quote="maintain SOC 2 Type II compliance.",
                )
                self.assertEqual(row["verified"], 1)

    def test_pass_with_invented_quote_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    evals.set_evaluation(
                        conn, opp_id, "CERT-01", "PASS",
                        quote="CMMC Level 2 required.",
                    )
                self.assertEqual(evals.list_evaluations(conn, opp_id), [])

    def test_pass_without_quote_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    evals.set_evaluation(conn, opp_id, "CERT-01", "PASS")

    def test_pass_with_unreadable_doc_stored_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp, doc_text=None)
            with closing(db.connect(db_path)) as conn:
                row = evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS", quote="some sentence."
                )
                self.assertIsNone(row["verified"])

    def test_fail_without_quote_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                row = evals.set_evaluation(conn, opp_id, "CERT-01", "FAIL")
                self.assertEqual(row["verdict"], "FAIL")

    def test_fail_with_invented_quote_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    evals.set_evaluation(
                        conn, opp_id, "CERT-01", "FAIL",
                        quote="not in the document.",
                    )

    def test_set_twice_upserts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                evals.set_evaluation(conn, opp_id, "CERT-01", "UNKNOWN")
                evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS",
                    quote="maintain SOC 2 Type II compliance.",
                )
                rows = evals.list_evaluations(conn, opp_id)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["verdict"], "PASS")

    def test_bad_verdict_and_unknown_code_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    evals.set_evaluation(conn, opp_id, "CERT-01", "MAYBE")
                with self.assertRaises(ValueError):
                    evals.set_evaluation(conn, opp_id, "NOPE-99", "PASS",
                                         quote="x")

    def test_shipped_requirements_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                count = evals.load_requirements(conn, evals.DEFAULT_REQUIREMENTS_PATH)
                self.assertGreater(count, 0)
                codes = [r["code"] for r in evals.list_requirements(conn)]
                self.assertIn("REG-01", codes)


class EvalPageTest(unittest.TestCase):
    def test_detail_shows_checklist_with_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS",
                    quote="maintain SOC 2 Type II compliance.",
                )
            response = handle_request(
                db_path, f"GET /opp/{opp_id} HTTP/1.0\r\nHost: x\r\n\r\n".encode()
            )
        self.assertIn("CERT-01", response)
        self.assertIn("maintain SOC 2 Type II compliance.", response)
        self.assertIn("verified", response.lower())


class EvalCommandTest(unittest.TestCase):
    def run_cli(self, db_path: Path, *args: str) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = cli_main(["--db", str(db_path), *args])
        return code, out.getvalue()

    def test_set_and_show_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            code, _ = self.run_cli(
                db_path, "set-eval", str(opp_id), "CERT-01", "PASS",
                "--quote", "maintain SOC 2 Type II compliance.",
            )
            self.assertEqual(code, 0)
            code, out = self.run_cli(db_path, "evals", str(opp_id))
            self.assertEqual(code, 0)
            self.assertIn("CERT-01", out)
            self.assertIn("PASS", out)

    def test_cli_rejects_invented_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            code, out = self.run_cli(
                db_path, "set-eval", str(opp_id), "CERT-01", "PASS",
                "--quote", "CMMC Level 2 required.",
            )
            self.assertEqual(code, 1)
            self.assertIn("verbatim", out)


if __name__ == "__main__":
    unittest.main()
