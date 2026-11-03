"""Email notice tests: parsing shaped by the samples, matching, dedup.

The .eml fixtures are the contract — formats are asserted, not guessed.
No network: Gmail and portal fetching are faked or file-based.
"""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from proposal_creator import db, intake, notices, web

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def fresh_db(test: unittest.TestCase) -> Path:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    db_path = Path(tmp.name) / "test.db"
    db.init_db(db_path)
    return db_path


class ParseMfmpTest(unittest.TestCase):
    def setUp(self) -> None:
        raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
        self.notice = notices.parse_eml(raw)

    def test_routes_by_sender(self) -> None:
        self.assertEqual(self.notice["source"], "mfmp")
        self.assertEqual(self.notice["kind"], "new")

    def test_both_identifiers_and_link(self) -> None:
        self.assertEqual(self.notice["portal_number"], "RFP-16733")
        # Stored verbatim (note the double space); matching normalizes.
        self.assertEqual(self.notice["agency_number"], "DOR RFP  #26/27-04")
        self.assertEqual(
            self.notice["url"],
            "https://vendor.myfloridamarketplace.com/search/bids/detail/16733",
        )

    def test_details(self) -> None:
        self.assertEqual(self.notice["title"], "Random Moment Sampling")
        self.assertEqual(self.notice["agency"], "Department of Revenue (REV)")
        self.assertEqual(self.notice["open_date"], "2026-08-20")
        self.assertEqual(self.notice["close_date"], "2026-11-20")
        self.assertIn("Jimmy Olsen", self.notice["contact"])
        self.assertIn("floridarevenue", self.notice["contact"])

    def test_message_id_kept_for_dedup(self) -> None:
        self.assertIn("ashprdl01vip.ngmfmp.com", self.notice["message_id"])

    def test_non_posting_subjects_skipped(self) -> None:
        raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
        tweaked = raw.replace(
            b"Subject: Advertisement Posted - RFP-16733",
            b"Subject: Advertisement Updated - RFP-16733",
        )
        notice = notices.parse_eml(tweaked)
        self.assertEqual(notice["kind"], "skip")
        self.assertIn("skip_reason", notice)


class ParseOpengovTest(unittest.TestCase):
    def setUp(self) -> None:
        raw = (SAMPLES / "opengov-pompano-voip.eml").read_bytes()
        self.notice = notices.parse_eml(raw)

    def test_routes_by_sender(self) -> None:
        self.assertEqual(self.notice["source"], "opengov")
        self.assertEqual(self.notice["kind"], "new")

    def test_city_title_summary(self) -> None:
        self.assertEqual(self.notice["agency"], "City of Pompano Beach, FL")
        self.assertEqual(self.notice["title"], "VOIP Phone System")
        assert self.notice["summary"] is not None
        self.assertIn("unified communications", self.notice["summary"])
        self.assertNotIn("<", self.notice["summary"])  # tags stripped

    def test_no_numbers_or_deadline(self) -> None:
        self.assertIsNone(self.notice["portal_number"])
        self.assertIsNone(self.notice["agency_number"])
        self.assertIsNone(self.notice["open_date"])
        self.assertIsNone(self.notice["close_date"])

    def test_view_link_found(self) -> None:
        # Sample links were removed, but the shape (view vs subscription)
        # must still resolve to the view URL slot.
        assert self.notice["url"] is not None
        self.assertIn("TRACKING-LINK-REMOVED", self.notice["url"])
        self.assertNotIn("SUBSCRIPTION", self.notice["url"])

    def test_project_id_from_resolved_url(self) -> None:
        self.assertEqual(
            notices.opengov_project_id(
                "https://procurement.opengov.com/portal/copbfl/projects/309982"
            ),
            "309982",
        )
        self.assertIsNone(notices.opengov_project_id(self.notice["url"]))
        self.assertIsNone(notices.opengov_project_id(None))

    def test_unknown_sender_skipped(self) -> None:
        raw = (SAMPLES / "opengov-pompano-voip.eml").read_bytes()
        tweaked = raw.replace(b"@opengov.com", b"@evil.example")
        notice = notices.parse_eml(tweaked)
        self.assertEqual(notice["kind"], "skip")


