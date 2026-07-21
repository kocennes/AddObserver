"""PostgreSQL transaction-boundary tests for connector authorization routes."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.domain import (  # noqa: E402
    AuthorizationTransaction,
    TransactionStatus,
)
from backend.src.auth.google_oauth import GoogleTokenResult  # noqa: E402
from backend.src.auth.server import (  # noqa: E402
    _authorization_transactions,
    _postgres_google_callback,
)


class FakeTransactions:
    pass


class FakeWork:
    def __init__(self, transactions: FakeTransactions):
        self.repositories = SimpleNamespace(authorization_transactions=transactions)
        self.entered = False
        self.exited = False
        self.exit_exception_type: type[BaseException] | None = None

    def __enter__(self):  # noqa: ANN204
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exited = True
        self.exit_exception_type = exc_type


class FakeFactory:
    def __init__(self, work: FakeWork):
        self.work = work

    def request(self) -> FakeWork:
        return self.work


class PostgresAuthorizeRouteTests(unittest.TestCase):
    def test_authorization_transaction_store_uses_short_postgres_work(self) -> None:
        transactions = FakeTransactions()
        work = FakeWork(transactions)
        context = SimpleNamespace(postgres_uow_factory=FakeFactory(work))

        with _authorization_transactions(context) as store:  # pyright: ignore[reportArgumentType]
            self.assertIs(store, transactions)
            self.assertTrue(work.entered)
            self.assertFalse(work.exited)

        self.assertTrue(work.exited)
        self.assertIsNone(work.exit_exception_type)

    def test_authorization_transaction_store_rolls_back_on_route_error(self) -> None:
        work = FakeWork(FakeTransactions())
        context = SimpleNamespace(postgres_uow_factory=FakeFactory(work))

        with (
            self.assertRaisesRegex(RuntimeError, "route failed"),
            _authorization_transactions(context),  # pyright: ignore[reportArgumentType]
        ):
            raise RuntimeError("route failed")

        self.assertTrue(work.exited)
        self.assertIs(work.exit_exception_type, RuntimeError)


class PostgresGoogleCallbackTests(unittest.IsolatedAsyncioTestCase):
    def _transaction(self) -> AuthorizationTransaction:
        return AuthorizationTransaction(
            transaction_id="transaction-id",
            client_id="https://client.example/metadata.json",
            redirect_uri="https://client.example/callback",
            code_challenge="a" * 43,
            code_challenge_method="S256",
            resource="https://connector.example/mcp",
            scope="openid https://www.googleapis.com/auth/adwords",
            client_state="client-state",
            consent_csrf_hash="csrf-hash",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            status=TransactionStatus.CONSENTED,
        )

    async def test_callback_keeps_google_and_vault_outside_db_transactions(self) -> None:
        events: list[str] = []
        transaction = self._transaction()
        principal = SimpleNamespace(id="753e587a-bcad-46c5-9ed0-169a051adb7b")

        class Transactions:
            def __init__(self):
                self.get_calls = 0

            def get(self, state: str):  # noqa: ANN201
                self.get_calls += 1
                events.append(f"transaction.get:{state}")
                return transaction

            def save(self, value) -> None:  # noqa: ANN001
                events.append(f"transaction.save:{value.status.value}")

        transactions = Transactions()

        class Principals:
            def get_or_create(self, issuer: str, subject: str):  # noqa: ANN201
                events.append(f"principal.get_or_create:{issuer}:{subject}")
                return principal

        class Credentials:
            def upsert(self, owner: str, vault_ref: str, *, key_version: int) -> None:
                events.append(f"credential.upsert:{owner}:{vault_ref}:{key_version}")

        class Grants:
            def record_consent(self, owner: str, client_id: str, scope: str) -> None:
                events.append(f"grant.record:{owner}:{client_id}:{scope}")

        class Codes:
            def save(self, auth_code) -> None:  # noqa: ANN001
                events.append(f"code.save:{auth_code.principal_id}")

        class Work:
            def __init__(self, repositories):  # noqa: ANN001
                self.repositories = repositories

            def __enter__(self):  # noqa: ANN204
                events.append("work.enter")
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
                events.append("work.exit")

            def bind_principal(self, owner: str) -> None:
                events.append(f"principal.bind:{owner}")

        lookup = Work(SimpleNamespace(authorization_transactions=transactions))
        persist = Work(
            SimpleNamespace(
                authorization_transactions=transactions,
                principals=Principals(),
                credentials=Credentials(),
                client_grants=Grants(),
                authorization_codes=Codes(),
            )
        )
        works = [lookup, persist]

        class Factory:
            def request(self):  # noqa: ANN201
                return works.pop(0)

        class GoogleClient:
            def exchange_code(self, *, code: str) -> GoogleTokenResult:
                events.append(f"google.exchange:{code}")
                return GoogleTokenResult(
                    refresh_token="google-refresh",
                    access_token="google-access",
                    google_subject="google-subject",
                    email="user@example.com",
                    granted_scopes=frozenset({"openid", "https://www.googleapis.com/auth/adwords"}),
                )

        class Vault:
            def store(self, secret: str) -> str:
                events.append(f"vault.store:{secret}")
                return "vault-ref"

            def revoke(self, vault_ref: str) -> None:
                events.append(f"vault.revoke:{vault_ref}")

        context = SimpleNamespace(
            postgres_uow_factory=Factory(),
            google_client=GoogleClient(),
            vault=Vault(),
        )
        response = await _postgres_google_callback(
            transaction.transaction_id,
            code="google-code",
            error=None,
            context=context,  # pyright: ignore[reportArgumentType]
        )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["state"], [transaction.client_state])
        self.assertIn("code", query)
        self.assertEqual(
            events,
            [
                "work.enter",
                "transaction.get:transaction-id",
                "work.exit",
                "google.exchange:google-code",
                "vault.store:google-refresh",
                "work.enter",
                "transaction.get:transaction-id",
                "principal.get_or_create:https://accounts.google.com:google-subject",
                f"principal.bind:{principal.id}",
                f"credential.upsert:{principal.id}:vault-ref:1",
                f"grant.record:{principal.id}:{transaction.client_id}:{transaction.scope}",
                "transaction.save:completed",
                f"code.save:{principal.id}",
                "work.exit",
            ],
        )

    async def test_persist_failure_rolls_back_and_revokes_new_vault_secret(self) -> None:
        transaction = self._transaction()
        events: list[str] = []

        class Transactions:
            def get(self, state: str):  # noqa: ANN201
                return transaction

        class Work:
            repositories = SimpleNamespace(
                authorization_transactions=Transactions(),
                principals=SimpleNamespace(
                    get_or_create=lambda issuer, subject: SimpleNamespace(
                        id="753e587a-bcad-46c5-9ed0-169a051adb7b"
                    )
                ),
            )

            def __init__(self, fail: bool = False):
                self.fail = fail

            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
                events.append("rollback" if exc_type else "commit")

            def bind_principal(self, owner: str) -> None:
                if self.fail:
                    raise RuntimeError("sensitive database failure")

        works = [Work(), Work(fail=True)]

        class Factory:
            def request(self):  # noqa: ANN201
                return works.pop(0)

        class Vault:
            def store(self, secret: str) -> str:
                return "new-vault-ref"

            def revoke(self, vault_ref: str) -> None:
                events.append(f"vault.revoke:{vault_ref}")

        context = SimpleNamespace(
            postgres_uow_factory=Factory(),
            google_client=SimpleNamespace(
                exchange_code=lambda **kwargs: GoogleTokenResult(
                    refresh_token="secret",
                    access_token="access",
                    google_subject="subject",
                    email="user@example.com",
                    granted_scopes=frozenset({"https://www.googleapis.com/auth/adwords"}),
                )
            ),
            vault=Vault(),
        )

        response = await _postgres_google_callback(
            transaction.transaction_id,
            code="google-code",
            error=None,
            context=context,  # pyright: ignore[reportArgumentType]
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            parse_qs(urlsplit(response.headers["location"]).query)["error"],
            ["server_error"],
        )
        self.assertEqual(events, ["commit", "rollback", "vault.revoke:new-vault-ref"])


if __name__ == "__main__":
    unittest.main()
