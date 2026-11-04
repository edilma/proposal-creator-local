"""OpenGov portal tests: parse a saved page, backfill, download files.

The committed fixture is the real page shape with URLs, emails, phones,
and personal names scrubbed; the raw save stays out of Git (see
.gitignore). Downloads are faked — no network.
"""

import os
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proposal_creator import db, intake, opengov
from proposal_creator.__main__ import main as cli_main

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIXTURE = SAMPLES / "opengov-page-fixture.html"


class UnwrapTest(unittest.TestCase):
    def test_wrapper_restores_original(self) -> None:
        original = opengov.unwrap_view_source(
            FIXTURE.read_text(encoding="utf-8")
        )
        self.assertIn("window.__data=", original)
        self.assertNotIn("line-content", original)
        self.assertNotIn("line-number", original)

    def test_entities_decoded_per_line(self) -> None:
        wrapper = (
            '<td class="line-content">'
            '<span class="html-tag">&lt;a href="x?a=1&amp;b=2"&gt;</span>'
            "</td>"
        )
        self.assertEqual(
            opengov.unwrap_view_source(wrapper), '<a href="x?a=1&b=2">'
        )

    def test_plain_save_passes_through(self) -> None:
        page = "<html><script>window.__data={}</script></html>"
        self.assertEqual(opengov.unwrap_view_source(page), page)

    def test_wrapper_without_lines_rejected(self) -> None:
        with self.assertRaises(opengov.PortalError):
            opengov.unwrap_view_source(
                '<div class="source-container"></div>'
            )


class ExtractTest(unittest.TestCase):
    def test_fixture_summarizes(self) -> None:
        summary = opengov.summarize_project(
            opengov.extract_project(FIXTURE.read_text(encoding="utf-8"))
        )
        self.assertEqual(summary["portal_number"], "322586")
        self.assertEqual(summary["agency_number"], "Y26-1015")
        self.assertEqual(
            summary["title"],
            "West Orange Trail Bicycle Concession - Winter Garden Station",
        )
        self.assertEqual(summary["open_date"], "2026-11-02")
        self.assertEqual(summary["close_date"], "2026-12-03")
        self.assertEqual(len(summary["attachments"]), 2)
        attachment = summary["attachments"][0]
        self.assertEqual(attachment["kind"], "attachment")
        self.assertEqual(
            attachment["filename"],
            "RFP_Y26-1015-JA_WOT_Bicycle_Concession_-_WGS.pdf",
        )
        self.assertEqual(attachment["appendix"], "A")
        self.assertTrue(attachment["url"].startswith("https://"))
        generated = summary["attachments"][1]
        self.assertEqual(generated["kind"], "generated")
        self.assertEqual(
            generated["filename"],
            "West_Orange_Trail_Bicycle_Concession_-_Winter_Garden_Station.pdf",
        )
        self.assertTrue(generated["url"].startswith("https://"))

    def test_generated_variants_tolerated(self) -> None:
        base = {"id": 1, "attachments": [
            {"filename": "a.pdf", "url": "https://x/a"},
        ]}
        self.assertEqual(len(opengov.summarize_project(base)["attachments"]), 1)
        none_case = dict(base, documentAttachment=None)
        self.assertEqual(
            len(opengov.summarize_project(none_case)["attachments"]), 1
        )
        list_case = dict(base, documentAttachment=[
            {"filename": "g.pdf", "url": "https://x/g"},
            "junk",
        ])
        items = opengov.summarize_project(list_case)["attachments"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1]["kind"], "generated")
        junk_case = dict(base, documentAttachment="junk")
        self.assertEqual(
            len(opengov.summarize_project(junk_case)["attachments"]), 1
        )

    def test_missing_blob_rejected(self) -> None:
        for bad in ("<html>empty</html>", "window.__data="):
            with self.assertRaises(opengov.PortalError):
                opengov.extract_project(bad)

    def test_missing_project_rejected(self) -> None:
        with self.assertRaises(opengov.PortalError):
            opengov.extract_project(
                "<script>window.__data={\"nope\":true}</script>"
            )

    def test_truncated_project_rejected(self) -> None:
        with self.assertRaises(opengov.PortalError):
            opengov.extract_project(
                '<script>window.__data={"x":1,"project":{"id":1</script>'
            )

    def test_sparse_project_summarizes_to_none(self) -> None:
        summary = opengov.summarize_project({})
        self.assertEqual(
            (summary["portal_number"], summary["agency_number"],
             summary["title"], summary["open_date"], summary["close_date"],
             summary["attachments"]),
            (None, None, None, None, None, []),
        )
        self.assertIsNone(
            opengov.summarize_project({"proposalDeadline": "soon"})["close_date"]
        )


