"""Gmail access: read-only notice fetching over the Gmail REST API.

Stdlib only (`urllib`), like the model client. OAuth is the installed-app
flow: `intake-email --auth` prints a consent URL, the browser returns to a
loopback listener with a code, the code is exchanged for tokens, and only
the refresh token is kept (in a gitignored file, never the repo). The
`gmail.readonly` scope makes send/reply/delete/move/mark impossible.

Network calls go through small injectable functions so tests never touch
the network.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly"

DEFAULT_QUERY = (
    "{from:MFMP.NoReply@dms.myflorida.com"
    " from:procurement-notifications@opengov.com}"
)
DEFAULT_TOKEN_PATH = Path("data/gmail_token.json")


class GmailError(Exception):
    pass


class OAuthError(GmailError):
    pass


@dataclass(frozen=True)
class GmailConfig:
    client_id: str
    client_secret: str
    token_path: Path = DEFAULT_TOKEN_PATH


def _read_env_file(path: str) -> dict:
    """Same minimal dotenv reader as config.py (kept in sync by hand)."""
    values: dict = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
            raw = raw[1:-1]
        values[key.strip()] = raw
    return values


def load_config(
    client_id: str | None = None,
    client_secret: str | None = None,
    token_path: str | Path | None = None,
    env_file: str | None = None,
    environ: dict | None = None,
) -> GmailConfig:
    """Credentials from args, environment, or `.env`; never printed."""
    environ = os.environ if environ is None else environ
    if env_file is None:
        env_file = environ.get("PROPOSAL_ENV_FILE", ".env")
    file_values = _read_env_file(env_file)

    def get(name: str) -> str:
        for source in (environ, file_values):
            if name in source and str(source[name]).strip():
                return str(source[name])
        return ""

    client_id = client_id or get("PROPOSAL_GMAIL_CLIENT_ID")
    client_secret = client_secret or get("PROPOSAL_GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise GmailError(
            "Gmail OAuth client not configured. Set PROPOSAL_GMAIL_CLIENT_ID"
            " and PROPOSAL_GMAIL_CLIENT_SECRET (see .env.example), then run"
            " `intake-email --auth`."
        )
    if token_path is None:
        token_path = get("PROPOSAL_GMAIL_TOKEN") or str(DEFAULT_TOKEN_PATH)
    return GmailConfig(
        client_id=client_id,
        client_secret=client_secret,
        token_path=Path(token_path),
    )


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def auth_url(config: GmailConfig, port: int, state: str) -> str:
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri(port),
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    return f"{AUTH_URL}?{params}"


def save_refresh_token(path: Path | str, refresh_token: str) -> Path:
    """Store the refresh token; file ends up readable only by the owner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"refresh_token": refresh_token}), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best effort (non-POSIX filesystems)
    return path


def load_refresh_token(path: Path | str) -> str:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        token = data.get("refresh_token", "")
    except (OSError, ValueError):
        token = ""
    if not token:
        raise GmailError(
            f"No Gmail refresh token at {path}."
            " Run `intake-email --auth` first."
        )
    return token


def _post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OAuthError(f"Token request failed: HTTP {exc.code}.") from None
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise OAuthError(f"Token request failed: {exc}.") from None


def _get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GmailError(f"Gmail request failed: HTTP {exc.code}.") from None
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise GmailError(f"Gmail request failed: {exc}.") from None


def exchange_code(
    config: GmailConfig,
    code: str,
    port: int,
    post: Callable[[str, dict], dict] | None = None,
) -> str:
    """Trade an auth code for a refresh token (returned, not stored)."""
    post = post or _post_form
    payload = post(TOKEN_URL, {
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": redirect_uri(port),
        "grant_type": "authorization_code",
    })
    if not payload.get("refresh_token"):
        raise OAuthError("Token response had no refresh token.")
    return str(payload["refresh_token"])


def fresh_access_token(
    config: GmailConfig,
    post: Callable[[str, dict], dict] | None = None,
) -> str:
    """Mint a short-lived access token from the stored refresh token."""
    post = post or _post_form
    payload = post(TOKEN_URL, {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": load_refresh_token(config.token_path),
        "grant_type": "refresh_token",
    })
    if not payload.get("access_token"):
        raise OAuthError("Refresh response had no access token.")
    return str(payload["access_token"])


def parse_callback(path: str) -> dict:
    """Pull code/state/error out of a loopback callback request path."""
    query = urllib.parse.urlparse(path).query
    fields = urllib.parse.parse_qs(query)
    return {
        "code": (fields.get("code") or [""])[0],
        "state": (fields.get("state") or [""])[0],
        "error": (fields.get("error") or [""])[0],
    }


def validate_callback(captured: dict, state: str) -> str:
    """Return the code, rejecting errors, timeouts, and forged states."""
    if captured.get("error"):
        raise OAuthError(f"Consent failed: {captured['error']}.")
    if not captured.get("code"):
        raise OAuthError("No code received (timed out or tab closed).")
    if captured.get("state") != state:
        raise OAuthError("State mismatch — refusing the code.")
    return str(captured["code"])


class CallbackHandler(BaseHTTPRequestHandler):
    """Loopback listener endpoint (one request per consent flow)."""

    captured: dict = {}

    def do_GET(self) -> None:  # noqa: N802 (stdlib handler naming)
        self.captured.update(parse_callback(self.path))
        body = (
            b"<html><body><p>Signed in. You can close this tab and"
            b" return to the terminal.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def start_callback_server(port: int = 0) -> HTTPServer:
    """Bind the loopback listener (port 0 picks an ephemeral port)."""
    handler = type("BoundCallback", (CallbackHandler,), {"captured": {}})
    return HTTPServer(("127.0.0.1", port), handler)


def wait_for_code(
    server: HTTPServer, state: str, timeout: int = 300
) -> str:
    """Serve one loopback callback; return the code (validates state)."""
    server.timeout = timeout
    server.handle_request()
    server.server_close()
    return validate_callback(server.RequestHandlerClass.captured, state)


def list_message_ids(
    token: str,
    query: str = DEFAULT_QUERY,
    get: Callable[[str, str], dict] | None = None,
) -> list[str]:
    get = get or _get_json
    url = f"{GMAIL_API}/messages?{urllib.parse.urlencode({'q': query})}"
    payload = get(url, token)
    return [m["id"] for m in payload.get("messages", []) if m.get("id")]


def get_raw(
    token: str,
    gmail_id: str,
    get: Callable[[str, str], dict] | None = None,
) -> bytes:
    """Full RFC822 bytes of one message (feeds the same parser as .eml)."""
    get = get or _get_json
    url = f"{GMAIL_API}/messages/{gmail_id}?{urllib.parse.urlencode({'format': 'raw'})}"
    payload = get(url, token)
    try:
        return base64.urlsafe_b64decode(payload["raw"])
    except (KeyError, ValueError) as exc:
        raise GmailError(f"Message {gmail_id} had no usable body.") from exc
