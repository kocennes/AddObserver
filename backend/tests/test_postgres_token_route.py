"""PostgreSQL unit-of-work branch tests for the OAuth token endpoint."""

from __future__ import annotations

import json
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
from backend.src.auth.domain import (  # noqa: E402
    AccessToken,
    AuthError,
    AuthorizationCode,
    RefreshOutcome,
    RefreshToken,
    compute_code_challenge,
)
from backend.src.auth.google_oauth import FakeGoogleOAuthClient  # noqa: E402
from backend.src.auth.server import _postgres_token_response  # noqa: E402
from backend.src.config import Settings  # noqa: E402


class FakeCodes:
    def __init__(self, code: AuthorizationCode):
        self.code = code
        self.claimed: list[str] = []

    def claim(self, raw_code: str) -> tuple[AuthorizationCode, bool]:
        self.claimed.append(raw_code)
        return self.code, False


class FakeTokens:
    def __init__(self, outcome: RefreshOutcome | None = None):
        self.outcome = outcome
        self.saved_access: list[AccessToken] = []
        self.saved_refresh: list[RefreshToken] = []
        self.rotated: list[str] = []

    def save_access(self, token: AccessToken) -> None:
        self.saved_access.append(token)

    def save_refresh(self, token: RefreshToken) -> None:
        self.saved_refresh.append(token)

    def rotate(self, raw_token: str, *, now: datetime) -> RefreshOutcome:
        self.rotated.append(raw_token)
        assert self.outcome is not None
        return self.outcome


class FakeWork:
    def __init__(self, *, codes: FakeCodes, tokens: FakeTokens):
        self.repositories = SimpleNamespace(authorization_codes=codes, tokens=tokens)
        self.code_bootstraps: list[str] = []
        self.refresh_bootstraps: list[str] = []
        self.exit_exception_types: list[type[BaseException] | None] = []

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exit_exception_types.append(exc_type)
        return None

    def bootstrap_authorization_code(self, raw_code: str) -> str:
        self.code_bootstraps.append(raw_code)
        return "753e587a-bcad-46c5-9ed0-169a051adb7b"

    def bootstrap_refresh_token(self, raw_token: str) -> str:
        self.refresh_bootstraps.append(raw_token)
        return "753e587a-bcad-46c5-9ed0-169a051adb7b"


class FakeFactory:
    def __init__(self, work: FakeWork):
        self.work = work

    def request(self) -> FakeWork:
        return self.work


class PostgresTokenRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 19, 12, tzinfo=UTC)
        self.verifier = "v" * 43
        self.code = AuthorizationCode(
            code="authorization-secret",
            transaction_id="transaction-1",
            principal_id="753e587a-bcad-46c5-9ed0-169a051adb7b",
            client_id="client-1",
            redirect_uri="https://client.example/callback",
            code_challenge=compute_code_challenge(self.verifier),
            code_challenge_method="S256",
            resource="https://connector.example/mcp",
            scope="read",
            expires_at=self.now + timedelta(minutes=5),
        )

    def _context(self, work: FakeWork):
        return SimpleNamespace(postgres_uow_factory=FakeFactory(work))

    def test_authorization_code_grant_uses_bootstrap_claim_and_same_work_tokens(self) -> None:
        codes = FakeCodes(self.code)
        tokens = FakeTokens()
        work = FakeWork(codes=codes, tokens=tokens)

        response = _postgres_token_response(
            context=self._context(work),  # pyright: ignore[reportArgumentType]
            grant_type="authorization_code",
            code=self.code.code,
            redirect_uri=self.code.redirect_uri,
            client_id=self.code.client_id,
            code_verifier=self.verifier,
            resource=self.code.resource,
            raw_refresh_token=None,
            now=self.now,
        )

        body = json.loads(bytes(response.body))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(work.code_bootstraps, [self.code.code])
        self.assertEqual(codes.claimed, [self.code.code])
        self.assertEqual(len(tokens.saved_access), 1)
        self.assertEqual(len(tokens.saved_refresh), 1)
        self.assertEqual(body["scope"], "read")

    def test_refresh_grant_bootstraps_before_rotation(self) -> None:
        access = AccessToken(
            token="new-access",
            principal_id=self.code.principal_id,
            client_id=self.code.client_id,
            resource=self.code.resource,
            scope=self.code.scope,
            expires_at=self.now + timedelta(minutes=10),
        )
        refresh = RefreshToken(
            token="new-refresh",
            family_id="family",
            principal_id=self.code.principal_id,
            client_id=self.code.client_id,
            resource=self.code.resource,
            scope=self.code.scope,
            expires_at=self.now + timedelta(days=30),
        )
        tokens = FakeTokens(RefreshOutcome(access_token=access, refresh_token=refresh))
        work = FakeWork(codes=FakeCodes(self.code), tokens=tokens)

        response = _postgres_token_response(
            context=self._context(work),  # pyright: ignore[reportArgumentType]
            grant_type="refresh_token",
            code=None,
            redirect_uri=None,
            client_id=None,
            code_verifier=None,
            resource=None,
            raw_refresh_token="old-refresh",
            now=self.now,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(work.refresh_bootstraps, ["old-refresh"])
        self.assertEqual(tokens.rotated, ["old-refresh"])

    def test_refresh_replay_error_commits_security_revocation_state(self) -> None:
        class ReplayTokens(FakeTokens):
            def rotate(self, raw_token: str, *, now: datetime) -> RefreshOutcome:
                self.rotated.append(raw_token)
                raise AuthError("invalid_grant", "replay; family revoked")

        tokens = ReplayTokens()
        work = FakeWork(codes=FakeCodes(self.code), tokens=tokens)

        response = _postgres_token_response(
            context=self._context(work),  # pyright: ignore[reportArgumentType]
            grant_type="refresh_token",
            code=None,
            redirect_uri=None,
            client_id=None,
            code_verifier=None,
            resource=None,
            raw_refresh_token="replayed-refresh",
            now=self.now,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(tokens.rotated, ["replayed-refresh"])
        self.assertEqual(work.exit_exception_types, [None])


class PostgresTokenAsgiTests(unittest.IsolatedAsyncioTestCase):
    async def test_app_composition_routes_token_grant_through_postgres_factory(self) -> None:
        now = datetime.now(UTC)
        verifier = "v" * 43
        code = AuthorizationCode(
            code="authorization-secret",
            transaction_id="transaction-1",
            principal_id="753e587a-bcad-46c5-9ed0-169a051adb7b",
            client_id="client-1",
            redirect_uri="https://client.example/callback",
            code_challenge=compute_code_challenge(verifier),
            code_challenge_method="S256",
            resource="https://connector.example.com/mcp",
            scope="read",
            expires_at=now + timedelta(minutes=5),
        )
        codes = FakeCodes(code)
        tokens = FakeTokens()
        work = FakeWork(codes=codes, tokens=tokens)
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

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="https://connector.example.com"
            ) as client:
                response = await client.post(
                    "/token",
                    data={
                        "grant_type": "authorization_code",
                        "code": code.code,
                        "redirect_uri": code.redirect_uri,
                        "client_id": code.client_id,
                        "code_verifier": verifier,
                        "resource": code.resource,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(work.code_bootstraps, [code.code])
        self.assertEqual(codes.claimed, [code.code])
        self.assertEqual(len(tokens.saved_access), 1)
        self.assertEqual(len(tokens.saved_refresh), 1)


if __name__ == "__main__":
    unittest.main()
