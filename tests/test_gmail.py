"""Gmail + intake-email tests. Offline: HTTP is faked, loopback is real."""

import base64
import os
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from helpers import FakeSocket
from proposal_creator import db, gmail, notices
from proposal_creator.__main__ import main as cli_main

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def gmail_config(tmp: str, **overrides) -> gmail.GmailConfig:
    base = {
        "client_id": "test-client",
        "client_secret": "test-secret",
        "token_path": Path(tmp) / "token.json",
    }
    base.update(overrides)
    return gmail.GmailConfig(**base)


class GmailConfigTest(unittest.TestCase):
    def test_missing_credentials_explain_without_leaking(self) -> None:
        with self.assertRaises(gmail.GmailError) as ctx:
            gmail.load_config(env_file="nope.env", environ={})
        self.assertIn("intake-email --auth", str(ctx.exception))

    def test_env_and_file_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "PROPOSAL_GMAIL_CLIENT_ID=file-id\n"
                "PROPOSAL_GMAIL_CLIENT_SECRET=file-secret\n",
                encoding="utf-8",
            )
            config = gmail.load_config(env_file=str(env), environ={})
            self.assertEqual(config.client_id, "file-id")
            config = gmail.load_config(
                env_file=str(env),
                environ={"PROPOSAL_GMAIL_CLIENT_ID": "env-id",
                         "PROPOSAL_GMAIL_CLIENT_SECRET": "env-secret"},
            )
            self.assertEqual(config.client_id, "env-id")  # env wins


class OAuthFlowTest(unittest.TestCase):
    def test_auth_url_is_readonly_offline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            url = gmail.auth_url(gmail_config(tmp), 8765, "state-1")
            for needle in (
                "accounts.google.com", "client_id=test-client",
                "127.0.0.1%3A8765", "gmail.readonly",
                "access_type=offline", "state=state-1",
            ):
                self.assertIn(needle, url)
            self.assertNotIn("test-secret", url)

    def test_exchange_code_posts_grant(self) -> None:
        seen: dict = {}

        def fake_post(url: str, fields: dict) -> dict:
            seen.update(fields)
            self.assertIn("oauth2.googleapis.com", url)
            return {"refresh_token": "refresh-1"}

        with tempfile.TemporaryDirectory() as tmp:
            token = gmail.exchange_code(
                gmail_config(tmp), "code-1", 8765, post=fake_post
            )
            self.assertEqual(token, "refresh-1")
            self.assertEqual(seen["grant_type"], "authorization_code")
            self.assertEqual(seen["code"], "code-1")
            self.assertIn("127.0.0.1:8765", seen["redirect_uri"])

    def test_exchange_without_refresh_token_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(gmail.OAuthError):
                gmail.exchange_code(
                    gmail_config(tmp), "code-1", 8765,
                    post=lambda u, f: {"access_token": "x"},
                )

    def test_refresh_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = gmail_config(tmp)
            gmail.save_refresh_token(config.token_path, "refresh-1")
            self.assertEqual(
                gmail.load_refresh_token(config.token_path), "refresh-1"
            )

            def fake_post(url: str, fields: dict) -> dict:
                self.assertEqual(fields["grant_type"], "refresh_token")
                self.assertEqual(fields["refresh_token"], "refresh-1")
                return {"access_token": "access-1"}

            self.assertEqual(
                gmail.fresh_access_token(config, post=fake_post), "access-1"
            )

    def test_missing_token_points_to_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(gmail.GmailError) as ctx:
                gmail.load_refresh_token(Path(tmp) / "missing.json")
            self.assertIn("--auth", str(ctx.exception))

    def test_callback_handler_captures_code(self) -> None:
        captured: dict = {}
        handler_cls = type(
            "BoundCallback", (gmail.CallbackHandler,), {"captured": captured}
        )
        fake = FakeSocket(
            b"GET /callback?code=code-9&state=state-9 HTTP/1.0\r\n"
            b"Host: x\r\n\r\n"
        )
        handler_cls(fake, ("127.0.0.1", 0), None)
        self.assertEqual(captured["code"], "code-9")
        self.assertEqual(
            gmail.validate_callback(captured, "state-9"), "code-9"
        )
        self.assertIn("close this tab", fake.sent.getvalue().decode("utf-8"))

    def test_state_mismatch_refused(self) -> None:
        with self.assertRaises(gmail.OAuthError) as ctx:
            gmail.validate_callback(
                {"code": "x", "state": "forged", "error": ""}, "expected"
            )
        self.assertIn("State mismatch", str(ctx.exception))

    def test_consent_error_and_timeout_rejected(self) -> None:
        with self.assertRaises(gmail.OAuthError) as ctx:
            gmail.validate_callback(
                {"code": "", "state": "s", "error": "access_denied"}, "s"
            )
        self.assertIn("access_denied", str(ctx.exception))
        with self.assertRaises(gmail.OAuthError):
            gmail.validate_callback({"code": "", "state": "s", "error": ""}, "s")