class FilenameTest(unittest.TestCase):
    def test_traversal_and_illegal_chars_neutered(self) -> None:
        self.assertEqual(
            opengov.safe_filename("../../x.pdf", "", "pdf", 1), "x.pdf"
        )
        self.assertEqual(
            opengov.safe_filename('a<b>c:"d"', "", "", 2), "a_b_c__d_"
        )

    def test_empty_name_falls_back(self) -> None:
        self.assertEqual(
            opengov.safe_filename("", "B", "pdf", 3), "attachment-B.pdf"
        )
        self.assertEqual(opengov.safe_filename("", "", "", 4), "attachment-4")


class DownloadTest(unittest.TestCase):
    def test_writes_bytes_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = opengov.download_attachments(
                [{"filename": "a.pdf", "url": "https://x/a",
                  "appendix": "A", "extension": "pdf"}],
                Path(tmp) / "dest",
                opener=lambda url: b"%PDF-bytes",
            )
            self.assertEqual(results[0]["status"], "downloaded")
            dest = Path(tmp) / "dest"
            self.assertEqual((dest / "a.pdf").read_bytes(), b"%PDF-bytes")
            self.assertEqual(list(dest.glob("*.part")), [])

    def test_existing_file_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "dest"
            dest.mkdir()
            (dest / "a.pdf").write_bytes(b"original")

            def boom(url: str) -> bytes:
                raise AssertionError("must not re-download")

            results = opengov.download_attachments(
                [{"filename": "a.pdf", "url": "https://x/a",
                  "appendix": "", "extension": "pdf"}],
                dest,
                opener=boom,
            )
            self.assertEqual(results[0]["status"], "skipped-exists")
            self.assertEqual((dest / "a.pdf").read_bytes(), b"original")

    def test_failure_names_resave(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def fail(url: str) -> bytes:
                raise ConnectionError("403 Forbidden")

            results = opengov.download_attachments(
                [{"filename": "a.pdf", "url": "https://x/a",
                  "appendix": "", "extension": "pdf"},
                 {"filename": "b.pdf", "url": "",
                  "appendix": "", "extension": "pdf"}],
                Path(tmp) / "dest",
                opener=fail,
            )
            self.assertEqual(results[0]["status"], "failed")
            self.assertIn("re-save", results[0]["error"])
            self.assertEqual(results[1]["status"], "failed")

    def test_no_partial_file_on_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "dest"
            results = opengov.download_attachments(
                [{"filename": "a.pdf", "url": "https://x/a",
                  "appendix": "", "extension": "pdf"}],
                dest,
                opener=lambda url: (_ for _ in ()).throw(
                    TimeoutError("slow")
                ),
            )
            self.assertEqual(results[0]["status"], "failed")
            self.assertEqual(list(dest.glob("*")), [])

    def test_same_name_batch_mates_numbered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "dest"
            results = opengov.download_attachments(
                [
                    {"filename": "doc.pdf", "url": "https://x/1",
                     "appendix": "A", "extension": "pdf", "kind": "attachment"},
                    {"filename": "doc.pdf", "url": "https://x/2",
                     "appendix": "", "extension": "pdf", "kind": "generated"},
                ],
                dest,
                opener=lambda url: f"bytes for {url}".encode(),
            )
            self.assertEqual(
                [r["filename"] for r in results], ["doc.pdf", "doc (2).pdf"]
            )
            self.assertEqual(
                [r["status"] for r in results], ["downloaded", "downloaded"]
            )
            self.assertEqual(
                (dest / "doc (2).pdf").read_bytes(), b"bytes for https://x/2"
            )
            self.assertEqual(results[1]["kind"], "generated")


class ApplyPortalDataTest(unittest.TestCase):
    def test_backfill_fills_empties_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "opengov", "external_id": None,
                    "title": "Email title kept", "agency": "City",
                    "url": "https://portal/x", "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": None, "notes": None,
                })
                filled = opengov.apply_portal_data(conn, opp_id, {
                    "portal_number": "322586", "agency_number": "Y26-1015",
                    "title": "Portal title", "open_date": "2026-11-02",
                    "close_date": "2026-12-03", "attachments": [],
                })
                self.assertEqual(
                    filled,
                    {"portal_number": "322586", "agency_number": "Y26-1015",
                     "open_date": "2026-11-02", "close_date": "2026-12-03"},
                )
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                self.assertEqual(row["title"], "Email title kept")

    def test_unknown_opp_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(opengov.PortalError):
                    opengov.apply_portal_data(conn, 999, {})


