"""Transaction-bound repository tests for local-only PostgreSQL MCP tools."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.config import Settings  # noqa: E402
from backend.src.mcp.proposals import _proposal_repositories  # noqa: E402
from backend.src.mcp.tools import _resolve_credentials  # noqa: E402


class FakeWork:
    """Observable principal-scoped MCP tool unit of work."""

    def __init__(self):
        self.accounts = SimpleNamespace(name="accounts")
        self.proposals = SimpleNamespace(name="proposals")
        self.repositories = SimpleNamespace(
            accounts=self.accounts,
            proposals=self.proposals,
        )
        self.bound: list[str] = []
        self.exited = False

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exited = True

    def bind_principal(self, principal_id: str) -> None:
        """Record the RLS principal installed for the tool call."""
        self.bound.append(principal_id)


class FakeFactory:
    """Return one tool transaction."""

    def __init__(self, work: FakeWork):
        self.work = work

    def request(self) -> FakeWork:
        """Return the configured work instance."""
        return self.work


class PostgresMcpToolTests(unittest.TestCase):
    def test_local_proposal_repositories_share_one_principal_transaction(self) -> None:
        work = FakeWork()
        context = SimpleNamespace(postgres_uow_factory=FakeFactory(work))
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"

        with _proposal_repositories(
            context,  # pyright: ignore[reportArgumentType]
            SimpleNamespace(),  # pyright: ignore[reportArgumentType]
            principal_id,
        ) as (accounts, proposals):
            self.assertIs(accounts, work.accounts)
            self.assertIs(proposals, work.proposals)
            self.assertFalse(work.exited)

        self.assertEqual(work.bound, [principal_id])
        self.assertTrue(work.exited)

    def test_vault_read_happens_after_credential_metadata_transaction_closes(self) -> None:
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"
        events: list[str] = []

        class Accounts:
            def get_active_account(self, owner: str, customer_id: str):  # noqa: ANN201
                events.append(f"account:{owner}:{customer_id}")
                return SimpleNamespace(login_customer_id="1112223333")

        class Credentials:
            def get_active(self, owner: str):  # noqa: ANN201
                events.append(f"credential:{owner}")
                return SimpleNamespace(vault_ref="vault-ref")

        class CredentialWork:
            def __init__(self):
                self.repositories = SimpleNamespace(accounts=Accounts(), credentials=Credentials())
                self.exited = False

            def __enter__(self):  # noqa: ANN204
                events.append("work.enter")
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
                self.exited = True
                events.append("work.exit")

            def bind_principal(self, owner: str) -> None:
                events.append(f"bind:{owner}")

        work = CredentialWork()

        test_case = self

        class Vault:
            def read(self, vault_ref: str) -> str:
                test_case.assertTrue(work.exited)
                events.append(f"vault.read:{vault_ref}")
                return "refresh-token"

        vault = Vault()
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
        context = SimpleNamespace(
            postgres_uow_factory=FakeFactory(work),
            settings=settings,
            vault=vault,
        )

        with patch("backend.src.mcp.tools.authenticated_principal_id", return_value=principal_id):
            resolved = _resolve_credentials(
                context,  # pyright: ignore[reportArgumentType]
                SimpleNamespace(),  # pyright: ignore[reportArgumentType]
                "1234567890",
            )

        self.assertEqual(resolved.refresh_token, "refresh-token")
        self.assertEqual(
            events,
            [
                "work.enter",
                f"bind:{principal_id}",
                f"account:{principal_id}:1234567890",
                f"credential:{principal_id}",
                "work.exit",
                "vault.read:vault-ref",
            ],
        )


if __name__ == "__main__":
    unittest.main()