class GmailApiTest(unittest.TestCase):
    def test_list_and_raw_with_fake_transport(self) -> None:
        raw = (SAMPLES / "mfmp-RFP-16733.eml").read_bytes()

        def fake_get(url: str, token: str) -> dict:
            self.assertEqual(token, "access-1")
            if url.endswith("/messages?q=") or "/messages?" in url:
                return {"messages": [{"id": "abc"}, {"id": "def"}]}
            return {"raw": base64.urlsafe_b64encode(raw).decode("ascii")}

        self.assertEqual(
            gmail.list_message_ids("access-1", get=fake_get), ["abc", "def"]
        )
        body = gmail.get_raw("access-1", "abc", get=fake_get)
        notice = notices.parse_eml(body)
        self.assertEqual(notice["portal_number"], "RFP-16733")


class ResolveLinkTest(unittest.TestCase):
    def test_resolved_portal_url_sets_project_id(self) -> None:
        notice = {
            "source": "opengov", "kind": "new",
            "url": "https://url856.opengov.com/ls/click?upn=tracking",
        }
        out = notices.resolve_notice_link(
            notice,
            opener=lambda u: (
                "https://procurement.opengov.com/portal/copbfl/projects/309982"
            ),
        )
        self.assertEqual(out["portal_number"], "309982")
        self.assertIn("/projects/309982", out["url"])

    def test_failed_resolve_keeps_original(self) -> None:
        notice = {"source": "opengov", "kind": "new", "url": "https://t.example/x"}

        def boom(url: str) -> str:
            raise ConnectionError("offline")

        out = notices.resolve_notice_link(notice, opener=boom)
        self.assertEqual(out["url"], "https://t.example/x")
        self.assertNotIn("portal_number", out)

    def test_mfmp_links_untouched(self) -> None:
        notice = {"source": "mfmp", "kind": "new", "url": "https://vip.example/1"}
        out = notices.resolve_notice_link(
            notice, opener=lambda u: (_ for _ in ()).throw(AssertionError())
        )
        self.assertEqual(out["url"], "https://vip.example/1")


class IntakeEmailCommandTest(unittest.TestCase):
    def run_cli(self, environ: dict, *args: str) -> tuple[int, str]:
        out = StringIO()
        with patch.dict(os.environ, environ, clear=False):
            with redirect_stdout(out):
                code = cli_main(list(args))
        return code, out.getvalue()

    def test_from_file_creates_then_dedups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            env = {"PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env")}
            eml = str(SAMPLES / "mfmp-RFP-16733.eml")
            code, out = self.run_cli(
                env, "--db", str(db_path), "intake-email", "--from-file", eml
            )
            self.assertEqual(code, 0)
            self.assertIn("created", out)
            code, out = self.run_cli(
                env, "--db", str(db_path), "intake-email", "--from-file", eml
            )
            self.assertEqual(code, 0)
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(len(db.list_opportunities(conn)), 1)

    def test_from_file_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            env = {"PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env")}
            code, out = self.run_cli(
                env, "--db", str(db_path), "intake-email", "--from-file",
                str(SAMPLES / "opengov-pompano-voip.eml"), "--dry-run",
            )
            self.assertEqual(code, 0)
            self.assertIn("would process [opengov]", out)
            with closing(db.connect(db_path)) as conn:
                self.assertEqual(db.list_opportunities(conn), [])

    def test_live_without_config_explains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PROPOSAL_ENV_FILE": str(Path(tmp) / "empty.env")}
            code, out = self.run_cli(
                env, "--db", str(Path(tmp) / "t.db"), "intake-email"
            )
            self.assertEqual(code, 1)
            self.assertIn("PROPOSAL_GMAIL_CLIENT_ID", out)


if __name__ == "__main__":
    unittest.main()
