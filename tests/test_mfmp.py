"""MFMP portal tests: advertisement fetch, summarize, download checks.

The committed JSON fixture is the live ad-16733 shape with contact PII
scrubbed. Network is faked — no live calls.
"""

import json
import os
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proposal_creator import db, intake, mfmp
from proposal_creator.__main__ import main as cli_main

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIXTURE = SAMPLES / "mfmp-16733-fixture.json"


def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FetchTest(unittest.TestCase):
    def test_rejects_non_numeric_id_without_network(self) -> None:
        def boom(url: str, headers: dict) -> bytes:
            raise AssertionError("must not fetch")

        with self.assertRaises(mfmp.MfmpError):
            mfmp.fetch_advertisement("RFP-16733", opener=boom)
        with self.assertRaises(mfmp.MfmpError):
            mfmp.fetch_advertisement("", opener=boom)

    def test_uses_json_endpoint_and_headers(self) -> None:
        seen: dict = {}

        def fake(url: str, headers: dict) -> bytes:
            seen["url"] = url
            seen["headers"] = headers
            return json.dumps({"advertisementId": "16733"}).encode()

        payload = mfmp.fetch_advertisement("16733", opener=fake)
        self.assertEqual(payload["advertisementId"], "16733")
        self.assertIn("/mfmp/pub/search/bids/detail?id=16733", seen["url"])
        self.assertEqual(seen["headers"]["Accept"], "application/json")

    def test_bad_payload_rejected(self) -> None:
        with self.assertRaises(mfmp.MfmpError):
            mfmp.fetch_advertisement(
                "16733", opener=lambda u, h: b"<html>nope</html>"
            )
        with self.assertRaises(mfmp.MfmpError):
            mfmp.fetch_advertisement(
                "16733", opener=lambda u, h: b'{"unexpected": true}'
            )

    def test_transport_error_wrapped(self) -> None:
        def fail(url: str, headers: dict) -> bytes:
            raise ConnectionError("down")

        with self.assertRaises(mfmp.MfmpError) as ctx:
            mfmp.fetch_advertisement("16733", opener=fail)
        self.assertIn("Could not reach MFMP", str(ctx.exception))


class SummarizeTest(unittest.TestCase):
    def test_fixture_summarizes(self) -> None:
        summary = mfmp.summarize_advertisement(fixture_payload())
        self.assertEqual(summary["portal_number"], "RFP-16733")
        self.assertEqual(summary["agency_number"], "DOR RFP  #26/27-04")
        self.assertEqual(summary["title"], "Random Moment Sampling")
        self.assertEqual(summary["agency"], "Department of Revenue (REV)")
        self.assertEqual(summary["open_date"], "2026-08-20")
        self.assertEqual(summary["close_date"], "2026-11-20")
        self.assertIn("/search/bids/detail/16733", summary["url"])
        self.assertEqual(len(summary["attachments"]), 15)
        first = summary["attachments"][0]
        self.assertEqual(first["filename"], "RFP 26-27-04 FINAL.pdf")
        self.assertEqual(first["attachmentId"], 39524)
        self.assertIn("attachmentId=39524", first["url"])
        self.assertEqual(first["kind"], "attachment")

    def test_sparse_payload_summarizes(self) -> None:
        summary = mfmp.summarize_advertisement({"advertisementId": "1"})
        self.assertEqual(
            (summary["portal_number"], summary["agency_number"],
             summary["title"], summary["attachments"]),
            (None, None, None, []),
        )

    def test_incomplete_docs_skipped(self) -> None:
        summary = mfmp.summarize_advertisement({
            "advertisementId": "1",
            "docs": [
                {"fileName": "no-id.pdf"},
                {"attachmentId": 7},
                "junk",
                {"fileName": "ok.pdf", "attachmentId": 7},
            ],
        })
        self.assertEqual(len(summary["attachments"]), 1)
        self.assertEqual(summary["attachments"][0]["filename"], "ok.pdf")


class MagicTest(unittest.TestCase):
    def test_known_types_checked(self) -> None:
        mfmp.check_magic("a.pdf", b"%PDF-1.7 rest")
        mfmp.check_magic("a.xlsx", b"PK\x03\x04 rest")
        with self.assertRaises(mfmp.MfmpError) as ctx:
            mfmp.check_magic("a.pdf", b"<!DOCTYPE html>")
        self.assertIn("did not return a .pdf", str(ctx.exception))

    def test_empty_rejected_unknown_accepted(self) -> None:
        with self.assertRaises(mfmp.MfmpError):
            mfmp.check_magic("a.pdf", b"")
        mfmp.check_magic("notes.txt", b"anything at all")
        mfmp.check_magic("data.xyz", b"<!DOCTYPE html>")


class FetchPortalMfmpTest(unittest.TestCase):
    def run_cli(self, environ: dict, *args: str) -> tuple[int, str]:
        out = StringIO()
        with patch.dict(os.environ, environ, clear=False):
            with redirect_stdout(out):
                code = cli_main(list(args))
        return code, out.getvalue()

    def test_full_flow_with_fake_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "drop", "external_id": None, "title": "T",
                    "agency": None, "url": None, "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": None, "notes": None,
                })
            payload = fixture_payload()
            payload["docs"] = payload["docs"][:2]

            def fake_fetch(ad_id: str, opener=None) -> dict:
                self.assertEqual(ad_id, "16733")
                return payload

            def fake_download(attachments, dest_dir, opener=None,
                              failure_hint=""):
                self.assertEqual(len(attachments), 2)
                self.assertIn("MFMP", failure_hint)
                Path(dest_dir).mkdir(parents=True, exist_ok=True)
                return [
                    {"filename": a["filename"],
                     "path": str(Path(dest_dir) / f"file{i}.pdf"),
                     "kind": "attachment", "status": "downloaded",
                     "error": None}
                    for i, a in enumerate(attachments)
                ]

            with patch.object(mfmp, "fetch_advertisement", fake_fetch), \
                    patch("proposal_creator.opengov.download_attachments",
                          fake_download):
                code, out = self.run_cli(
                    {}, "--db", str(db_path), "fetch-portal", str(opp_id),
                    "--mfmp", "16733", "--drop", str(Path(tmp) / "drop"),
                )
            self.assertEqual(code, 0)
            self.assertIn("backfilled portal_number: RFP-16733", out)
            self.assertIn("backfilled agency_number: DOR RFP  #26/27-04", out)
            with closing(db.connect(db_path)) as conn:
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                self.assertEqual(row["portal_number"], "RFP-16733")
                docs = intake.list_documents(conn, opp_id)
                self.assertEqual(len(docs), 2)
                self.assertEqual(docs[0]["source"], "mfmp")

    def test_exactly_one_source_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            code, out = self.run_cli(
                {}, "--db", str(db_path), "fetch-portal", "1",
            )
            self.assertEqual(code, 1)
            self.assertIn("exactly one", out)
            code, out = self.run_cli(
                {}, "--db", str(db_path), "fetch-portal", "1",
                "--from-file", "x.html", "--mfmp", "16733",
            )
            self.assertEqual(code, 1)
            self.assertIn("exactly one", out)

    def test_bad_ad_id_explains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            code, out = self.run_cli(
                {}, "--db", str(db_path), "fetch-portal", "1",
                "--mfmp", "RFP-16733",
            )
            self.assertEqual(code, 1)
            self.assertIn("advertisement id", out)


if __name__ == "__main__":
    unittest.main()
