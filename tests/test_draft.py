"""Draft step tests: prompt honesty rules, save/link flow, checklist shape.

Structural only — every model call is faked. The live run (`draft 9`)
needs the real API and the owner's environment.
"""

import json
import os
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from proposal_creator import config, db, draft, evals, evaluate, model, prompts, web
from proposal_creator.__main__ import main as cli_main

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
    {
        "code": "FIN-01",
        "category": "financial",
        "question": "Financial requirements met?",
        "guidance": "Compare to profile.",
    },
]
PAYLOAD = {
    "draft_markdown": "# Draft response\n\nWe hold SOC 2 Type II. [NEEDS ME] pricing.",
    "commitments": [
        {"claim": "We hold SOC 2 Type II.", "status": "supported"},
        {"claim": "Fixed-price bid of $X.", "status": "needs_me"},
    ],
}


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
        if doc_text is not None:
            evals.set_evaluation(
                conn, opp_id, "CERT-01", "PASS",
                quote="maintain SOC 2 Type II compliance.",
                evaluated_by="human",
            )
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


class DraftPromptTest(unittest.TestCase):
    """The honesty rules must be present in the source-of-truth prompt."""

    def test_honesty_rules_present(self) -> None:
        prompt = prompts.get_prompt("Draft")
        for needle in (
            "FULL CREATIVE LATITUDE",
            "structure", "tone", "wording", "section organization",
            "STRICT",
            "Never invent a company fact",
            "[NEEDS ME]",
            "pricing", "staffing", "technical approach",
            "CONSISTENT WITH THE EVALUATION",
            "FAIL", "UNKNOWN",
            "THIS solicitation", "generic template",
            "DRAFT", "never submit",
            "draft_markdown", "commitments",
            "untrusted",
        ):
            self.assertIn(needle, prompt)

    def test_code_uses_prompts_file_verbatim(self) -> None:
        self.assertEqual(draft.SYSTEM_PROMPT, prompts.get_prompt("Draft"))
        self.assertEqual(evaluate.SYSTEM_PROMPT, prompts.get_prompt("Eligibility"))

    def test_unknown_prompt_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            prompts.get_prompt("No-Such-Prompt")


class DraftMessagesTest(unittest.TestCase):
    def test_messages_carry_profile_evals_and_doc(self) -> None:
        checklist = [
            {"code": "CERT-01", "question": "Certs?", "verdict": "PASS",
             "note": None},
            {"code": "FIN-01", "question": "Money?", "verdict": None,
             "note": None},
        ]
        messages = draft.build_messages(PROFILE, checklist, DOC_TEXT)
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertEqual(messages[0]["content"], draft.SYSTEM_PROMPT)
        user = messages[1]["content"]
        for needle in ("Demo LLC", "CERT-01", "PASS", "FIN-01",
                       "unevaluated", "SOC 2 Type II compliance", "untrusted"):
            self.assertIn(needle, user)


