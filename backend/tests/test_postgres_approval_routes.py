"""ASGI contract tests for PostgreSQL-backed browser approval routes."""

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
from backend.src.auth.approvals_routes import handle_web_login_callback  # noqa: E402
from backend.src.auth.domain import hash_token  # noqa: E402
from backend.src.auth.google_oauth import FakeGoogleOAuthClient, GoogleTokenResult  # noqa: E402
from backend.src.config import Settings  # noqa: E402
from backend.src.db.web_session_store import WebSessionLookup  # noqa: E402


class FakeWebSessions:
    """Resolve one active browser session and record lookup scope."""

    def __init__(self, principal_id: str, csrf_token: str):
        self.principal_id = principal_id
        self.csrf_token = csrf_token
        self.lookups: list[str] = []

    def lookup(self, raw_token: str) -> WebSessionLookup:
        """Return an active session only for the expected raw cookie."""
        self.lookups.append(raw_token)
        if raw_token != "session-token":
            return WebSessionLookup(None, None, None, False)
        return WebSessionLookup(
            principal_id=self.principal_id,
            csrf_token_hash=hash_token(self.csrf_token),
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
            revoked=False,
        )


class FakeProposals:
    """Return an empty principal-scoped approval queue."""

    def __init__(self):
        self.list_calls: list[str] = []

    def list_pending(self, principal_id: str):  # noqa: ANN201
        """Record the owner and return an empty page."""
        self.list_calls.append(principal_id)
        return SimpleNamespace(proposals=[])


class FakeWork:
    """Observable browser request unit of work."""

    def __init__(self, principal_id: str, csrf_token: str):
        self.sessions = FakeWebSessions(principal_id, csrf_token)
        self.proposals = FakeProposals()
        self.repositories = SimpleNamespace(
            web_sessions=self.sessions,
            proposals=self.proposals,
            approvals=SimpleNamespace(),
        )
        self.bootstraps: list[str] = []
        self.exited = False

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exited = True

    def bootstrap_web_session(self, raw_token: str) -> str | None:
        """Bind only the expected browser session owner."""
        self.bootstraps.append(raw_token)
        return self.sessions.principal_id if raw_token == "session-token" else None


class FakeFactory:
    """Return one work instance for one browser request."""

    def __init__(self, work: FakeWork):
        self.work = work

    def request(self) -> FakeWork:
        """Return the configured request work."""
        return self.work


class PostgresApprovalRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_approval_list_bootstraps_session_and_reads_in_one_work(self) -> None:
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"
        csrf_token = "csrf-token"
        work = FakeWork(principal_id, csrf_token)
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
                cookies={"web_session": "session-token", "web_csrf": csrf_token},
            ) as client,
        ):
            response = await client.get("/approvals")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Bekleyen öneri yok", response.text)
        self.assertEqual(work.bootstraps, ["session-token"])
        self.assertEqual(work.sessions.lookups, ["session-token"])
        self.assertEqual(work.proposals.list_calls, [principal_id])
        self.assertTrue(work.exited)

    async def test_login_callback_closes_state_claim_before_google_exchange(self) -> None:
        events: list[str] = []
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"

        class LoginStates:
            def claim(self, raw_state: str):  # noqa: ANN201
                events.append(f"state.claim:{raw_state}")
                return False, datetime.now(UTC) + timedelta(minutes=5)

        class Principals:
            def get(self, issuer: str, subject: str):  # noqa: ANN201
                events.append(f"principal.get:{issuer}:{subject}")
                return SimpleNamespace(id=principal_id)

        class Sessions:
            def create(self, owner: str, token: str, csrf: str, expires_at):  # noqa: ANN001, ANN201
                events.append(f"session.create:{owner}")

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

        works = [
            Work(SimpleNamespace(web_login_states=LoginStates())),
            Work(SimpleNamespace(principals=Principals(), web_sessions=Sessions())),
        ]

        class Factory:
            def request(self):  # noqa: ANN201
                return works.pop(0)

        test_case = self

        class GoogleClient:
            def exchange_code(self, *, code: str) -> GoogleTokenResult:
                claim_exit_index = events.index("work.exit")
                events.append(f"google.exchange:{code}")
                test_case.assertGreaterEqual(claim_exit_index, 0)
                return GoogleTokenResult(
                    refresh_token="unused-refresh",
                    access_token="unused-access",
                    google_subject="google-subject",
                    email="user@example.com",
                )

        context = SimpleNamespace(
            postgres_uow_factory=Factory(),
            login_google_client=GoogleClient(),
            settings=SimpleNamespace(environment="test"),
        )

        response = await handle_web_login_callback(
            "login-state",
            code="google-code",
            error=None,
            context=context,  # pyright: ignore[reportArgumentType]
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            events,
            [
                "work.enter",
                "state.claim:login-state",
                "work.exit",
                "google.exchange:google-code",
                "work.enter",
                "principal.get:https://accounts.google.com:google-subject",
                f"principal.bind:{principal_id}",
                f"session.create:{principal_id}",
                "work.exit",
            ],
        )

    async def test_disconnect_atomically_enqueues_revocation_without_touching_vault(self) -> None:
        events: list[str] = []
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"
        csrf_token = "csrf-token"
        credential = SimpleNamespace(id="credential-id")

        class Sessions(FakeWebSessions):
            def revoke_all_for_principal(self, owner: str) -> None:
                events.append(f"sessions.revoke:{owner}")

        class Tokens:
            def revoke_all_for_principal(self, owner: str, *, now: datetime) -> None:
                events.append(f"tokens.revoke:{owner}")

        class Credentials:
            def get_active(self, owner: str):  # noqa: ANN201
                events.append(f"credential.get:{owner}")
                return credential

        class Revocations:
            def revoke_and_enqueue(self, owner: str, credential_id: str, *, now: datetime):  # noqa: ANN201
                events.append(f"revocation.enqueue:{owner}:{credential_id}")
                return SimpleNamespace(id="job-id")

        class Accounts:
            def list_accounts(self, owner: str):  # noqa: ANN201
                events.append(f"accounts.list:{owner}")
                return [SimpleNamespace(id="account-id")]

            def disconnect_all(self, owner: str) -> None:
                events.append(f"accounts.disconnect:{owner}")

        class Audit:
            def insert(self, event) -> None:  # noqa: ANN001
                events.append(f"audit.insert:{event.principal_id}:{event.outcome}")

        class Work:
            def __init__(self):
                self.sessions = Sessions(principal_id, csrf_token)
                self.repositories = SimpleNamespace(
                    web_sessions=self.sessions,
                    tokens=Tokens(),
                    credentials=Credentials(),
                    credential_revocations=Revocations(),
                    accounts=Accounts(),
                    audit=Audit(),
                )

            def __enter__(self):  # noqa: ANN204
                events.append("work.enter")
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
                events.append("work.exit")

            def bootstrap_web_session(self, raw_token: str) -> str | None:
                events.append(f"session.bootstrap:{raw_token}")
                return principal_id

        work = Work()

        class Factory:
            def request(self):  # noqa: ANN201
                return work

        class FailIfCalledVault:
            def read(self, vault_ref: str) -> str:
                raise AssertionError("disconnect route must not read the vault")

            def store(self, secret_value: str) -> str:
                raise AssertionError("disconnect route must not write the vault")

            def revoke(self, vault_ref: str) -> None:
                raise AssertionError("disconnect route must leave vault revoke to the worker")

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
            postgres_uow_factory=Factory(),  # pyright: ignore[reportArgumentType]
        )
        app.state.auth_context.vault = FailIfCalledVault()

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://connector.example.com",
                cookies={"web_session": "session-token", "web_csrf": csrf_token},
            ) as client,
        ):
            response = await client.post(
                "/disconnect",
                data={"csrf_token": csrf_token},
                headers={"X-Correlation-ID": "disconnect-pg-1"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            events,
            [
                "work.enter",
                "session.bootstrap:session-token",
                f"accounts.list:{principal_id}",
                f"tokens.revoke:{principal_id}",
                f"credential.get:{principal_id}",
                f"revocation.enqueue:{principal_id}:credential-id",
                f"accounts.disconnect:{principal_id}",
                f"sessions.revoke:{principal_id}",
                f"audit.insert:{principal_id}:revocation_queued",
                "work.exit",
            ],
        )


if __name__ == "__main__":
    unittest.main()
