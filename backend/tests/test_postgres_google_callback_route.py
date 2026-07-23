"""PostgreSQL unit-of-work branch tests for the Google OAuth callback route.

``google_callback``'s Claude-client leg is the one remaining SQLite-only write
path identified while closing out todo.md 4.3: it writes a Google Ads refresh
token into the vault and a credential/grant/authorization-code into three
RLS-protected tables. These tests prove the split into short, separately
committed units of work around the two calls that must never run inside an
open DB transaction (docs/decisions/0006): the upstream Google code exchange
and the vault write.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from backend.src.app import create_app  # noqa: E402
from backend.src.auth.domain import (  # noqa: E402
    AuthorizationTransaction,
    TransactionStatus,
    compute_code_challenge,
)
from backend.src.auth.google_oauth import FakeGoogleOAuthClient, GoogleTokenResult  # noqa: E402
from backend.src.auth.server import _postgres_google_callback  # noqa: E402
from backend.src.config import Settings  # noqa: E402

PUBLIC_BASE_URL = "https://connector.example.com"
CLIENT_ID_URL = "https://client.example.com/oauth-client.json"
CLIENT_REDIRECT_URI = "https://client.example.com/callback"


def _transaction(**overrides) -> AuthorizationTransaction:
    now = datetime.now(UTC)
    fields = {
        "transaction_id": "transaction-1",
        "client_id": CLIENT_ID_URL,
        "redirect_uri": CLIENT_REDIRECT_URI,
        "code_challenge": compute_code_challenge("a-code-verifier-that-is-long-enough-1234567890"),
        "code_challenge_method": "S256",
        "resource": f"{PUBLIC_BASE_URL}/mcp",
        "scope": "adwords offline_access",
        "client_state": "client-state-1",
        "consent_csrf_hash": "csrf-hash",
        "expires_at": now + timedelta(minutes=10),
        "status": TransactionStatus.CONSENTED,
    }
    fields.update(overrides)
    return AuthorizationTransaction(**fields)


def _settings() -> Settings:
    from cryptography.fernet import Fernet

    return Settings(
        sqlite_db_path=":memory:",
        environment="test",
        public_base_url=PUBLIC_BASE_URL,
        mcp_resource_path="/mcp",
        local_vault_key=Fernet.generate_key().decode(),
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_ads_developer_token="developer-token",
        allowed_hosts=("connector.example.com",),
        cors_allowed_origins=(),
    )


class FakeWork:
    """One request-scoped unit of work; records its own transaction boundary."""

    def __init__(self, repositories: SimpleNamespace, events: list[str]):
        self.repositories = repositories
        self.events = events
        self.exit_exception_type: type[BaseException] | None = None

    def __enter__(self) -> FakeWork:
        self.events.append("work.enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exit_exception_type = exc_type
        self.events.append("work.exit")

    def bind_principal(self, principal_id: str) -> None:
        self.events.append(f"principal.bind:{principal_id}")


class FakeFactory:
    """Return the next queued work for each ``.request()`` call, in order."""

    def __init__(self, works: list[FakeWork]):
        self._works = list(works)

    def request(self) -> FakeWork:
        return self._works.pop(0)


class PostgresGoogleCallbackUnitTests(unittest.IsolatedAsyncioTestCase):
    """Direct tests of ``_postgres_google_callback`` using fake units of work."""

    def _context(self, *, factory: FakeFactory, google_client, vault) -> SimpleNamespace:
        return SimpleNamespace(
            postgres_uow_factory=factory,
            google_client=google_client,
            vault=vault,
            login_google_client=None,
            settings=SimpleNamespace(environment="test"),
        )

    async def test_success_closes_transaction_and_principal_work_before_external_calls(
        self,
    ) -> None:
        events: list[str] = []
        transaction = _transaction()
        principal_id = str(uuid.uuid4())
        saved_credentials: list[tuple[str, str, int]] = []
        saved_grants: list[tuple[str, str, str]] = []
        saved_codes: list = []
        saved_transactions: list[AuthorizationTransaction] = []

        class Transactions:
            def get(self, transaction_id: str):
                events.append(f"transactions.get:{transaction_id}")
                return transaction

            def save(self, updated: AuthorizationTransaction) -> None:
                events.append(f"transactions.save:{updated.status.value}")
                saved_transactions.append(updated)

        class Principals:
            def get_or_create(self, issuer: str, subject: str):
                events.append(f"principals.get_or_create:{issuer}:{subject}")
                return SimpleNamespace(id=principal_id)

        class Credentials:
            def upsert(self, owner: str, vault_ref: str, key_version: int) -> None:
                events.append(f"credentials.upsert:{owner}")
                saved_credentials.append((owner, vault_ref, key_version))

        class ClientGrants:
            def record_consent(self, owner: str, client_id: str, scope: str) -> None:
                events.append(f"grants.record_consent:{owner}")
                saved_grants.append((owner, client_id, scope))

        class Codes:
            def save(self, code) -> None:  # noqa: ANN001
                events.append("codes.save")
                saved_codes.append(code)

        class GoogleClient:
            def exchange_code(self, *, code: str) -> GoogleTokenResult:
                assert events[-1] == "work.exit", "must run after the transaction-read work closed"
                events.append(f"google.exchange:{code}")
                return GoogleTokenResult(
                    refresh_token="google-refresh-1",
                    access_token="google-access-1",
                    google_subject="google-subject-1",
                    email="user@example.com",
                    granted_scopes=None,
                )

        class Vault:
            def store(self, secret: str) -> str:
                assert events[-1] == "work.exit", "must run after the principal work closed"
                events.append("vault.store")
                return "vault-ref-1"

        works = [
            FakeWork(SimpleNamespace(authorization_transactions=Transactions()), events),
            FakeWork(SimpleNamespace(principals=Principals()), events),
            FakeWork(
                SimpleNamespace(
                    credentials=Credentials(),
                    client_grants=ClientGrants(),
                    authorization_transactions=Transactions(),
                    authorization_codes=Codes(),
                ),
                events,
            ),
        ]
        context = self._context(
            factory=FakeFactory(works), google_client=GoogleClient(), vault=Vault()
        )

        response = await _postgres_google_callback(
            "transaction-1", code="g-code-1", error=None, context=context
        )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["state"][0], "client-state-1")
        self.assertEqual(saved_credentials, [(principal_id, "vault-ref-1", 1)])
        self.assertEqual(saved_grants, [(principal_id, CLIENT_ID_URL, "adwords offline_access")])
        self.assertEqual(len(saved_codes), 1)
        self.assertEqual(saved_transactions[0].status, TransactionStatus.COMPLETED)
        self.assertEqual(
            events,
            [
                "work.enter",
                "transactions.get:transaction-1",
                "work.exit",
                "google.exchange:g-code-1",
                "work.enter",
                "principals.get_or_create:https://accounts.google.com:google-subject-1",
                "work.exit",
                "vault.store",
                "work.enter",
                f"principal.bind:{principal_id}",
                "credentials.upsert:" + principal_id,
                "grants.record_consent:" + principal_id,
                "transactions.save:completed",
                "codes.save",
                "work.exit",
            ],
        )

    async def test_unknown_transaction_falls_back_to_web_login_without_touching_vault(
        self,
    ) -> None:
        class Transactions:
            def get(self, transaction_id: str):
                return None

        class LoginStates:
            def claim(self, raw_state: str):
                return False, datetime.now(UTC) + timedelta(minutes=5)

        class FailIfCalledVault:
            def store(self, secret: str) -> str:
                raise AssertionError("unknown transaction must never write the vault")

        class FailIfCalledGoogleClient:
            def exchange_code(self, *, code: str):
                raise AssertionError("unknown transaction must never exchange a Google code")

        works = [
            FakeWork(SimpleNamespace(authorization_transactions=Transactions()), []),
            # handle_web_login_callback's own login-state-claim unit of work.
            FakeWork(SimpleNamespace(web_login_states=LoginStates()), []),
        ]
        context = self._context(
            factory=FakeFactory(works),
            google_client=FailIfCalledGoogleClient(),
            vault=FailIfCalledVault(),
        )

        response = await _postgres_google_callback(
            "unknown-state", code="g-code-1", error=None, context=context
        )

        # login_google_client is None on this context, so the /approvals login
        # fallback (handle_web_login_callback) fails closed with a config error --
        # proof the fallback was actually reached, not the Claude-client leg.
        self.assertEqual(response.status_code, 500)

    async def test_partial_scope_grant_is_denied_before_any_principal_or_vault_work(self) -> None:
        transaction = _transaction()

        class Transactions:
            def get(self, transaction_id: str):
                return transaction

        class GoogleClient:
            def exchange_code(self, *, code: str) -> GoogleTokenResult:
                return GoogleTokenResult(
                    refresh_token="google-refresh-1",
                    access_token="google-access-1",
                    google_subject="google-subject-1",
                    email="user@example.com",
                    granted_scopes=("openid", "email"),  # adwords missing
                )

        class FailIfCalledVault:
            def store(self, secret: str) -> str:
                raise AssertionError("scope-denied grant must never write the vault")

        factory = FakeFactory(
            [FakeWork(SimpleNamespace(authorization_transactions=Transactions()), [])]
        )
        context = self._context(
            factory=factory, google_client=GoogleClient(), vault=FailIfCalledVault()
        )

        response = await _postgres_google_callback(
            "transaction-1", code="g-code-1", error=None, context=context
        )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["error"][0], "access_denied")
        self.assertEqual(factory._works, [])  # no second/third work was ever requested

    async def test_reusing_a_completed_transaction_rolls_back_the_write_work(self) -> None:
        """An already-COMPLETED transaction makes ``issue_authorization_code`` raise;
        the credential/grant writes that already ran in the same work must not commit."""
        completed_transaction = _transaction(status=TransactionStatus.COMPLETED)
        principal_id = str(uuid.uuid4())
        events: list[str] = []

        class Transactions:
            def get(self, transaction_id: str):
                return completed_transaction

        class Principals:
            def get_or_create(self, issuer: str, subject: str):
                return SimpleNamespace(id=principal_id)

        class Credentials:
            def upsert(self, owner: str, vault_ref: str, key_version: int) -> None:
                events.append("credentials.upsert")

        class ClientGrants:
            def record_consent(self, owner: str, client_id: str, scope: str) -> None:
                events.append("grants.record_consent")

        class GoogleClient:
            def exchange_code(self, *, code: str) -> GoogleTokenResult:
                return GoogleTokenResult(
                    refresh_token="google-refresh-1",
                    access_token="google-access-1",
                    google_subject="google-subject-1",
                    email="user@example.com",
                    granted_scopes=None,
                )

        class Vault:
            def store(self, secret: str) -> str:
                return "vault-ref-1"

        write_work = FakeWork(
            SimpleNamespace(credentials=Credentials(), client_grants=ClientGrants()), events
        )
        works = [
            FakeWork(SimpleNamespace(authorization_transactions=Transactions()), events),
            FakeWork(SimpleNamespace(principals=Principals()), events),
            write_work,
        ]
        context = self._context(
            factory=FakeFactory(works), google_client=GoogleClient(), vault=Vault()
        )

        response = await _postgres_google_callback(
            "transaction-1", code="g-code-1", error=None, context=context
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(events.count("credentials.upsert"), 1)
        self.assertEqual(events.count("grants.record_consent"), 1)
        self.assertIsNotNone(write_work.exit_exception_type)


class InMemoryPostgresBackend:
    """Shared state behind a Postgres-shaped fake factory, across independent requests."""

    def __init__(self) -> None:
        self.transactions: dict[str, AuthorizationTransaction] = {}
        self.principals: dict[tuple[str, str], SimpleNamespace] = {}
        self.credentials: list[tuple[str, str, int]] = []
        self.grants: list[tuple[str, str, str]] = []
        self.codes: list = []


class _BackedTransactions:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend

    def get(self, transaction_id: str):
        return self._backend.transactions.get(transaction_id)

    def save(self, transaction: AuthorizationTransaction) -> None:
        self._backend.transactions[transaction.transaction_id] = transaction


class _BackedPrincipals:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend

    def get_or_create(self, issuer: str, subject: str) -> SimpleNamespace:
        key = (issuer, subject)
        if key not in self._backend.principals:
            self._backend.principals[key] = SimpleNamespace(id=str(uuid.uuid4()))
        return self._backend.principals[key]


class _BackedCredentials:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend

    def upsert(self, principal_id: str, vault_ref: str, key_version: int) -> None:
        self._backend.credentials.append((principal_id, vault_ref, key_version))


class _BackedClientGrants:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend

    def record_consent(self, principal_id: str, client_id: str, scope: str) -> None:
        self._backend.grants.append((principal_id, client_id, scope))


class _BackedCodes:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend

    def save(self, code) -> None:  # noqa: ANN001
        self._backend.codes.append(code)


class _BackedWork:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend
        self.repositories = SimpleNamespace(
            authorization_transactions=_BackedTransactions(backend),
            principals=_BackedPrincipals(backend),
            credentials=_BackedCredentials(backend),
            client_grants=_BackedClientGrants(backend),
            authorization_codes=_BackedCodes(backend),
        )

    def __enter__(self) -> _BackedWork:
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        return None

    def bind_principal(self, principal_id: str) -> None:
        return None


class _BackedFactory:
    def __init__(self, backend: InMemoryPostgresBackend):
        self._backend = backend

    def request(self) -> _BackedWork:
        return _BackedWork(self._backend)


class PostgresGoogleCallbackAsgiTests(unittest.IsolatedAsyncioTestCase):
    """Drives ``/authorize`` -> ``/authorize/consent`` -> ``/google/callback`` over ASGI
    with the production PostgreSQL unit-of-work factory wired in (fully faked backend --
    no real PostgreSQL is available in this environment; see
    ``test_postgres_rls_integration.py`` for the opt-in live-DSN proof)."""

    async def test_full_flow_stores_credential_and_issues_a_redeemable_code(self) -> None:
        backend = InMemoryPostgresBackend()
        settings = _settings()
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(granted_scopes=None),
            postgres_uow_factory=_BackedFactory(backend),  # pyright: ignore[reportArgumentType]
        )

        def _cimd_handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "93.184.216.34":
                return httpx.Response(
                    200,
                    json={
                        "client_id": CLIENT_ID_URL,
                        "redirect_uris": [CLIENT_REDIRECT_URI],
                        "token_endpoint_auth_method": "none",
                    },
                )
            return httpx.Response(404)

        app.state.auth_context.http_client = httpx.Client(
            transport=httpx.MockTransport(_cimd_handler)
        )
        app.state.auth_context.resolve = lambda hostname: ["93.184.216.34"]

        verifier = "a-code-verifier-that-is-long-enough-1234567890"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": CLIENT_ID_URL,
                    "redirect_uri": CLIENT_REDIRECT_URI,
                    "code_challenge": compute_code_challenge(verifier),
                    "code_challenge_method": "S256",
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                    "state": "client-state-1",
                },
            )
            self.assertEqual(page.status_code, 200)
            import re

            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            txn_id = re.search(r'name="transaction_id" value="([^"]+)"', page.text).group(1)

            consent = await client.post(
                "/authorize/consent",
                data={"transaction_id": txn_id, "decision": "approve", "csrf_token": csrf},
            )
            self.assertEqual(consent.status_code, 302)

            callback = await client.get(
                "/google/callback", params={"state": txn_id, "code": "g-code-1"}
            )

        self.assertEqual(callback.status_code, 302)
        query = parse_qs(urlsplit(callback.headers["location"]).query)
        self.assertEqual(query["state"][0], "client-state-1")
        self.assertEqual(len(backend.credentials), 1)
        self.assertEqual(len(backend.grants), 1)
        self.assertEqual(len(backend.codes), 1)
        self.assertEqual(backend.transactions[txn_id].status, TransactionStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
