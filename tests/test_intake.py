"""Slice 3 tests: drop-folder intake creates one stub record per new file."""

import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path

from proposal_creator import db, intake
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
            self.assertEqual(len(skipped), 1)

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
            self.assertIn("added 1", out.getvalue())
            self.assertIn("skipped 0", out.getvalue())


if __name__ == "__main__":
    unittest.main()
