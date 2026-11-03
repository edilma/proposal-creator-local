"""Slice 3 tests: drop-folder intake creates one stub record per new file."""

import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

from proposal_creator import db, evals, intake
from proposal_creator.__main__ import main as cli_main


def make_drop(tmp: str, *names: str) -> Path:
    drop = Path(tmp) / "drop"
    drop.mkdir()
    for name in names:
        (drop / name).write_text("fake solicitation text", encoding="utf-8")
    return drop


class IntakeTest(unittest.TestCase):
    def test_scan_creates_one_record_per_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            drop = make_drop(tmp, "RFP-118.txt", "RFP-119.txt")
            db_path = Path(tmp) / "test.db"
            added, skipped = intake.scan_drop(drop, db_path)
            self.assertEqual(len(added), 2)
            self.assertEqual(skipped, [])
            with closing(db.connect(db_path)) as conn:
                rows = db.list_opportunities(conn)
            by_title = {r["title"]: r for r in rows}
            self.assertEqual(by_title["RFP-118"]["source"], "drop")
            self.assertEqual(by_title["RFP-118"]["status"], "new")
            self.assertTrue(by_title["RFP-118"]["document_path"].endswith("RFP-118.txt"))

    def test_rescan_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            drop = make_drop(tmp, "RFP-118.txt")
            db_path = Path(tmp) / "test.db"
            intake.scan_drop(drop, db_path)
            added, skipped = intake.scan_drop(drop, db_path)
            self.assertEqual(added, [])
            self.assertEqual(skipped, [])
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(len(db.list_opportunities(conn)), 1)

    def test_new_stub_files_document_into_opp_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            drop = make_drop(tmp, "RFP-118.txt")
            db_path = Path(tmp) / "test.db"
            added, _ = intake.scan_drop(drop, db_path)
            self.assertEqual(len(added), 1)
            self.assertFalse((drop / "RFP-118.txt").exists())
            with closing(db.connect(db_path)) as conn:
                rows = db.list_opportunities(conn)
                opp_id = rows[0]["id"]
                moved = drop / f"opp-{opp_id}" / "RFP-118.txt"
                self.assertTrue(moved.is_file())
                self.assertIn(f"opp-{opp_id}", rows[0]["document_path"])
                docs = intake.list_documents(conn, opp_id)
                self.assertEqual(len(docs), 1)
                self.assertIn(f"opp-{opp_id}", docs[0]["path"])

    def test_skips_dotfiles_and_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            drop = make_drop(tmp, ".gitkeep", "real.txt")
            (drop / "subdir").mkdir()
            db_path = Path(tmp) / "test.db"
            added, _ = intake.scan_drop(drop, db_path)
            self.assertEqual([a["title"] for a in added], ["real"])

    def test_missing_folder_is_empty_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            added, skipped = intake.scan_drop(Path(tmp) / "nope", Path(tmp) / "t.db")
            self.assertEqual((added, skipped), ([], []))

    def test_cli_intake_reports_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            drop = make_drop(tmp, "RFP-118.txt")
            db_path = Path(tmp) / "test.db"
            out = StringIO()
            with redirect_stdout(out):
                code = cli_main(["--db", str(db_path), "intake", "--drop", str(drop)])
            self.assertEqual(code, 0)
            # Filed into the new stub's folder, then seen as known by scan_folders.
            self.assertIn("added 1", out.getvalue())
            self.assertIn("attached 0", out.getvalue())
            self.assertIn("skipped 1", out.getvalue())
            self.assertIn("opp-1", out.getvalue())


def make_opp(conn, **overrides) -> int:
    opp = {
        "source": "drop", "external_id": None, "title": "T",
        "agency": None, "url": None, "open_date": None, "close_date": None,
        "status": "new", "verdict": None, "blockers": None,
        "document_path": None, "notes": None,
    }
    opp.update(overrides)
    return db.add_opportunity(conn, opp)


