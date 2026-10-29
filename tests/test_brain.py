"""Slice 5 tests: model config, OpenAI-compatible client, evaluate flow."""

import json
import os
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proposal_creator import config, db, evals, evaluate, model
from proposal_creator.__main__ import main as cli_main

SENTINEL = "sk-SENTINEL-123"
DOC_TEXT = "The Contractor shall maintain SOC 2 Type II compliance."
PROFILE = "Company: Demo LLC. Certifications: SOC 2 Type II."
REQS = [
    {
        "code": "CERT-01",
        "category": "certifications",
        "question": "Required certifications held?",
        "guidance": "Compare to profile.",
    }
]


def make_db(tmp: str, doc_text: str | None = DOC_TEXT) -> tuple[Path, int]:
    db_path = Path(tmp) / "test.db"
    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn:
        req_path = Path(tmp) / "reqs.json"
        req_path.write_text(json.dumps(REQS), encoding="utf-8")
        evals.load_requirements(conn, req_path)
        doc = Path(tmp) / "sol.txt"
        if doc_text is not None:
            doc.write_text(doc_text, encoding="utf-8")
        opp = {
            "source": "drop", "external_id": None, "title": "Test RFP",
            "agency": None, "url": None, "open_date": None, "close_date": None,
            "status": "new", "verdict": None, "blockers": None,
            "document_path": str(doc) if doc_text is not None else None,
            "notes": None,
        }
        opp_id = db.add_opportunity(conn, opp)
    return db_path, opp_id


def test_config(**overrides: object) -> config.ModelConfig:
    base = {
        "provider": "meta",
        "base_url": "https://api.meta.ai/v1",
        "api_key": SENTINEL,
        "model": "muse-spark-1.3",
        "reasoning_effort": "minimal",
        "max_tokens": 4000,
        "temperature": 0.0,
        "timeout": 120,
        "company_profile": "profile.md",
    }
    base.update(overrides)
    return config.ModelConfig(**base)  # type: ignore[arg-type]


class ConfigTest(unittest.TestCase):
    def test_missing_key_raises_without_leaking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / "nope.env")
            with self.assertRaises(config.ConfigError) as ctx:
                config.load(env_file=env_file, environ={})
        self.assertIn("PROPOSAL_MODEL_API_KEY", str(ctx.exception))

    def test_env_and_file_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "t.env"
            env_file.write_text(
                "# comment\n"
                f'PROPOSAL_MODEL_API_KEY="{SENTINEL}"\n'
                "PROPOSAL_MODEL_NAME=from-file\n",
                encoding="utf-8",
            )
            cfg = config.load(
                env_file=str(env_file),
                environ={"PROPOSAL_MODEL_NAME": "from-env"},
            )
            self.assertEqual(cfg.api_key, SENTINEL)
            self.assertEqual(cfg.model, "from-env")  # env beats file
            self.assertEqual(cfg.base_url, "https://api.meta.ai/v1")  # default

    def test_invalid_numbers_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_file = str(Path(tmp) / "nope.env")
            with self.assertRaises(config.ConfigError):
                config.load(
                    env_file=env_file,
                    environ={
                        "PROPOSAL_MODEL_API_KEY": SENTINEL,
                        "PROPOSAL_MODEL_MAX_TOKENS": "lots",
                    },
                )

    def test_stub_provider_needs_no_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(
                env_file=str(Path(tmp) / "nope.env"),
                environ={"PROPOSAL_MODEL_PROVIDER": "stub"},
            )
            self.assertEqual(cfg.provider, "stub")

    def test_describe_excludes_key(self) -> None:
        self.assertNotIn(SENTINEL, test_config().describe())