class NumberCoreTest(unittest.TestCase):
    def test_cross_format_match(self) -> None:
        cores = {
            notices.number_core("DOR RFP  #26/27-04"),
            notices.number_core("(RFP) 26/27-04"),
            notices.number_core("RFP_26-27-04_FINAL"),
            notices.number_core("dor rfp #26/27-04"),
        }
        self.assertEqual(cores, {"262704"})

    def test_empty_safe(self) -> None:
        self.assertEqual(notices.number_core(None), "")
        self.assertEqual(notices.number_core("no digits"), "")


class ApplyNoticeTest(unittest.TestCase):
    def test_creates_stub_then_dedups(self) -> None:
        db_path = fresh_db(self)
        raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
        with closing(db.connect(db_path)) as conn:
            result, opp_id = notices.apply_notice(conn, notices.parse_eml(raw))
            self.assertEqual(result, "created")
            assert opp_id is not None
            row = db.get_opportunity(conn, opp_id)
            assert row is not None
            self.assertEqual(row["portal_number"], "RFP-16733")
            self.assertEqual(row["agency_number"], "DOR RFP  #26/27-04")
            self.assertEqual(row["title"], "Random Moment Sampling")
            self.assertEqual(row["status"], "new")
            self.assertIn("detail/16733".lower(), row["url"].lower())
            # Same bytes again: same result, no second record.
            result2, opp_id2 = notices.apply_notice(
                conn, notices.parse_eml(raw)
            )
            self.assertEqual((result2, opp_id2), ("created", opp_id))
            self.assertEqual(len(db.list_opportunities(conn)), 1)

    def test_matches_unnumbered_opp_by_doc_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            doc = Path(tmp) / "sol.txt"
            doc.write_text(
                "Request for Proposals (RFP) 26/27-04 for Random Moment S",
                encoding="utf-8",
            )
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "drop", "external_id": None,
                    "title": "RFP_26-27-04_FINAL", "agency": None, "url": None,
                    "open_date": None, "close_date": None, "status": "new",
                    "verdict": None, "blockers": None,
                    "document_path": str(doc), "notes": None,
                })
                raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
                result, matched = notices.apply_notice(
                    conn, notices.parse_eml(raw)
                )
                self.assertEqual((result, matched), ("matched", opp_id))
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                self.assertEqual(row["portal_number"], "RFP-16733")
                self.assertEqual(row["agency_number"], "DOR RFP  #26/27-04")
                self.assertIn("detail/16733".lower(), row["url"].lower())
                self.assertEqual(len(db.list_opportunities(conn)), 1)

    def test_backfill_never_overwrites(self) -> None:
        db_path = fresh_db(self)
        with closing(db.connect(db_path)) as conn:
            opp_id = db.add_opportunity(conn, {
                "source": "drop", "external_id": None, "title": "T",
                "agency": None, "url": "https://kept.example",
                "open_date": None, "close_date": None, "status": "new",
                "verdict": None, "blockers": None, "document_path": None,
                "portal_number": "RFP-16733", "agency_number": None,
                "notes": None,
            })
            raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
            result, matched = notices.apply_notice(conn, notices.parse_eml(raw))
            self.assertEqual((result, matched), ("matched", opp_id))
            row = db.get_opportunity(conn, opp_id)
            assert row is not None
            self.assertEqual(row["url"], "https://kept.example")
            self.assertEqual(row["agency_number"], "DOR RFP  #26/27-04")

    def test_ambiguous_doc_match_reported_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            for name in ("a.txt", "b.txt"):
                doc = Path(tmp) / name
                doc.write_text("RFP 26/27-04 everywhere", encoding="utf-8")
                with closing(db.connect(db_path)) as conn:
                    db.add_opportunity(conn, {
                        "source": "drop", "external_id": None, "title": name,
                        "agency": None, "url": None, "open_date": None,
                        "close_date": None, "status": "new", "verdict": None,
                        "blockers": None, "document_path": str(doc),
                        "notes": None,
                    })
            raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(
                    notices.apply_notice(conn, notices.parse_eml(raw))[0],
                    "ambiguous",
                )
                self.assertEqual(
                    notices.apply_notice(conn, notices.parse_eml(raw))[0],
                    "ambiguous",
                )
                self.assertEqual(len(db.list_opportunities(conn)), 2)

    def test_opengov_creates_thin_stub(self) -> None:
        db_path = fresh_db(self)
        raw = (SAMPLES / "opengov-pompano-voip.eml").read_bytes()
        with closing(db.connect(db_path)) as conn:
            result, opp_id = notices.apply_notice(conn, notices.parse_eml(raw))
            self.assertEqual(result, "created")
            row = db.get_opportunity(conn, opp_id)
            assert row is not None
            self.assertEqual(row["title"], "VOIP Phone System")
            self.assertEqual(row["agency"], "City of Pompano Beach, FL")
            self.assertIsNone(row["portal_number"])
            self.assertIn("unified communications", row["notes"])

    def test_id_less_mail_still_dedups(self) -> None:
        db_path = fresh_db(self)
        raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()
        stripped = b"\n".join(
            line for line in raw.split(b"\n") if not line.startswith(b"Message-id:")
        )
        with closing(db.connect(db_path)) as conn:
            first = notices.apply_notice(conn, notices.parse_eml(stripped))
            second = notices.apply_notice(conn, notices.parse_eml(stripped))
            self.assertEqual(first, second)
            self.assertEqual(len(db.list_opportunities(conn)), 1)


