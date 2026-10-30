"""Roll-up tests: stored evaluations + deadline -> overall verdict."""

import json
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import os

from proposal_creator import db, evals, evaluate, model, rollup
from proposal_creator.__main__ import main as cli_main

TODAY = date(2026, 10, 29)
REQS = [
    {"code": "AAA-01", "category": "c", "question": "q?", "guidance": None},
    {"code": "BBB-02", "category": "c", "question": "q?", "guidance": None},
]


def make_db(tmp: str, close_date: str | None = None) -> tuple[Path, int]:
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        req_path = Path(tmp) / "reqs.json"
        req_path.write_text(json.dumps(REQS), encoding="utf-8")
        evals.load_requirements(conn, req_path)
        opp_id = db.add_opportunity(
            conn,
            {
                "source": "drop",
                "title": "Test",
                "status": "new",
                "close_date": close_date,
            },
        )
    return db_path, opp_id


def set_all(conn: object, opp_id: int, mapping: dict) -> None:
    for code, verdict in mapping.items():
        quote = "unverified sentence." if verdict == "PASS" else None
        evals.set_evaluation(conn, opp_id, code, verdict, quote=quote)


def row(conn: object, opp_id: int) -> tuple:
    r = db.get_opportunity(conn, opp_id)
    return r["verdict"], r["blockers"]


class ComputeTest(unittest.TestCase):
    def test_all_pass_can_bid(self) -> None:
        verdict, blocker = rollup.compute_rollup(
            {"close_date": None},
            [
                {"requirement_code": "AAA-01", "verdict": "PASS"},
                {"requirement_code": "BBB-02", "verdict": "PASS"},
            ],
            TODAY,
        )
        self.assertEqual((verdict, blocker), ("CAN BID", ""))

    def test_one_fail_cannot_bid(self) -> None:
        verdict, blocker = rollup.compute_rollup(
            {"close_date": None},
            [
                {"requirement_code": "AAA-01", "verdict": "PASS"},
                {"requirement_code": "BBB-02", "verdict": "FAIL"},
                {"requirement_code": "CCC-03", "verdict": "UNKNOWN"},
            ],
            TODAY,
        )
        self.assertEqual(verdict, "CANNOT BID")
        self.assertIn("BBB-02", blocker)
        self.assertNotIn("AAA-01", blocker)

    def test_fail_codes_sorted(self) -> None:
        _, blocker = rollup.compute_rollup(
            {"close_date": None},
            [
                {"requirement_code": "BBB-02", "verdict": "FAIL"},
                {"requirement_code": "AAA-01", "verdict": "FAIL"},
            ],
            TODAY,
        )
        self.assertEqual(blocker, "Failed: AAA-01, BBB-02.")

    def test_unknown_needs_info(self) -> None:
        verdict, blocker = rollup.compute_rollup(
            {"close_date": None},
            [
                {"requirement_code": "AAA-01", "verdict": "PASS"},
                {"requirement_code": "BBB-02", "verdict": "UNKNOWN"},
            ],
            TODAY,
        )
        self.assertEqual(verdict, "NEEDS INFO")
        self.assertIn("BBB-02", blocker)

    def test_passed_deadline_cannot_bid_regardless(self) -> None:
        verdict, blocker = rollup.compute_rollup(
            {"close_date": "2026-10-28"},
            [{"requirement_code": "AAA-01", "verdict": "PASS"}],
            TODAY,
        )
        self.assertEqual(verdict, "CANNOT BID")
        self.assertIn("2026-10-28", blocker)

    def test_open_deadline_does_not_block(self) -> None:
        verdict, _ = rollup.compute_rollup(
            {"close_date": "2026-11-30"},
            [{"requirement_code": "AAA-01", "verdict": "UNKNOWN"}],
            TODAY,
        )
        self.assertEqual(verdict, "NEEDS INFO")

    def test_na_treated_as_satisfied(self) -> None:
        verdict, blocker = rollup.compute_rollup(
            {"close_date": None},
            [
                {"requirement_code": "AAA-01", "verdict": "PASS"},
                {"requirement_code": "BBB-02", "verdict": "N/A"},
            ],
            TODAY,
        )
        self.assertEqual((verdict, blocker), ("CAN BID", ""))

    def test_no_evaluations_leaves_unset(self) -> None:
        self.assertEqual(rollup.compute_rollup({"close_date": None}, [], TODAY), (None, ""))


