"""HTTP API route tests for principal-scoped connector resources."""

from __future__ import annotations

import base64
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.app import create_app
from backend.src.approval import Proposal, submit_proposal
from backend.src.auth.domain import AccessToken
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from backend.src.db.oauth_store import TokenRepository
from backend.src.db.proposals import ProposalRepository
from backend.src.db.repository import AdsAccountRepository, PrincipalRepository

PUBLIC_BASE_URL = "https://connector.example.com"


def _flip_last_byte(cursor: str) -> str:
    """Flip one bit in the decoded signature -- flipping a base64 *character* can leave the
    decoded bytes unchanged when it only touches unused padding bits, which makes that
    approach flaky. Round-tripping through bytes is deterministic."""
    padded = cursor + "=" * (-len(cursor) % 4)
    raw = bytearray(base64.urlsafe_b64decode(padded))
    raw[-1] ^= 0x01
    return base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")


def _settings() -> Settings:
    return Settings(
        sqlite_db_path=":memory:",
        environment="test",
        public_base_url=PUBLIC_BASE_URL,
        mcp_resource_path="/mcp",
        local_vault_key=Fernet.generate_key().decode(),
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_ads_developer_token="dev-token",
        allowed_hosts=("connector.example.com",),
        cors_allowed_origins=(),
    )