class NoticeDetailPageTest(unittest.TestCase):
    def test_identifiers_link_and_documents_shown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "mfmp", "external_id": None,
                    "title": "Random Moment Sampling",
                    "agency": "Department of Revenue (REV)",
                    "url": "https://vendor.myfloridamarketplace.com/x",
                    "open_date": None, "close_date": None, "status": "new",
                    "verdict": None, "blockers": None, "document_path": None,
                    "portal_number": "RFP-16733",
                    "agency_number": "DOR RFP  #26/27-04", "notes": None,
                })
                intake.add_document(conn, opp_id, "drop/opp-1/a.pdf")
                conn.execute(
                    "UPDATE documents SET confirmed = 1"
                    " WHERE opportunity_id = ?",
                    (opp_id,),
                )
                conn.commit()
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                page = web.render_detail(
                    row, [], documents=intake.list_documents(conn, opp_id)
                )
            self.assertIn("RFP-16733", page)
            self.assertIn("DOR RFP  #26/27-04", page)
            self.assertIn(
                '<a href="https://vendor.myfloridamarketplace.com/x">', page
            )
            self.assertIn("drop/opp-1/a.pdf", page)
            self.assertIn("confirmed", page)
            self.assertIn(f"drop/opp-{opp_id}/", page)

    def test_non_http_url_not_linked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                opp_id = db.add_opportunity(conn, {
                    "source": "x", "external_id": None, "title": "T",
                    "agency": None, "url": "javascript:alert(1)",
                    "open_date": None, "close_date": None, "status": "new",
                    "verdict": None, "blockers": None, "document_path": None,
                    "notes": None,
                })
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                page = web.render_detail(row, [], documents=[])
            self.assertNotIn("<a href=\"javascript", page)
            self.assertIn("javascript:alert(1)", page)  # inert text only
            self.assertIn("No documents yet", page)


class MigrationTest(unittest.TestCase):
    def test_old_db_gains_columns_and_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "old.db"
            with closing(sqlite3.connect(db_path)) as raw_conn:
                raw_conn.execute(
                    "CREATE TABLE opportunities ("
                    " id INTEGER PRIMARY KEY, source TEXT, title TEXT)"
                )
                raw_conn.commit()
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                cols = {
                    r["name"]
                    for r in conn.execute("PRAGMA table_info(opportunities)")
                }
                self.assertIn("portal_number", cols)
                self.assertIn("agency_number", cols)
                tables = {
                    r["name"]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertIn("documents", tables)
                self.assertIn("intake_seen", tables)


if __name__ == "__main__":
    unittest.main()
