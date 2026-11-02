"""Answer library tests: store, freshness, draft integration, web pages.

Structural only — every model call is faked. The live run (`draft 9`)
needs the real API and the owner's environment.
"""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

from helpers import handle_request
from proposal_creator import answers, config, db, draft, evals, model, prompts, web


def fresh_conn(test: unittest.TestCase) -> sqlite3.Connection:
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    db_path = Path(tmp.name) / "test.db"
    db.init_db(db_path)
    conn = db.connect(db_path)
    test.addCleanup(conn.close)
    return conn


class AnswerStoreTest(unittest.TestCase):
    def test_crud_round_trip(self) -> None:
        conn = fresh_conn(self)
        row = answers.save_answer(
            conn, "support", "We answer fast.",
            last_confirmed="2026-10-01", valid_until="2027-01-01",
        )
        self.assertEqual(row["label"], "support")
        self.assertEqual(row["answer_text"], "We answer fast.")
        self.assertEqual(row["last_confirmed"], "2026-10-01")
        self.assertEqual(row["valid_until"], "2027-01-01")
        self.assertEqual(row["status"], "active")
        self.assertIsNotNone(answers.get_answer(conn, row["id"]))
        self.assertEqual(len(answers.list_answers(conn)), 1)
        edited = answers.update_answer(
            conn, row["id"], answer_text="We answer faster.",
            valid_until="2027-06-01",
        )
        self.assertEqual(edited["answer_text"], "We answer faster.")
        self.assertEqual(edited["valid_until"], "2027-06-01")
        self.assertEqual(edited["label"], "support")  # untouched fields kept
        retired = answers.retire_answer(conn, row["id"])
        self.assertEqual(retired["status"], "retired")
        self.assertEqual(answers.list_active_answers(conn), [])
        self.assertEqual(len(answers.list_answers(conn)), 1)  # kept, not deleted

    def test_one_per_topic_update_never_duplicates(self) -> None:
        conn = fresh_conn(self)
        answers.save_answer(conn, "support", "First.", last_confirmed="2026-01-01")
        again = answers.save_answer(conn, "support", "Second.")
        rows = answers.list_answers(conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(again["answer_text"], "Second.")
        self.assertEqual(rows[0]["answer_text"], "Second.")
        # Re-saving re-confirms: an omitted date means today.
        self.assertEqual(again["last_confirmed"], answers.today_iso())

    def test_labels_match_case_insensitively(self) -> None:
        conn = fresh_conn(self)
        answers.save_answer(conn, "Support", "First.")
        answers.save_answer(conn, "support", "Second.")
        self.assertEqual(len(answers.list_answers(conn)), 1)
        other_id = answers.save_answer(conn, "other", "O.")["id"]
        with self.assertRaises(ValueError):
            answers.update_answer(conn, other_id, label="SUPPORT")

    def test_save_keeps_dates_and_status_on_conflict(self) -> None:
        conn = fresh_conn(self)
        answers.save_answer(
            conn, "support", "First.", valid_until="2027-01-01",
        )
        answers.retire_answer(conn, answers.get_by_label(conn, "support")["id"])
        row = answers.save_answer(conn, "support", "Second.")
        self.assertEqual(row["valid_until"], "2027-01-01")
        self.assertEqual(row["status"], "retired")
        cleared = answers.update_answer(conn, row["id"], valid_until="")
        self.assertIsNone(cleared["valid_until"])

    def test_defaults_and_validation(self) -> None:
        conn = fresh_conn(self)
        row = answers.save_answer(conn, "  support ", "  Text. ")
        self.assertEqual(row["last_confirmed"], answers.today_iso())
        self.assertIsNone(row["valid_until"])
        for bad_label, bad_text in (("", "x"), ("x", ""), ("  ", "x")):
            with self.assertRaises(ValueError):
                answers.save_answer(conn, bad_label, bad_text)
        for bad_date in ("01/02/2026", "2026-13-01", "yesterday"):
            with self.assertRaises(ValueError):
                answers.save_answer(conn, "l", "t", last_confirmed=bad_date)
            with self.assertRaises(ValueError):
                answers.save_answer(conn, "l", "t", valid_until=bad_date)
        with self.assertRaises(ValueError):
            answers.save_answer(conn, "l", "t", status="archived")
        with self.assertRaises(ValueError):
            answers.update_answer(conn, 999, answer_text="nope")

    def test_migrates_older_db_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "old.db"
            with closing(sqlite3.connect(db_path)) as raw:
                raw.execute("CREATE TABLE opportunities (id INTEGER PRIMARY KEY)")
                raw.commit()
            with closing(db.connect(db_path)) as conn:
                answers.save_answer(conn, "support", "Text.")
                self.assertEqual(len(answers.list_answers(conn)), 1)


class FreshnessTest(unittest.TestCase):
    def entry(self, **overrides: object) -> dict:
        base = {
            "label": "support",
            "answer_text": "Text.",
            "last_confirmed": "2026-10-15",
            "valid_until": None,
            "status": "active",
        }
        base.update(overrides)
        return base  # type: ignore[return-value]

    def test_expired_only_when_past_valid_until(self) -> None:
        self.assertEqual(
            answers.freshness(self.entry(valid_until="2026-11-01"), today="2026-11-02"),
            "expired",
        )
        # The valid-until day itself still counts as valid.
        self.assertEqual(
            answers.freshness(
                self.entry(last_confirmed="2026-11-01", valid_until="2026-11-02"),
                today="2026-11-02",
            ),
            "fresh",
        )
        self.assertFalse(answers.is_expired(self.entry()))

    def test_stale_when_older_than_threshold(self) -> None:
        old = self.entry(last_confirmed="2026-01-01")
        self.assertTrue(answers.is_stale(old, today="2026-11-02"))
        self.assertEqual(
            answers.freshness(old, today="2026-11-02"), "stale"
        )
        fresh = self.entry(last_confirmed="2026-10-15")
        self.assertFalse(answers.is_stale(fresh, today="2026-11-02"))
        self.assertEqual(answers.freshness(fresh, today="2026-11-02"), "fresh")

    def test_stale_boundary_is_exclusive(self) -> None:
        # Exactly 90 days old is fresh; 91 is stale (default threshold).
        self.assertFalse(
            answers.is_stale(
                self.entry(last_confirmed="2026-08-04"), today="2026-11-02"
            )
        )
        self.assertTrue(
            answers.is_stale(
                self.entry(last_confirmed="2026-08-03"), today="2026-11-02"
            )
        )

    def test_expired_wins_over_stale(self) -> None:
        self.assertEqual(
            answers.freshness(
                self.entry(
                    last_confirmed="2020-01-01", valid_until="2020-06-01"
                ),
                today="2026-11-02",
            ),
            "expired",
        )

    def test_threshold_configurable(self) -> None:
        entry = self.entry(last_confirmed="2026-10-01")
        self.assertEqual(answers.stale_days_from_env({}), 90)
        self.assertEqual(
            answers.stale_days_from_env({"PROPOSAL_ANSWER_STALE_DAYS": "10"}), 10
        )
        for bad in ("", "soon", "-5", "0"):
            self.assertEqual(
                answers.stale_days_from_env({"PROPOSAL_ANSWER_STALE_DAYS": bad}),
                90,
            )
        self.assertFalse(answers.is_stale(entry, today="2026-11-02", stale_days=90))
        self.assertTrue(answers.is_stale(entry, today="2026-11-02", stale_days=10))

    def test_unknown_age_flags_stale(self) -> None:
        self.assertTrue(answers.is_stale(self.entry(last_confirmed="bogus")))
        self.assertTrue(answers.is_stale({"label": "x"}))


def post(path: str, fields: dict) -> bytes:
    body = urlencode(fields).encode("utf-8")
    return (
        f"POST {path} HTTP/1.0\r\nHost: x\r\n"
        "Content-Type: application/x-www-form-urlencoded\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode("utf-8") + body


def seed_opp(db_path: Path) -> int:
    db.seed_examples(db_path)
    with closing(db.connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id FROM opportunities ORDER BY id"
        ).fetchone()
        assert row is not None
        return int(row["id"])


class AnswersPageTest(unittest.TestCase):
    def test_manage_page_lists_and_flags_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                answers.save_answer(
                    conn, "fresh", "New text.",
                    last_confirmed="2026-10-15",
                )
                answers.save_answer(
                    conn, "old", "Old text.",
                    last_confirmed="2020-01-01",
                )
                answers.save_answer(
                    conn, "gone", "Gone text.",
                    last_confirmed="2026-10-15", valid_until="2026-01-01",
                )
                rows = answers.list_answers(conn)
            page = web.render_answers(rows, stale_days=90)
            for needle in ("fresh", "New text.", "old", "gone",
                           "Last confirmed", "Valid until", "Status"):
                self.assertIn(needle, page)
            self.assertIn("EXPIRED", page)
            self.assertIn("STALE", page)
            self.assertIn('class="fail"', page)
            self.assertIn('class="warn"', page)
            self.assertIn("Add an answer", page)
            self.assertIn("/answers/add", page)
            self.assertIn("/answers/update", page)
            self.assertIn("/answers/retire", page)

    def test_manage_page_escapes_untrusted_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                answers.save_answer(conn, "<b>x</b>", "<script>alert(1)</script>")
                rows = answers.list_answers(conn)
            page = web.render_answers(rows)
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("&lt;b&gt;x&lt;/b&gt;", page)

    def test_add_edit_and_retire_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            added = handle_request(
                db_path,
                post("/answers/add", {
                    "label": "support",
                    "answer_text": "We answer fast.",
                    "last_confirmed": "2026-10-15",
                    "valid_until": "",
                }),
            )
            self.assertIn("303", added.splitlines()[0])
            with closing(db.connect(db_path)) as conn:
                row = answers.get_by_label(conn, "support")
                assert row is not None
                answer_id = row["id"]
            # Re-adding the same label edits instead of duplicating.
            handle_request(
                db_path,
                post("/answers/add", {
                    "label": "Support", "answer_text": "We answer faster.",
                }),
            )
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(len(answers.list_answers(conn)), 1)
                self.assertEqual(
                    answers.get_answer(conn, answer_id)["answer_text"],
                    "We answer faster.",
                )
            edited = handle_request(
                db_path,
                post("/answers/update", {
                    "id": str(answer_id),
                    "label": "support",
                    "answer_text": "Edited.",
                    "last_confirmed": "2026-10-20",
                    "valid_until": "2027-01-01",
                    "status": "active",
                }),
            )
            self.assertIn("303", edited.splitlines()[0])
            with closing(db.connect(db_path)) as conn:
                row = answers.get_answer(conn, answer_id)
                assert row is not None
                self.assertEqual(row["answer_text"], "Edited.")
                self.assertEqual(row["valid_until"], "2027-01-01")
            retired = handle_request(
                db_path, post("/answers/retire", {"id": str(answer_id)})
            )
            self.assertIn("303", retired.splitlines()[0])
            with closing(db.connect(db_path)) as conn:
                row = answers.get_answer(conn, answer_id)
                assert row is not None
                self.assertEqual(row["status"], "retired")
            shown = handle_request(db_path, b"GET /answers HTTP/1.0\r\nHost: x\r\n\r\n")
            self.assertIn("200", shown.splitlines()[0])
            self.assertIn("Edited.", shown)
            self.assertIn("retired", shown)

    def test_add_rejects_empty_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            response = handle_request(
                db_path, post("/answers/add", {"label": "", "answer_text": "x"})
            )
            self.assertIn("400", response.splitlines()[0])
            self.assertIn("label", response.lower())

    def test_save_from_opportunity_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            opp_id = seed_opp(db_path)
            detail = handle_request(
                db_path, f"GET /opp/{opp_id} HTTP/1.0\r\nHost: x\r\n\r\n".encode()
            )
            self.assertIn("Save to answer library", detail)
            self.assertIn(f"/opp/{opp_id}/save-answer", detail)
            saved = handle_request(
                db_path,
                post(f"/opp/{opp_id}/save-answer", {
                    "label": "support", "answer_text": "From the draft.",
                }),
            )
            self.assertIn("303", saved.splitlines()[0])
            with closing(db.connect(db_path)) as conn:
                row = answers.get_by_label(conn, "support")
                assert row is not None
                self.assertEqual(row["answer_text"], "From the draft.")
                self.assertEqual(row["last_confirmed"], answers.today_iso())
            missing = handle_request(
                db_path, post("/opp/999/save-answer", {
                    "label": "x", "answer_text": "y",
                }),
            )
            self.assertIn("404", missing.splitlines()[0])

    def test_list_page_links_to_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            seed_opp(db_path)
            response = handle_request(db_path, b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
            self.assertIn("/answers", response)


SENTINEL = "[REDACTED]"
DOC_TEXT = "The Contractor shall maintain SOC 2 Type II compliance."
PROFILE = "Company: Demo LLC. Certifications: SOC 2 Type II."
REQS = [
    {
        "code": "CERT-01",
        "category": "certifications",
        "question": "Required certifications held?",
        "guidance": "Compare to profile.",
    },
]


def make_draft_db(tmp: str) -> tuple[Path, int]:
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        req_path = Path(tmp) / "reqs.json"
        req_path.write_text(json.dumps(REQS), encoding="utf-8")
        evals.load_requirements(conn, req_path)
        doc = Path(tmp) / "sol.txt"
        doc.write_text(DOC_TEXT, encoding="utf-8")
        opp_id = db.add_opportunity(conn, {
            "source": "drop", "external_id": None, "title": "Test RFP",
            "agency": None, "url": None, "open_date": None, "close_date": None,
            "status": "new", "verdict": None, "blockers": None,
            "document_path": str(doc), "notes": None,
        })
        evals.set_evaluation(
            conn, opp_id, "CERT-01", "PASS",
            quote="maintain SOC 2 Type II compliance.",
            evaluated_by="human",
        )
    return db_path, opp_id


def test_config() -> config.ModelConfig:
    return config.ModelConfig(
        provider="meta",
        base_url="https://api.meta.ai/v1",
        api_key=SENTINEL,
        model="muse-spark-1.3",
        reasoning_effort="minimal",
        max_tokens=4000,
        temperature=0.0,
        timeout=120,
        company_profile="profile.md",
    )


class DraftLibraryPromptTest(unittest.TestCase):
    def test_library_rules_in_source_of_truth(self) -> None:
        prompt = prompts.get_prompt("Draft")
        for needle in (
            "SAVED ANSWERS",
            "human-written and approved",
            "wherever it fits a required section of THIS solicitation",
            "confirm still current",
            "Where neither the profile nor the library covers",
            "from your saved answers",
            '"library_stale"',
        ):
            self.assertIn(needle, prompt)
        self.assertEqual(draft.SYSTEM_PROMPT, prompt)

    def test_messages_carry_library_with_freshness_flags(self) -> None:
        library = [
            {"label": "support", "answer_text": "We answer fast.",
             "last_confirmed": "2026-10-15", "freshness": "fresh"},
            {"label": "old", "answer_text": "Old words.",
             "last_confirmed": "2020-01-01", "freshness": "stale"},
            {"label": "gone", "answer_text": "Gone words.",
             "last_confirmed": "2026-10-15", "freshness": "expired"},
        ]
        messages = draft.build_messages(
            PROFILE,
            [{"code": "CERT-01", "question": "Certs?",
              "verdict": "PASS", "note": None}],
            DOC_TEXT,
            library,
        )
        user = messages[1]["content"]
        for needle in ("SAVED ANSWERS", "[support]", "We answer fast.",
                       "[old]", "STALE", "[gone]", "EXPIRED",
                       "confirm still current"):
            self.assertIn(needle, user)

    def test_empty_library_says_so(self) -> None:
        for messages in (
            draft.build_messages(PROFILE, [], DOC_TEXT),
            draft.build_messages(PROFILE, [], DOC_TEXT, []),
        ):
            self.assertIn("no saved answers", messages[1]["content"])


class DraftLibraryRunTest(unittest.TestCase):
    def run_with_library(self, tmp: str) -> tuple[dict, list]:
        db_path, opp_id = make_draft_db(tmp)
        with closing(db.connect(db_path)) as conn:
            answers.save_answer(
                conn, "customer support",
                "Our help desk answers in under an hour.",
                last_confirmed="2026-10-15",
            )
            answers.save_answer(
                conn, "legacy", "We used to offer fax support.",
                last_confirmed="2020-01-01",
            )
            answers.save_answer(conn, "retired", "Do not use this.")
            answers.retire_answer(
                conn, answers.get_by_label(conn, "retired")["id"]
            )
        seen: list = []
        payload = {
            "draft_markdown": "# Draft\n\nSupport words. Old words. [NEEDS ME].",
            "commitments": [
                {"claim": "We hold SOC 2 Type II.", "status": "supported"},
                {"claim": "Help desk answers fast.",
                 "status": "library"},
                {"claim": "Fax support still offered.",
                 "status": "library_stale"},
                {"claim": "Fixed-price bid of $X.", "status": "needs_me"},
            ],
        }

        def fake(messages: list) -> model.ModelResult:
            seen.extend(messages)
            return model.ModelResult(json.dumps(payload), {})

        with closing(db.connect(db_path)) as conn:
            report = draft.run_draft(
                conn, opp_id, test_config(), PROFILE,
                complete_fn=fake, drafts_dir=Path(tmp) / "drafts",
            )
        return report, seen

    def test_draft_reads_active_library_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, seen = self.run_with_library(tmp)
            user = seen[1]["content"]
            self.assertIn("customer support", user)
            self.assertIn("under an hour", user)
            self.assertIn("[STALE", user)  # legacy answer flagged in the feed
            self.assertIn("confirm still current", user)
            self.assertNotIn("Do not use this", user)  # retired: not fed

    def test_checklist_is_three_way(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report, _ = self.run_with_library(tmp)
            self.assertEqual(
                [c["status"] for c in report["commitments"]],
                ["supported", "library", "library_stale", "needs_me"],
            )
            body = Path(report["commitments_path"]).read_text(encoding="utf-8")
            self.assertIn("supported by the company profile", body)
            self.assertIn("from your saved answers", body)
            self.assertIn(
                "from your saved answers — confirm still current", body
            )
            self.assertIn("[NEEDS ME]", body)

    def test_unknown_checklist_status_still_fails_closed(self) -> None:
        rendered = draft.render_commitments(
            1, "T",
            [{"claim": "Anything.", "status": "bogus"},
             {"claim": "Fresh library claim.", "status": "library"}],
            "m",
        )
        self.assertIn("[NEEDS ME]", rendered)
        self.assertIn("(from your saved answers) Fresh library claim.", rendered)


if __name__ == "__main__":
    unittest.main()
