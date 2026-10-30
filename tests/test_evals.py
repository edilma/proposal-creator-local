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

# Actual extraction artifacts from RFP_26-27-04_FINAL.pdf (opportunity 9):
# a stray intra-word space ("o f") and line breaks mid-sentence.
ARTIFACTED_INSURANCE = (
    "commercial general liability coverage with limits o f \n"
    "not less than $1,000,000 per occurrence and $5,000,000 general aggregate "
    "for bodily injury and property \n"
    "damage; and comprehensive automobile liability coverage with limits of not "
    "less than $1,000,000 combined \n"
    "single limit and Contractor Pollution Liability coverage with limits not "
    "less than $5,000,000 each occurrence \n"
    "and aggregate."
)

# The FIN-01 quote the model returned for opportunity 9 and the old
# whitespace-only rule refused. Clean wording, same words and numbers.
REFUSED_FIN_QUOTE = (
    "commercial general liability coverage with limits of not less than "
    "$1,000,000 per occurrence and $5,000,000 general aggregate for bodily "
    "injury and property damage; and comprehensive automobile liability "
    "coverage with limits of not less than $1,000,000 combined single limit "
    "and Contractor Pollution Liability coverage with limits not less than "
    "$5,000,000 each occurrence and aggregate."
)

REQS = [
    {
        "code": "CERT-01",
        "category": "certifications",
        "question": "Required certifications held?",
        "guidance": "Compare to profile.",
    }
]


def build_pdf_bytes(text_lines: str | list) -> bytes:
    """Minimal one-page text PDF with computed xref (ASCII only)."""
    if isinstance(text_lines, str):
        text_lines = [text_lines]

    def esc(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    shown = " T* ".join(f"({esc(t)}) Tj" for t in text_lines)
    stream = f"BT /F1 12 Tf 72 720 Td 14 TL {shown} ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out: list[bytes] = [b"%PDF-1.4\n"]
    offsets = []
    for num, body in enumerate(objs, start=1):
        offsets.append(sum(map(len, out)))
        out.append(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = sum(map(len, out))
    out.append(f"xref\n0 {len(objs) + 1}\n".encode())
    out.append(b"0000000000 65535 f \n")
    for off in offsets:
        out.append(f"{off:010d} 00000 n \n".encode())
    out.append(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n".encode())
    out.append(f"startxref\n{xref_pos}\n%%EOF\n".encode())
    return b"".join(out)


def make_db(
    tmp: str,
    doc_text: str | None = DOC_TEXT,
    suffix: str = ".txt",
    raw: bytes | None = None,
) -> tuple[Path, int]:
    """Seeded DB + one opportunity; returns (db_path, opp_id)."""
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        req_path = Path(tmp) / "reqs.json"
        req_path.write_text(json.dumps(REQS), encoding="utf-8")
        evals.load_requirements(conn, req_path)
        doc = Path(tmp) / f"sol{suffix}"
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
            "document_path": (
                str(doc) if (doc_text is not None or raw is not None) else None
            ),
            "notes": None,
        }
        if raw is not None:
            doc.write_bytes(raw)
        elif doc_text is not None:
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


class ArtifactToleranceTest(unittest.TestCase):
    def test_refused_fin_quote_matches_artifacted_text(self) -> None:
        self.assertTrue(
            evals.quote_in_text(REFUSED_FIN_QUOTE, ARTIFACTED_INSURANCE)
        )

    def test_stray_intra_word_space_ignored(self) -> None:
        self.assertTrue(evals.quote_in_text("registration", "registrat ion"))
        self.assertTrue(
            evals.quote_in_text(
                "limits of not less than", "limits o f not less than"
            )
        )

    def test_line_break_hyphen_ignored(self) -> None:
        self.assertTrue(
            evals.quote_in_text(
                "each occurrence and aggregate",
                "each occur-\nrence and aggregate",
            )
        )

    def test_wrong_number_still_rejected(self) -> None:
        self.assertFalse(
            evals.quote_in_text(
                "limits of not less than $2,000,000 per occurrence",
                ARTIFACTED_INSURANCE,
            )
        )

    def test_gate_verifies_refused_quote_and_keeps_original(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp, doc_text=ARTIFACTED_INSURANCE)
            with closing(db.connect(db_path)) as conn:
                row = evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS", quote=REFUSED_FIN_QUOTE
                )
        self.assertEqual(row["verified"], 1)
        self.assertEqual(row["quote"], REFUSED_FIN_QUOTE)

    def test_gate_rejects_wrong_number(self) -> None:
        bad = (
            "commercial general liability coverage with limits of not less "
            "than $2,000,000 per occurrence"
        )
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp, doc_text=ARTIFACTED_INSURANCE)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    evals.set_evaluation(conn, opp_id, "CERT-01", "PASS", quote=bad)

    def test_read_doc_text_supports_txt_and_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "a.txt"
            txt.write_text("hello", encoding="utf-8")
            self.assertEqual(evals.read_doc_text(str(txt)), "hello")
            pdf = Path(tmp) / "a.pdf"
            pdf.write_bytes(build_pdf_bytes("hello from pdf"))
            text = evals.read_doc_text(str(pdf))
            assert text is not None
            self.assertIn("hello from pdf", text)
            self.assertIsNone(evals.read_doc_text(str(Path(tmp) / "gone.txt")))
            self.assertIsNone(evals.read_doc_text(None))

    def test_read_doc_text_rejects_garbage_and_blank_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            garbage = Path(tmp) / "garbage.pdf"
            garbage.write_bytes(b"%PDF-1.4 fake")
            self.assertIsNone(evals.read_doc_text(str(garbage)))
            blank = Path(tmp) / "blank.pdf"
            blank.write_bytes(build_pdf_bytes([]))
            self.assertIsNone(evals.read_doc_text(str(blank)))


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

    def test_pass_with_pdf_quote_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(
                tmp, doc_text=None, suffix=".pdf",
                raw=build_pdf_bytes(DOC_TEXT),
            )
            with closing(db.connect(db_path)) as conn:
                row = evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS",
                    quote="maintain SOC 2 Type II compliance.",
                )
                self.assertEqual(row["verified"], 1)

    def test_pass_with_invented_quote_on_pdf_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(
                tmp, doc_text=None, suffix=".pdf",
                raw=build_pdf_bytes(DOC_TEXT),
            )
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    evals.set_evaluation(
                        conn, opp_id, "CERT-01", "PASS",
                        quote="CMMC Level 2 required.",
                    )

    def test_pass_on_blank_pdf_stored_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(
                tmp, doc_text=None, suffix=".pdf",
                raw=build_pdf_bytes([]),
            )
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

    def test_na_needs_no_quote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                row = evals.set_evaluation(conn, opp_id, "CERT-01", "N/A")
                self.assertEqual(row["verdict"], "N/A")

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