class ApiHttpRouteTests(unittest.IsolatedAsyncioTestCase):
    def _save_access_token(self, app, principal_id: str, token: str = "caller-token") -> None:
        TokenRepository(app.state.auth_context.conn).save_access(
            AccessToken(
                token=token,
                principal_id=principal_id,
                client_id="https://client.example.com/metadata",
                resource=app.state.auth_context.settings.mcp_resource_uri,
                scope="adwords",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    def _pending_proposal(
        self,
        *,
        proposal_id: str,
        principal_id: str,
        customer_id: str = "1234567890",
        expires_at: datetime | None = None,
    ) -> Proposal:
        draft = Proposal.create(
            proposal_id=proposal_id,
            principal_id=principal_id,
            customer_id=customer_id,
            payload={
                "schema_version": "1",
                "type": "campaign_pause",
                "resource_name": f"customers/{customer_id}/campaigns/123",
                "before": {"status": "ENABLED"},
                "after": {"status": "PAUSED"},
                "reason": "test proposal",
                "evidence_refs": [],
                "risk": "low",
            },
            expires_at=expires_at or datetime.now(UTC) + timedelta(hours=1),
        )
        return submit_proposal(draft, now=datetime.now(UTC))

    async def test_accounts_requires_connector_bearer_token(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get(
                "/api/v1/accounts", headers={"X-Correlation-ID": "route-test-1"}
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertIn("WWW-Authenticate", response.headers)
        self.assertEqual(response.json()["code"], "invalid_token")
        self.assertEqual(response.json()["correlation_id"], "route-test-1")

    async def test_accounts_lists_only_authenticated_principals_accounts(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            principals = PrincipalRepository(conn)
            caller = principals.get_or_create("https://accounts.google.com", "caller")
            other = principals.get_or_create("https://accounts.google.com", "other")
            accounts = AdsAccountRepository(conn)
            accounts.link_account(caller.id, "1234567890", "9999999999")
            accounts.link_account(caller.id, "2222222222", None)
            accounts.link_account(other.id, "3333333333", None)
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/accounts", headers={"Authorization": "Bearer caller-token"}
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "accounts": [
                    {
                        "customer_id": "1234567890",
                        "login_customer_id": "9999999999",
                        "status": "active",
                    },
                    {"customer_id": "2222222222", "login_customer_id": None, "status": "active"},
                ]
            },
        )

    async def test_accounts_list_hides_disconnected_accounts(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            accounts = AdsAccountRepository(conn)
            accounts.link_account(caller.id, "1234567890", None)
            accounts.link_account(caller.id, "2222222222", None)
            accounts.disconnect_all(caller.id)
            accounts.link_account(caller.id, "2222222222", None)
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/accounts", headers={"Authorization": "Bearer caller-token"}
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "accounts": [
                    {"customer_id": "2222222222", "login_customer_id": None, "status": "active"}
                ]
            },
        )

    async def test_accounts_rejects_wrong_audience_token(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            principal = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            TokenRepository(conn).save_access(
                AccessToken(
                    token="wrong-audience-token",
                    principal_id=principal.id,
                    client_id="https://client.example.com/metadata",
                    resource="https://other.example.com/mcp",
                    scope="adwords",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/accounts",
                    headers={"Authorization": "Bearer wrong-audience-token"},
                )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_token")

    async def test_proposals_list_filters_to_caller_and_optional_customer(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            principals = PrincipalRepository(conn)
            caller = principals.get_or_create("https://accounts.google.com", "caller")
            other = principals.get_or_create("https://accounts.google.com", "other")
            accounts = AdsAccountRepository(conn)
            accounts.link_account(caller.id, "1234567890", None)
            accounts.link_account(caller.id, "2222222222", None)
            proposals = ProposalRepository(conn)
            proposals.save(self._pending_proposal(proposal_id="proposal-1", principal_id=caller.id))
            proposals.save(
                self._pending_proposal(
                    proposal_id="proposal-2", principal_id=caller.id, customer_id="2222222222"
                )
            )
            proposals.save(self._pending_proposal(proposal_id="proposal-3", principal_id=other.id))
            proposals.save(
                self._pending_proposal(
                    proposal_id="proposal-4",
                    principal_id=caller.id,
                )
            )
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                all_response = await client.get(
                    "/api/v1/proposals",
                    headers={"Authorization": "Bearer caller-token"},
                )
                filtered_response = await client.get(
                    "/api/v1/proposals?customer_id=2222222222",
                    headers={"Authorization": "Bearer caller-token"},
                )

        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(
            [proposal["proposal_id"] for proposal in all_response.json()["proposals"]],
            ["proposal-1", "proposal-2", "proposal-4"],
        )
        self.assertEqual(filtered_response.status_code, 200)
        self.assertEqual(
            [proposal["proposal_id"] for proposal in filtered_response.json()["proposals"]],
            ["proposal-2"],
        )

    async def test_proposals_list_paginates_with_a_cursor_and_never_repeats_a_row(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            proposals = ProposalRepository(conn)
            proposals.save(self._pending_proposal(proposal_id="proposal-1", principal_id=caller.id))
            proposals.save(self._pending_proposal(proposal_id="proposal-2", principal_id=caller.id))
            proposals.save(self._pending_proposal(proposal_id="proposal-3", principal_id=caller.id))
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                first_page = await client.get(
                    "/api/v1/proposals?limit=2",
                    headers={"Authorization": "Bearer caller-token"},
                )
                self.assertEqual(first_page.status_code, 200)
                first_body = first_page.json()
                self.assertEqual(
                    [proposal["proposal_id"] for proposal in first_body["proposals"]],
                    ["proposal-1", "proposal-2"],
                )
                self.assertIn("next_cursor", first_body)

                second_page = await client.get(
                    f"/api/v1/proposals?limit=2&cursor={first_body['next_cursor']}",
                    headers={"Authorization": "Bearer caller-token"},
                )

        self.assertEqual(second_page.status_code, 200)
        second_body = second_page.json()
        self.assertEqual(
            [proposal["proposal_id"] for proposal in second_body["proposals"]], ["proposal-3"]
        )
        self.assertNotIn("next_cursor", second_body)

    async def test_proposals_list_rejects_cursor_reused_with_a_different_customer_filter(
        self,
    ) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            AdsAccountRepository(conn).link_account(caller.id, "1234567890", None)
            AdsAccountRepository(conn).link_account(caller.id, "2222222222", None)
            proposals = ProposalRepository(conn)
            proposals.save(self._pending_proposal(proposal_id="proposal-1", principal_id=caller.id))
            proposals.save(self._pending_proposal(proposal_id="proposal-2", principal_id=caller.id))
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                first_page = await client.get(
                    "/api/v1/proposals?limit=1",
                    headers={"Authorization": "Bearer caller-token"},
                )
                cursor = first_page.json()["next_cursor"]

                response = await client.get(
                    f"/api/v1/proposals?limit=1&customer_id=2222222222&cursor={cursor}",
                    headers={
                        "Authorization": "Bearer caller-token",
                        "X-Correlation-ID": "cursor-mismatch-1",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_cursor")
        self.assertEqual(response.json()["correlation_id"], "cursor-mismatch-1")

    async def test_proposals_list_rejects_a_tampered_cursor(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            proposals = ProposalRepository(conn)
            proposals.save(self._pending_proposal(proposal_id="proposal-1", principal_id=caller.id))
            proposals.save(self._pending_proposal(proposal_id="proposal-2", principal_id=caller.id))
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                first_page = await client.get(
                    "/api/v1/proposals?limit=1",
                    headers={"Authorization": "Bearer caller-token"},
                )
                tampered = _flip_last_byte(first_page.json()["next_cursor"])

                response = await client.get(
                    f"/api/v1/proposals?limit=1&cursor={tampered}",
                    headers={
                        "Authorization": "Bearer caller-token",
                        "X-Correlation-ID": "cursor-tampered-1",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_cursor")
        self.assertEqual(response.json()["correlation_id"], "cursor-tampered-1")

    async def test_proposals_list_rejects_unlinked_customer_filter(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            AdsAccountRepository(conn).link_account(caller.id, "1234567890", None)
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/proposals?customer_id=2222222222",
                    headers={
                        "Authorization": "Bearer caller-token",
                        "X-Correlation-ID": "proposal-filter-1",
                    },
                )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "account_not_linked")
        self.assertEqual(response.json()["correlation_id"], "proposal-filter-1")

    async def test_proposals_list_rejects_invalid_customer_filter(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/proposals?customer_id=123-456-7890",
                    headers={
                        "Authorization": "Bearer caller-token",
                        "X-Correlation-ID": "bad-customer-1",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_customer_id")
        self.assertEqual(response.json()["correlation_id"], "bad-customer-1")

    async def test_proposals_list_rejects_invalid_limit(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/proposals?limit=0",
                    headers={
                        "Authorization": "Bearer caller-token",
                        "X-Correlation-ID": "bad-limit-1",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_limit")
        self.assertEqual(response.json()["correlation_id"], "bad-limit-1")

    async def test_proposals_list_rejects_non_numeric_limit_with_problem_json(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            caller = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    "/api/v1/proposals?limit=not-a-number",
                    headers={
                        "Authorization": "Bearer caller-token",
                        "X-Correlation-ID": "bad-limit-2",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_limit")
        self.assertEqual(response.json()["correlation_id"], "bad-limit-2")

    async def test_proposal_detail_is_owner_scoped_and_marks_expired_for_read(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            conn = app.state.auth_context.conn
            principals = PrincipalRepository(conn)
            caller = principals.get_or_create("https://accounts.google.com", "caller")
            other = principals.get_or_create("https://accounts.google.com", "other")
            proposals = ProposalRepository(conn)
            proposals.save(
                self._pending_proposal(
                    proposal_id="expired-proposal",
                    principal_id=caller.id,
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            proposals.save(
                self._pending_proposal(proposal_id="other-proposal", principal_id=other.id)
            )
            self._save_access_token(app, caller.id)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                own_response = await client.get(
                    "/api/v1/proposals/expired-proposal",
                    headers={"Authorization": "Bearer caller-token"},
                )
                other_response = await client.get(
                    "/api/v1/proposals/other-proposal",
                    headers={"Authorization": "Bearer caller-token"},
                )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(own_response.json()["status"], "expired")
        self.assertEqual(other_response.status_code, 404)
        self.assertEqual(other_response.json()["code"], "proposal_not_found")

    async def test_proposal_detail_rejects_oversized_identifier_before_lookup(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            caller = PrincipalRepository(app.state.auth_context.conn).get_or_create(
                "https://accounts.google.com", "caller"
            )
            self._save_access_token(app, caller.id)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client:
                response = await client.get(
                    f"/api/v1/proposals/{'a' * 129}",
                    headers={"Authorization": "Bearer caller-token"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_proposal_id")


if __name__ == "__main__":
    unittest.main()