class ApplyTest(unittest.TestCase):
    def test_apply_writes_verdict_and_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                set_all(conn, opp_id, {"AAA-01": "PASS", "BBB-02": "FAIL"})
                verdict, blocker = rollup.apply_rollup(conn, opp_id, TODAY)
                self.assertEqual((verdict, blocker), ("CANNOT BID", "Failed: BBB-02."))
                self.assertEqual(row(conn, opp_id), ("CANNOT BID", "Failed: BBB-02."))

    def test_apply_with_no_evaluations_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(rollup.apply_rollup(conn, opp_id, TODAY), (None, ""))
                self.assertEqual(row(conn, opp_id), (None, None))

    def test_apply_unknown_opportunity_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, _ = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError):
                    rollup.apply_rollup(conn, 999, TODAY)


class EvaluateHookTest(unittest.TestCase):
    def run_hook(self, tmp: str, payload: dict) -> tuple:
        from proposal_creator import config as config_mod

        db_path, opp_id = make_db(tmp)
        doc = Path(tmp) / "sol.txt"
        doc.write_text("alpha bravo.", encoding="utf-8")
        with closing(db.connect(db_path)) as conn:
            conn.execute(
                "UPDATE opportunities SET document_path = ? WHERE id = ?",
                (str(doc), opp_id),
            )
            conn.commit()

            def fake(messages: list) -> model.ModelResult:
                return model.ModelResult(json.dumps(payload), {})

            cfg = config_mod.ModelConfig(
                provider="meta", base_url="https://x", api_key="k",
                model="m", reasoning_effort="", max_tokens=1,
                temperature=0.0, timeout=1, company_profile="p",
            )
            report = evaluate.run_evaluate(
                conn, opp_id, cfg, "profile", complete_fn=fake
            )
            verdict = db.get_opportunity(conn, opp_id)["verdict"]
        return report, verdict

    def test_evaluate_sets_overall_verdict(self) -> None:
        payload = {"evaluations": [{
            "code": "AAA-01", "verdict": "PASS",
            "quote": "alpha bravo.", "note": "",
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            report, verdict = self.run_hook(tmp, payload)
        self.assertEqual(report["verdict"], "CAN BID")
        self.assertEqual(verdict, "CAN BID")

    def test_evaluate_rolls_up_downgrades(self) -> None:
        payload = {"evaluations": [{
            "code": "AAA-01", "verdict": "PASS",
            "quote": "invented words here.", "note": "",
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            report, verdict = self.run_hook(tmp, payload)
        self.assertEqual(report["verdict"], "NEEDS INFO")
        self.assertEqual(verdict, "NEEDS INFO")


class RollupCommandTest(unittest.TestCase):
    def run_cli(self, db_path: Path, *args: str) -> tuple[int, str]:
        out = StringIO()
        with redirect_stdout(out):
            code = cli_main(["--db", str(db_path), *args])
        return code, out.getvalue()

    def test_rollup_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                set_all(conn, opp_id, {"AAA-01": "PASS", "BBB-02": "UNKNOWN"})
            code, out = self.run_cli(db_path, "rollup", str(opp_id))
            self.assertEqual(code, 0)
            self.assertIn("NEEDS INFO", out)
            self.assertIn("BBB-02", out)
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(row(conn, opp_id)[0], "NEEDS INFO")

    def test_rollup_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, _ = make_db(tmp)
            code, out = self.run_cli(db_path, "rollup", "999")
            self.assertEqual(code, 1)
            self.assertIn("No opportunity", out)


if __name__ == "__main__":
    unittest.main()