class FetchPortalCommandTest(unittest.TestCase):
    def run_cli(self, environ: dict, *args: str) -> tuple[int, str]:
        out = StringIO()
        with patch.dict(os.environ, environ, clear=False):
            with redirect_stdout(out):
                code = cli_main(list(args))
        return code, out.getvalue()

    def test_full_flow_with_fake_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "opengov", "external_id": None,
                    "title": "West Orange Trail", "agency": "Orange County",
                    "url": "https://portal/x", "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": None, "notes": None,
                })

            def fake_download(attachments, dest_dir, opener=None,
                              failure_hint=""):
                assert len(attachments) == 2
                assert [a["kind"] for a in attachments] == [
                    "attachment", "generated",
                ]
                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                results = []
                for item in attachments:
                    path = Path(dest_dir) / item["filename"]
                    path.write_bytes(b"fake pdf")
                    results.append({
                        "filename": path.name, "path": str(path),
                        "kind": item["kind"],
                        "status": "downloaded", "error": None,
                    })
                return results

            with patch.object(
                opengov, "download_attachments", fake_download
            ):
                code, out = self.run_cli(
                    {}, "--db", str(db_path), "fetch-portal", str(opp_id),
                    "--from-file", str(FIXTURE),
                    "--drop", str(Path(tmp) / "drop"),
                )
            self.assertEqual(code, 0)
            self.assertIn("backfilled agency_number: Y26-1015", out)
            self.assertIn("backfilled close_date: 2026-12-03", out)
            self.assertIn("downloaded:", out)
            self.assertIn("(attachment)", out)
            self.assertIn("(project document)", out)
            with closing(db.connect(db_path)) as conn:
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                self.assertEqual(row["portal_number"], "322586")
                docs = intake.list_documents(conn, opp_id)
                self.assertEqual(len(docs), 2)
                self.assertEqual(docs[0]["source"], "portal")

    def test_rerun_downloads_only_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            drop = Path(tmp) / "drop"
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "opengov", "external_id": None,
                    "title": "West Orange Trail", "agency": "Orange County",
                    "url": "https://portal/x", "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": None, "notes": None,
                })
                folder = drop / f"opp-{opp_id}"
                folder.mkdir(parents=True)
                kept = folder / "RFP_Y26-1015-JA_WOT_Bicycle_Concession_-_WGS.pdf"
                kept.write_bytes(b"already here")
                intake.add_document(conn, opp_id, str(kept), source="portal")

            def fake_download(attachments, dest_dir, opener=None,
                              failure_hint=""):
                results = []
                for item in attachments:
                    path = Path(dest_dir) / item["filename"]
                    if path.exists():
                        results.append({
                            "filename": path.name, "path": str(path),
                            "kind": item["kind"],
                            "status": "skipped-exists", "error": None,
                        })
                    else:
                        path.write_bytes(b"fake pdf")
                        results.append({
                            "filename": path.name, "path": str(path),
                            "kind": item["kind"],
                            "status": "downloaded", "error": None,
                        })
                return results

            with patch.object(
                opengov, "download_attachments", fake_download
            ):
                code, out = self.run_cli(
                    {}, "--db", str(db_path), "fetch-portal", str(opp_id),
                    "--from-file", str(FIXTURE), "--drop", str(drop),
                )
            self.assertEqual(code, 0)
            self.assertIn("skipped-exists:", out)
            self.assertIn("downloaded:", out)
            with closing(db.connect(db_path)) as conn:
                docs = intake.list_documents(conn, opp_id)
                self.assertEqual(len(docs), 2)

    def test_failed_download_keeps_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "opengov", "external_id": None, "title": "T",
                    "agency": None, "url": None, "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": None, "notes": None,
                })
            with patch.object(
                opengov, "download_attachments",
                lambda a, d, opener=None, failure_hint="": [
                    {"filename": "a.pdf", "path": "x",
                     "status": "failed", "error": "gone stale — re-save"}
                ],
            ):
                code, out = self.run_cli(
                    {}, "--db", str(db_path), "fetch-portal", str(opp_id),
                    "--from-file", str(FIXTURE),
                    "--drop", str(Path(tmp) / "drop"),
                )
            self.assertEqual(code, 1)
            self.assertIn("re-save", out)
            with closing(db.connect(db_path)) as conn:
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                self.assertEqual(row["agency_number"], "Y26-1015")

    def test_bad_file_and_unknown_opp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            bad = Path(tmp) / "bad.html"
            bad.write_text("<html>no data here</html>", encoding="utf-8")
            code, out = self.run_cli(
                {}, "--db", str(db_path), "fetch-portal", "1",
                "--from-file", str(bad),
            )
            self.assertEqual(code, 1)
            self.assertIn("No embedded OpenGov data", out)
            code, out = self.run_cli(
                {}, "--db", str(db_path), "fetch-portal", "999",
                "--from-file", str(FIXTURE),
            )
            self.assertEqual(code, 1)
            self.assertIn("No opportunity with id 999", out)


if __name__ == "__main__":
    unittest.main()
