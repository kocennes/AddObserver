"""ASGI contract tests for PostgreSQL-backed bearer HTTP API routes."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.app import create_app  # noqa: E402
from backend.src.auth.domain import AccessToken  # noqa: E402
from backend.src.auth.google_oauth import FakeGoogleOAuthClient  # noqa: E402
from backend.src.config import Settings  # noqa: E402
from backend.src.db.models import AdsAccount  # noqa: E402


class FakeTokens:
    """Return one active token without retaining its raw bearer value."""

    def __init__(self, token: AccessToken):
        self.token = token
        self.lookups: list[str] = []

    def get_access(self, raw_token: str) -> AccessToken | None:
        """Resolve only the test bearer token."""
        self.lookups.append(raw_token)
        return self.token if raw_token == "valid-access" else None


class FakeAccounts:
    """Record principal-scoped account reads."""

    def __init__(self, account: AdsAccount):
        self.account = account
        self.list_calls: list[str] = []

    def list_active_accounts(self, principal_id: str) -> list[AdsAccount]:
        """Return the linked account for its owner."""
        self.list_calls.append(principal_id)
        return [self.account] if principal_id == self.account.principal_id else []


class FakeWork:
    """Minimal request unit of work used by the API composition test."""

    def __init__(self, token: AccessToken, account: AdsAccount):
        self.tokens = FakeTokens(token)
        self.accounts = FakeAccounts(account)
        self.repositories = SimpleNamespace(
            tokens=self.tokens,
            accounts=self.accounts,
            proposals=SimpleNamespace(),
        )
        self.bootstraps: list[str] = []
        self.entered = False
        self.exited = False

    def __enter__(self):  # noqa: ANN204
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exited = True

    def bootstrap_access_token(self, raw_token: str) -> str | None:
        """Bind the token owner exactly as the production bootstrap helper does."""
        self.bootstraps.append(raw_token)
        return self.tokens.token.principal_id if raw_token == "valid-access" else None


class FakeFactory:
    """Expose one observable work instance to a single request."""

    def __init__(self, work: FakeWork):
        self.work = work

    def request(self) -> FakeWork:
        """Return the request work instance."""
        return self.work


class PostgresApiRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_accounts_bootstraps_and_reads_inside_the_same_work(self) -> None:
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"
        token = AccessToken(
            token="",
            principal_id=principal_id,
            client_id="client-1",
            resource="https://connector.example.com/mcp",
            scope="read",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        account = AdsAccount(
            id="account-1",
            principal_id=principal_id,
            customer_id="1234567890",
            login_customer_id=None,
            status="active",
            created_at=datetime.now(UTC),
        )
        work = FakeWork(token, account)
        settings = Settings(
            sqlite_db_path=":memory:",
            environment="test",
            public_base_url="https://connector.example.com",
            mcp_resource_path="/mcp",
            local_vault_key=Fernet.generate_key().decode(),
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_ads_developer_token="developer-token",
            allowed_hosts=("connector.example.com",),
            cors_allowed_origins=(),
        )
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(),
            postgres_uow_factory=FakeFactory(work),  # pyright: ignore[reportArgumentType]
        )

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://connector.example.com",
            ) as client,
        ):
            response = await client.get(
                "/api/v1/accounts", headers={"Authorization": "Bearer valid-access"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["accounts"][0]["customer_id"], "1234567890")
        self.assertTrue(work.entered)
        self.assertTrue(work.exited)
        self.assertEqual(work.bootstraps, ["valid-access"])
        self.assertEqual(work.tokens.lookups, ["valid-access"])
        self.assertEqual(work.accounts.list_calls, [principal_id])


if __name__ == "__main__":
    unittest.main()
