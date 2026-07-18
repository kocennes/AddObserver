"""Defense-in-depth: secret-carrying objects must never print their raw value.

Faz 2.2 (todo.md) asks for a regression suite proving OAuth/MCP/HTTP/adapter
code paths never leak a token/secret into a log or trace. This repo has no
structured application logger yet (Faz 9.1 is still open), so the concrete,
testable risk today is different: several plain ``@dataclass`` objects
(``Settings``, ``GoogleAdsCredentials``, ``GoogleTokenResult``,
``AuthorizationCode``/``AccessToken``/``RefreshToken``, ``WebSession``,
``WebSessionIssued``) carry a real secret as a field and are threaded through
almost every request path. Python's default dataclass ``__repr__``/``__str__``
prints every field verbatim -- so the day any of these objects is passed to a
future ``logger.debug(...)``, an f-string, or ends up as a local variable in
an unhandled exception's traceback, the raw secret would be printed in full.

Each class below was given ``field(repr=False)`` on its secret field(s)
(docs/SECURITY.md -- "Token, secret ... loglanmaz"). These tests pin that
behaviour: construct the object with a distinctive marker in place of the
secret, then assert the marker never appears in ``repr()``/``str()`` while a
non-secret field remains visible (proving the object is still debuggable,
not just blanked out).
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.reporting import GoogleAdsCredentials
from backend.src.auth.domain import AccessToken, AuthorizationCode, RefreshToken, RefreshTokenStatus
from backend.src.auth.google_oauth import GoogleTokenResult
from backend.src.auth.web_session import WebSession
from backend.src.config import Settings
from backend.src.db.web_session_store import WebSessionIssued

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
SECRET = "SECRET-MARKER-do-not-print-9f3a7c"


class SettingsRedactionTests(unittest.TestCase):
    def _settings(self) -> Settings:
        return Settings(
            sqlite_db_path="backend/.data/local.db",
            environment="local",
            public_base_url="https://mcp.example.com",
            mcp_resource_path="/mcp",
            local_vault_key=SECRET,
            google_client_id="visible-client-id",
            google_client_secret=SECRET,
            google_ads_developer_token=SECRET,
            allowed_hosts=("mcp.example.com",),
            cors_allowed_origins=(),
        )

    def test_repr_hides_local_vault_key_client_secret_and_developer_token(self) -> None:
        rendered = repr(self._settings())
        self.assertNotIn(SECRET, rendered)
        self.assertIn("visible-client-id", rendered)

    def test_str_hides_secrets_too(self) -> None:
        self.assertNotIn(SECRET, str(self._settings()))


class GoogleAdsCredentialsRedactionTests(unittest.TestCase):
    def _credentials(self) -> GoogleAdsCredentials:
        return GoogleAdsCredentials(
            developer_token=SECRET,
            client_id="visible-client-id",
            client_secret=SECRET,
            refresh_token=SECRET,
            login_customer_id="1234567890",
        )

    def test_repr_hides_developer_token_client_secret_and_refresh_token(self) -> None:
        rendered = repr(self._credentials())
        self.assertNotIn(SECRET, rendered)
        self.assertIn("visible-client-id", rendered)
        self.assertIn("1234567890", rendered)


class GoogleTokenResultRedactionTests(unittest.TestCase):
    def test_repr_hides_refresh_and_access_token(self) -> None:
        result = GoogleTokenResult(
            refresh_token=SECRET,
            access_token=SECRET,
            google_subject="google-sub-1",
            email="user@example.com",
        )
        rendered = repr(result)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("google-sub-1", rendered)


class ConnectorTokenRedactionTests(unittest.TestCase):
    def test_authorization_code_repr_hides_code(self) -> None:
        code = AuthorizationCode(
            code=SECRET,
            transaction_id="txn-1",
            principal_id="principal-1",
            client_id="https://claude.ai/client",
            redirect_uri="https://claude.ai/callback",
            code_challenge="challenge",
            code_challenge_method="S256",
            resource="https://mcp.example.com/mcp",
            scope="",
            expires_at=NOW,
        )
        rendered = repr(code)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("txn-1", rendered)

    def test_access_token_repr_hides_token(self) -> None:
        token = AccessToken(
            token=SECRET,
            principal_id="principal-1",
            client_id="https://claude.ai/client",
            resource="https://mcp.example.com/mcp",
            scope="",
            expires_at=NOW,
        )
        rendered = repr(token)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("principal-1", rendered)

    def test_refresh_token_repr_hides_token(self) -> None:
        token = RefreshToken(
            token=SECRET,
            family_id="family-1",
            principal_id="principal-1",
            client_id="https://claude.ai/client",
            resource="https://mcp.example.com/mcp",
            scope="",
            expires_at=NOW,
            status=RefreshTokenStatus.ACTIVE,
        )
        rendered = repr(token)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("family-1", rendered)


class WebSessionRedactionTests(unittest.TestCase):
    def test_web_session_repr_hides_token_and_csrf_token(self) -> None:
        session = WebSession(
            token=SECRET,
            csrf_token=SECRET,
            principal_id="principal-1",
            expires_at=NOW,
        )
        rendered = repr(session)
        self.assertNotIn(SECRET, rendered)
        self.assertIn("principal-1", rendered)

    def test_web_session_issued_repr_hides_both_tokens(self) -> None:
        issued = WebSessionIssued(token=SECRET, csrf_token=SECRET)
        self.assertNotIn(SECRET, repr(issued))


if __name__ == "__main__":
    unittest.main()