class RunDraftTest(unittest.TestCase):
    def run_with(self, tmp: str, payload: dict) -> tuple[dict, Path]:
        db_path, opp_id = make_db(tmp)
        drafts_dir = Path(tmp) / "drafts"

        def fake(messages: list) -> model.ModelResult:
            return model.ModelResult(json.dumps(payload), {"total_tokens": 9})

        with closing(db.connect(db_path)) as conn:
            report = draft.run_draft(
                conn, opp_id, test_config(), PROFILE,
                complete_fn=fake, drafts_dir=drafts_dir,
            )
        return report, drafts_dir

    def test_outputs_generated_saved_and_shaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report, drafts_dir = self.run_with(tmp, PAYLOAD)
            draft_file = drafts_dir / "opp-1-draft.md"
            comm_file = drafts_dir / "opp-1-commitments.md"
            self.assertEqual(report["draft_path"], str(draft_file))
            self.assertEqual(report["commitments_path"], str(comm_file))
            self.assertTrue(draft_file.exists())
            self.assertTrue(comm_file.exists())
            self.assertIn("We hold SOC 2 Type II", draft_file.read_text())
            body = comm_file.read_text(encoding="utf-8")
            self.assertIn("We hold SOC 2 Type II", body)
            self.assertIn("supported by the company profile", body)
            self.assertIn("[NEEDS ME]", body)
            self.assertIn("confirm we can deliver", body)
            self.assertEqual(
                report["commitments"],
                [
                    {"claim": "We hold SOC 2 Type II.", "status": "supported"},
                    {"claim": "Fixed-price bid of $X.", "status": "needs_me"},
                ],
            )
            self.assertEqual(report["usage"]["total_tokens"], 9)

    def test_unknown_status_fails_closed_to_needs_me(self) -> None:
        payload = {
            "draft_markdown": "Draft.",
            "commitments": [
                {"claim": "We can do anything.", "status": "bogus"},
                {"claim": "Trust us."},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report, drafts_dir = self.run_with(tmp, payload)
            self.assertEqual(
                [c["status"] for c in report["commitments"]],
                ["needs_me", "needs_me"],
            )
            body = (drafts_dir / "opp-1-commitments.md").read_text()
            self.assertNotIn("supported by the company profile", body)

    def test_bad_responses_rejected(self) -> None:
        for bad in (
            "not json at all",
            json.dumps({"draft_markdown": "x"}),  # no commitments
            json.dumps({"draft_markdown": "  ", "commitments": []}),
            json.dumps({"draft_markdown": "x", "commitments": "nope"}),
            json.dumps({"draft_markdown": "x",
                        "commitments": [{"status": "supported"}]}),  # no claim
        ):
            with tempfile.TemporaryDirectory() as tmp:
                db_path, opp_id = make_db(tmp)

                def fake(messages: list, content: str = bad) -> model.ModelResult:
                    return model.ModelResult(content, {})

                with closing(db.connect(db_path)) as conn:
                    with self.assertRaises(ValueError):
                        draft.run_draft(
                            conn, opp_id, test_config(), PROFILE,
                            complete_fn=fake, drafts_dir=Path(tmp) / "d",
                        )

    def test_requires_stored_evaluations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                req_path = Path(tmp) / "reqs.json"
                req_path.write_text(json.dumps(REQS), encoding="utf-8")
                evals.load_requirements(conn, req_path)
                doc = Path(tmp) / "sol.txt"
                doc.write_text(DOC_TEXT, encoding="utf-8")
                opp_id = db.add_opportunity(conn, {
                    "source": "drop", "external_id": None, "title": "T",
                    "agency": None, "url": None, "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": str(doc), "notes": None,
                })
                with self.assertRaises(ValueError) as ctx:
                    draft.run_draft(
                        conn, opp_id, test_config(), PROFILE,
                        complete_fn=lambda m: model.ModelResult("{}", {}),
                        drafts_dir=Path(tmp) / "d",
                    )
            self.assertIn("evaluate", str(ctx.exception))

    def test_requires_readable_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp, doc_text=None)
            with closing(db.connect(db_path)) as conn:
                with self.assertRaises(ValueError) as ctx:
                    draft.run_draft(
                        conn, opp_id, test_config(), PROFILE,
                        complete_fn=lambda m: model.ModelResult("{}", {}),
                        drafts_dir=Path(tmp) / "d",
                    )
            self.assertIn("readable document", str(ctx.exception))

    def test_stub_writes_placeholders_without_calling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            drafts_dir = Path(tmp) / "drafts"

            def fake(messages: list) -> model.ModelResult:
                raise AssertionError("stub must not call the model")

            with closing(db.connect(db_path)) as conn:
                report = draft.run_draft(
                    conn, opp_id, test_config(provider="stub"), PROFILE,
                    complete_fn=fake, drafts_dir=drafts_dir,
                )
            self.assertTrue(Path(report["draft_path"]).exists())
            self.assertTrue(Path(report["commitments_path"]).exists())
            self.assertIn("stub", Path(report["draft_path"]).read_text())
            self.assertEqual(report["commitments"][0]["status"], "needs_me")
            self.assertEqual(report["usage"], {})

    def test_draft_call_gets_draft_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            seen: dict = {}

            def fake_complete(cfg: config.ModelConfig, messages: list,
                              **kwargs: object) -> model.ModelResult:
                seen["max_tokens"] = cfg.max_tokens
                return model.ModelResult(json.dumps(PAYLOAD), {})

            with patch.object(draft.model_api, "complete", fake_complete):
                with closing(db.connect(db_path)) as conn:
                    draft.run_draft(
                        conn, opp_id, test_config(), PROFILE,
                        drafts_dir=Path(tmp) / "d",
                    )
            self.assertEqual(seen["max_tokens"], 12000)

    def test_draft_budget_configurable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = config.load(
                env_file=str(Path(tmp) / "nope.env"),
                environ={
                    "PROPOSAL_MODEL_API_KEY": SENTINEL,
                    "PROPOSAL_MODEL_DRAFT_MAX_TOKENS": "20000",
                },
            )
            self.assertEqual(cfg.draft_max_tokens, 20000)
            self.assertEqual(cfg.max_tokens, 4000)  # evaluate budget untouched
            self.assertNotIn(SENTINEL, cfg.describe())


class DraftPageTest(unittest.TestCase):
    def test_detail_page_shows_draft_and_checklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            drafts_dir = Path(tmp) / "drafts"
            drafts_dir.mkdir()
            (drafts_dir / f"opp-{opp_id}-draft.md").write_text(
                "# Draft\n\nOur bid.", encoding="utf-8")
            (drafts_dir / f"opp-{opp_id}-commitments.md").write_text(
                "- ([NEEDS ME]) pricing", encoding="utf-8")
            with closing(db.connect(db_path)) as conn:
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                checklist = evals.list_checklist(conn, opp_id)
                page = web.render_detail(row, checklist, drafts_dir=drafts_dir)
            self.assertIn("Our bid.", page)
            self.assertIn("[NEEDS ME]", page)
            self.assertIn(f"opp-{opp_id}-draft.md", page)
            self.assertIn(f"opp-{opp_id}-commitments.md", page)

    def test_detail_page_without_draft_points_to_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            with closing(db.connect(db_path)) as conn:
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                checklist = evals.list_checklist(conn, opp_id)
                page = web.render_detail(
                    row, checklist, drafts_dir=Path(tmp) / "empty")
            self.assertIn("No draft yet", page)
            self.assertIn(f"proposal-creator draft {opp_id}", page)

    def test_draft_content_is_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            drafts_dir = Path(tmp) / "drafts"
            drafts_dir.mkdir()
            (drafts_dir / f"opp-{opp_id}-draft.md").write_text(
                "<script>alert(1)</script>", encoding="utf-8")
            with closing(db.connect(db_path)) as conn:
                row = db.get_opportunity(conn, opp_id)
                assert row is not None
                checklist = evals.list_checklist(conn, opp_id)
                page = web.render_detail(row, checklist, drafts_dir=drafts_dir)
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;", page)


class DraftCommandTest(unittest.TestCase):
    def run_cli(self, environ: dict, *args: str) -> tuple[int, str]:
        out = StringIO()
        with patch.dict(os.environ, environ, clear=False):
            with redirect_stdout(out):
                code = cli_main(list(args))
        return code, out.getvalue()

    def test_stub_draft_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            profile = Path(tmp) / "profile.md"
            profile.write_text(PROFILE, encoding="utf-8")
            env = {
                "PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env"),
                "PROPOSAL_MODEL_PROVIDER": "stub",
                "PROPOSAL_COMPANY_PROFILE": str(profile),
            }
            with patch.object(
                draft, "DEFAULT_DRAFTS_DIR", Path(tmp) / "drafts"
            ):
                code, out = self.run_cli(
                    env, "--db", str(db_path), "draft", str(opp_id)
                )
            self.assertEqual(code, 0)
            self.assertIn("draft:", out)
            self.assertIn("checklist:", out)
            self.assertTrue((Path(tmp) / "drafts").exists())

    def test_draft_without_evals_explains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            db.init_db(db_path)
            with closing(db.connect(db_path)) as conn:
                req_path = Path(tmp) / "reqs.json"
                req_path.write_text(json.dumps(REQS), encoding="utf-8")
                evals.load_requirements(conn, req_path)
                doc = Path(tmp) / "sol.txt"
                doc.write_text(DOC_TEXT, encoding="utf-8")
                opp_id = db.add_opportunity(conn, {
                    "source": "drop", "external_id": None, "title": "T",
                    "agency": None, "url": None, "open_date": None,
                    "close_date": None, "status": "new", "verdict": None,
                    "blockers": None, "document_path": str(doc), "notes": None,
                })
            profile = Path(tmp) / "profile.md"
            profile.write_text(PROFILE, encoding="utf-8")
            env = {
                "PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env"),
                "PROPOSAL_MODEL_PROVIDER": "stub",
                "PROPOSAL_COMPANY_PROFILE": str(profile),
            }
            code, out = self.run_cli(
                env, "--db", str(db_path), "draft", str(opp_id))
            self.assertEqual(code, 1)
            self.assertIn("evaluate", out)

    def test_dry_run_prints_without_calling_or_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path, opp_id = make_db(tmp)
            profile = Path(tmp) / "profile.md"
            profile.write_text(PROFILE, encoding="utf-8")
            env = {
                "PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env"),
                "PROPOSAL_MODEL_PROVIDER": "stub",
                "PROPOSAL_COMPANY_PROFILE": str(profile),
            }
            drafts_dir = Path(tmp) / "drafts"
            with patch.object(draft, "DEFAULT_DRAFTS_DIR", drafts_dir):
                code, out = self.run_cli(
                    env, "--db", str(db_path), "draft", str(opp_id), "--dry-run"
                )
            self.assertEqual(code, 0)
            self.assertIn("CERT-01", out)
            self.assertFalse(drafts_dir.exists())


if __name__ == "__main__":
    unittest.main()