class FolderIntakeTest(unittest.TestCase):
    def test_folder_files_attach_to_opp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = make_opp(conn)
            folder = Path(tmp) / "drop" / f"opp-{opp_id}"
            folder.mkdir(parents=True)
            (folder / "a.txt").write_text("first doc", encoding="utf-8")
            (folder / "b.txt").write_text("second doc", encoding="utf-8")
            attached, skipped = intake.scan_folders(
                Path(tmp) / "drop", db_path
            )
            self.assertEqual(skipped, [])
            self.assertEqual(len(attached), 2)
            with closing(db.connect(db_path)) as conn:
                docs = intake.list_documents(conn, opp_id)
            self.assertEqual(len(docs), 2)
            # Re-scan attaches nothing.
            attached, skipped = intake.scan_folders(Path(tmp) / "drop", db_path)
            self.assertEqual(attached, [])
            self.assertEqual(len(skipped), 2)

    def test_unknown_opp_and_stray_dirs_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            drop = Path(tmp) / "drop"
            ghost = drop / "opp-999"
            ghost.mkdir(parents=True)
            (ghost / "x.txt").write_text("orphan", encoding="utf-8")
            stray = drop / "random"
            stray.mkdir()
            (stray / "y.txt").write_text("stray", encoding="utf-8")
            attached, skipped = intake.scan_folders(drop, db_path)
            self.assertEqual(attached, [])
            self.assertEqual(len(skipped), 2)
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(db.list_opportunities(conn), [])

    def test_adopt_and_confirm_legacy_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "sol.txt"
            doc.write_text(
                "Request for Proposals (RFP) 26/27-04 for Random Moment S",
                encoding="utf-8",
            )
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = make_opp(
                    conn, document_path=str(doc),
                    agency_number="DOR RFP  #26/27-04",
                )
                self.assertEqual(intake.adopt_legacy_paths(conn), 1)
                self.assertEqual(intake.confirm_documents(conn, opp_id), 1)
                docs = intake.list_documents(conn, opp_id)
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["confirmed"], 1)

    def test_no_confirm_without_agency_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "sol.txt"
            doc.write_text("RFP 26/27-04", encoding="utf-8")
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = make_opp(conn, document_path=str(doc))
                intake.adopt_legacy_paths(conn)
                self.assertEqual(intake.confirm_documents(conn, opp_id), 0)


class MultiDocReadTest(unittest.TestCase):
    def test_single_doc_matches_legacy_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "sol.txt"
            doc.write_text("Only doc.", encoding="utf-8")
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = make_opp(conn, document_path=str(doc))
                self.assertEqual(
                    evals.read_opp_text(conn, opp_id),
                    evals.read_doc_text(str(doc)),
                )

    def test_combines_registered_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = make_opp(conn)
                for name, text in (("a.txt", "Alpha content."),
                                   ("b.txt", "Beta content.")):
                    doc = Path(tmp) / name
                    doc.write_text(text, encoding="utf-8")
                    intake.add_document(conn, opp_id, str(doc))
                combined = evals.read_opp_text(conn, opp_id)
                assert combined is not None
                self.assertIn("Alpha content.", combined)
                self.assertIn("Beta content.", combined)

    def test_quote_gate_checks_every_doc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            req_path = Path(tmp) / "reqs.json"
            req_path.write_text(
                '[{"code": "CERT-01", "category": "c",'
                ' "question": "q?", "guidance": ""}]',
                encoding="utf-8",
            )
            with closing(db.connect(db_path)) as conn:
                evals.load_requirements(conn, req_path)
                opp_id = make_opp(conn)
                for name, text in (
                    ("a.txt", "Nothing relevant here."),
                    ("b.txt", "The Contractor shall maintain SOC 2 compliance."),
                ):
                    doc = Path(tmp) / name
                    doc.write_text(text, encoding="utf-8")
                    intake.add_document(conn, opp_id, str(doc))
                row = evals.set_evaluation(
                    conn, opp_id, "CERT-01", "PASS",
                    quote="maintain SOC 2 compliance.",
                )
                self.assertEqual(row["verdict"], "PASS")
                self.assertEqual(row["verified"], 1)

    def test_no_docs_reads_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = make_opp(conn)
                self.assertIsNone(evals.read_opp_text(conn, opp_id))
                self.assertIsNone(evals.read_opp_text(conn, 999))


if __name__ == "__main__":
    unittest.main()