class ClientTest(unittest.TestCase):
    def test_request_shape(self) -> None:
        seen: dict = {}

        def fake(url: str, headers: dict, body: dict) -> dict:
            seen.update(url=url, headers=headers, body=body)
            return {
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }

        result = model.complete(
            test_config(), [{"role": "user", "content": "hi"}], transport=fake
        )
        self.assertEqual(seen["url"], "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(seen["headers"]["Authorization"], f"Bearer {SENTINEL}")
        self.assertEqual(seen["body"]["model"], "muse-spark-1.3")
        self.assertEqual(seen["body"]["reasoning_effort"], "minimal")
        self.assertEqual(seen["body"]["max_tokens"], 4000)
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(result.usage["prompt_tokens"], 5)

    def test_blank_reasoning_effort_omitted(self) -> None:
        seen: dict = {}

        def fake(url: str, headers: dict, body: dict) -> dict:
            seen["body"] = body
            return {"choices": [{"message": {"content": "{}"}}]}

        model.complete(
            test_config(reasoning_effort=""), [{"role": "user", "content": "hi"}],
            transport=fake,
        )
        self.assertNotIn("reasoning_effort", seen["body"])

    def test_empty_content_suggests_more_tokens(self) -> None:
        def fake(url: str, headers: dict, body: dict) -> dict:
            return {"choices": [{"message": {"content": ""}}]}

        with self.assertRaises(model.ModelError) as ctx:
            model.complete(
                test_config(), [{"role": "user", "content": "hi"}], transport=fake
            )
        self.assertIn("max_tokens", str(ctx.exception).lower())

    def test_stub_provider_short_circuits_in_run_evaluate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            calls: list = []

            def fake(messages: list) -> model.ModelResult:
                calls.append(messages)
                raise AssertionError("stub must not call the model")

            with closing(db.connect(db_path)) as conn:
                report = evaluate.run_evaluate(
                    conn, opp_id, test_config(provider="stub"), PROFILE,
                    complete_fn=fake,
                )
                rows = evals.list_evaluations(conn, opp_id)
        self.assertEqual(calls, [])
        self.assertEqual(rows[0]["verdict"], "UNKNOWN")
        self.assertIn("stub", rows[0]["note"])
        self.assertEqual(len(report["stored"]), 1)


class PromptTest(unittest.TestCase):
    def test_messages_carry_everything_and_warn_untrusted(self) -> None:
        messages = evaluate.build_messages(PROFILE, REQS, DOC_TEXT)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("verbatim", messages[0]["content"].lower())
        self.assertIn("untrusted", messages[0]["content"].lower())
        for needle in ("Demo LLC", "CERT-01", "SOC 2 Type II compliance"):
            self.assertIn(needle, messages[1]["content"])

    def test_sample_solicitation_exists(self) -> None:
        sample = Path("samples/demo-it-support-rfp.txt")
        self.assertTrue(sample.exists())
        self.assertGreater(len(sample.read_text(encoding="utf-8")), 200)


class RunEvaluateTest(unittest.TestCase):
    def run_with(
        self, tmp: str, payload: dict, only: list | None = None
    ) -> tuple[dict, list]:
        db_path, opp_id = make_db(tmp)
        calls: list = []

        def fake(messages: list) -> model.ModelResult:
            calls.append(messages)
            return model.ModelResult(json.dumps(payload), {"total_tokens": 9})

        with closing(db.connect(db_path)) as conn:
            report = evaluate.run_evaluate(
                conn, opp_id, test_config(), PROFILE, complete_fn=fake, only=only
            )
            rows = evals.list_evaluations(conn, opp_id)
        return report, rows

    def test_verbatim_pass_stored_verified(self) -> None:
        payload = {"evaluations": [{
            "code": "CERT-01", "verdict": "PASS",
            "quote": "maintain SOC 2 Type II compliance.",
            "note": "Profile lists SOC 2.",
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            report, rows = self.run_with(tmp, payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["verdict"], "PASS")
        self.assertEqual(rows[0]["verified"], 1)
        self.assertTrue(rows[0]["evaluated_by"].startswith("model:"))
        self.assertEqual(report["usage"]["total_tokens"], 9)

    def test_invented_quote_downgraded_to_unknown(self) -> None:
        payload = {"evaluations": [{
            "code": "CERT-01", "verdict": "PASS",
            "quote": "CMMC Level 2 required.", "note": "",
        }]}
        with tempfile.TemporaryDirectory() as tmp:
            report, rows = self.run_with(tmp, payload)
        self.assertEqual(rows[0]["verdict"], "UNKNOWN")
        self.assertIsNone(rows[0]["quote"])
        self.assertIn("verbatim", rows[0]["note"])
        self.assertEqual(len(report["downgraded"]), 1)

    def test_unknown_code_skipped_and_bad_verdict_coerced(self) -> None:
        payload = {"evaluations": [
            {"code": "NOPE-99", "verdict": "PASS", "quote": "x"},
            {"code": "CERT-01", "verdict": "MAYBE", "quote": ""},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            report, rows = self.run_with(tmp, payload)
        self.assertEqual([r["requirement_code"] for r in rows], ["CERT-01"])
        self.assertEqual(rows[0]["verdict"], "UNKNOWN")
        self.assertIn("MAYBE", rows[0]["note"])
        self.assertEqual(report["skipped"], ["NOPE-99"])

    def test_only_limits_requirements_sent(self) -> None:
        payload = {"evaluations": []}
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            calls: list = []

            def fake(messages: list) -> model.ModelResult:
                calls.append(messages)
                return model.ModelResult(json.dumps(payload), {})

            with closing(db.connect(db_path)) as conn:
                evaluate.run_evaluate(
                    conn, opp_id, test_config(), PROFILE,
                    complete_fn=fake, only=["CERT-01"],
                )
        self.assertIn("CERT-01", calls[0][1]["content"])


class EvaluateCommandTest(unittest.TestCase):
    def run_cli(self, environ: dict, *args: str) -> tuple[int, str]:
        out = StringIO()
        with patch.dict(os.environ, environ, clear=False):
            with redirect_stdout(out):
                code = cli_main(list(args))
        return code, out.getvalue()

    def test_stub_evaluate_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            profile = Path(tmp) / "profile.md"
            profile.write_text(PROFILE, encoding="utf-8")
            env = {
                "PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env"),
                "PROPOSAL_MODEL_PROVIDER": "stub",
                "PROPOSAL_COMPANY_PROFILE": str(profile),
            }
            code, out = self.run_cli(
                env, "--db", str(db_path), "evaluate", str(opp_id)
            )
            self.assertEqual(code, 0)
            self.assertIn("CERT-01", out)
            with closing(db.connect(db_path)) as conn:
                rows = evals.list_evaluations(conn, opp_id)
            self.assertEqual(rows[0]["verdict"], "UNKNOWN")

    def test_evaluate_needs_readable_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp, doc_text=None)
            env = {
                "PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env"),
                "PROPOSAL_MODEL_PROVIDER": "stub",
                "PROPOSAL_COMPANY_PROFILE": str(Path(tmp) / "p.md"),
            }
            Path(tmp, "p.md").write_text(PROFILE, encoding="utf-8")
            code, out = self.run_cli(env, "--db", str(db_path), "evaluate", str(opp_id))
            self.assertEqual(code, 1)
            self.assertIn("readable document", out)

    def test_dry_run_prints_without_calling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            profile = Path(tmp) / "profile.md"
            profile.write_text(PROFILE, encoding="utf-8")
            env = {
                "PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env"),
                "PROPOSAL_MODEL_PROVIDER": "stub",
                "PROPOSAL_COMPANY_PROFILE": str(profile),
            }
            code, out = self.run_cli(
                env, "--db", str(db_path), "evaluate", str(opp_id), "--dry-run"
            )
            self.assertEqual(code, 0)
            self.assertIn("CERT-01", out)
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(evals.list_evaluations(conn, opp_id), [])


if __name__ == "__main__":
    unittest.main()
