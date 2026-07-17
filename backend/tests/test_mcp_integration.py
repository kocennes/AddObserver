"""End-to-end MCP integration tests, driven through the real MCP client SDK.

Runs the actual Streamable HTTP protocol (initialize -> list_tools ->
call_tool) against ``backend.src.app.create_app`` over ``httpx.ASGITransport``
-- no real socket, but no hand-rolled protocol either (docs/TESTING.md mock
policy: exercise the real client, not an invented shape). Covers the
zorunlu güvenlik vakaları this feature is actually responsible for
(TESTING.md #3, #11): unauthenticated access, closed schemas, and
cross-principal isolation through the full stack.
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from cryptography.fernet import Fernet
from google.ads.googleads.v24.services.types.google_ads_service import SearchGoogleAdsResponse
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from backend.src.api.reporting import FakeGoogleAdsSearchService, GoogleAdsReportingClient
from backend.src.app import create_app
from backend.src.auth.domain import AccessToken
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from backend.src.db.oauth_store import TokenRepository
from backend.src.db.repository import AdsAccountRepository, OAuthCredentialRepository, PrincipalRepository

PUBLIC_BASE_URL = "https://connector.example.com"


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
    )


def _http_client_factory(app):
    def factory(headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=PUBLIC_BASE_URL,
            headers=dict(headers or {}),
            timeout=timeout or 30,
        )

    return factory


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _build_app(self, *, reporting_client: GoogleAdsReportingClient | None = None):
        settings = _settings()
        app = create_app(settings, google_client=FakeGoogleOAuthClient(), reporting_client=reporting_client)
        return settings, app

    def _issue_access_token(self, app, settings: Settings, *, principal_id: str, token: str) -> None:
        TokenRepository(app.state.auth_context.conn).save_access(
            AccessToken(
                token=token,
                principal_id=principal_id,
                client_id="https://client.example.com/metadata",
                resource=settings.mcp_resource_uri,
                scope="adwords",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )

    async def test_unauthenticated_request_gets_401_with_www_authenticate(self) -> None:
        settings, app = self._build_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL) as client:
                response = await client.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Accept": "application/json, text/event-stream"},
                )
        self.assertEqual(response.status_code, 401)
        www_authenticate = response.headers["www-authenticate"]
        self.assertIn("Bearer", www_authenticate)
        self.assertIn(
            f'resource_metadata="{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"', www_authenticate
        )

    async def test_registered_tools_have_closed_schemas_and_readonly_annotations(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = (await session.list_tools()).tools

        names = {tool.name for tool in tools}
        read_only_names = {
            "list_accessible_accounts",
            "get_campaign_performance",
            "get_ad_group_performance",
            "get_keyword_performance",
            "get_proposal",
        }
        local_write_names = {"prepare_proposal"}
        self.assertEqual(names, read_only_names | local_write_names)
        for tool in tools:
            self.assertLessEqual(len(tool.name), 64)
            self.assertIsNotNone(tool.title)
            self.assertIs(tool.inputSchema.get("additionalProperties"), False)
            self.assertFalse(tool.annotations.destructiveHint)
            if tool.name in read_only_names:
                self.assertTrue(tool.annotations.readOnlyHint)
            else:
                self.assertFalse(tool.annotations.readOnlyHint)

    async def test_call_tool_returns_linked_account_and_mapped_campaign_rows(self) -> None:
        fake_search = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[], next_page_token="")}
        )
        reporting_client = GoogleAdsReportingClient(search_service_factory=lambda creds: fake_search)
        settings, app = self._build_app(reporting_client=reporting_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        vault_ref = vault.store("google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    accounts_result = await session.call_tool("list_accessible_accounts", {})
                    campaign_result = await session.call_tool(
                        "get_campaign_performance",
                        {"customer_id": "1234567890", "start_date": "2026-07-01", "end_date": "2026-07-10"},
                    )

        self.assertFalse(accounts_result.isError)
        self.assertIn('"customer_id": "1234567890"', accounts_result.content[0].text)
        self.assertFalse(campaign_result.isError)
        self.assertIn('"rows": []', campaign_result.content[0].text)
        self.assertEqual(len(fake_search.calls), 1)
        self.assertEqual(fake_search.calls[0]["customer_id"], "1234567890")

    async def test_principal_cannot_read_another_principals_linked_account(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        owner = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-owner")
        AdsAccountRepository(conn).link_account(owner.id, "1234567890", None)
        vault_ref = vault.store("owners-refresh-token")
        OAuthCredentialRepository(conn).upsert(owner.id, vault_ref, key_version=1)

        attacker = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-attacker")
        attacker_token = "attacker-token"
        self._issue_access_token(app, settings, principal_id=attacker.id, token=attacker_token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {attacker_token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "get_campaign_performance",
                        {"customer_id": "1234567890", "start_date": "2026-07-01", "end_date": "2026-07-10"},
                    )

        self.assertTrue(result.isError)
        text = result.content[0].text
        self.assertIn("baglanti", text.lower())
        self.assertNotIn("owners-refresh-token", text)

    async def test_prepare_proposal_round_trips_through_get_proposal(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    prepared = await session.call_tool(
                        "prepare_proposal",
                        {
                            "customer_id": "1234567890",
                            "proposal_type": "campaign_budget_update",
                            "campaign_id": "5555",
                            "rationale": "Son 30 gunde ROAS hedefin uzerinde, butce artisi oneriliyor.",
                            "current_budget_amount_micros": 5_000_000,
                            "proposed_budget_amount_micros": 8_000_000,
                        },
                    )
                    self.assertFalse(prepared.isError)
                    created = json.loads(prepared.content[0].text)
                    fetched = await session.call_tool("get_proposal", {"proposal_id": created["proposal_id"]})

        self.assertEqual(created["status"], "pending_approval")
        self.assertEqual(created["customer_id"], "1234567890")
        self.assertEqual(created["payload"]["type"], "campaign_budget_update")
        self.assertFalse(fetched.isError)
        fetched_body = json.loads(fetched.content[0].text)
        self.assertEqual(fetched_body, created)

    async def test_prepare_proposal_rejects_unlinked_customer_id(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "prepare_proposal",
                        {
                            "customer_id": "1234567890",
                            "proposal_type": "campaign_pause",
                            "campaign_id": "5555",
                            "rationale": "Kampanya butcesini asiyor.",
                            "current_status": "ENABLED",
                        },
                    )

        self.assertTrue(result.isError)
        self.assertIn("baglanti", result.content[0].text.lower())

    async def test_prepare_proposal_rejects_out_of_allowlist_type(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "prepare_proposal",
                        {
                            "customer_id": "1234567890",
                            "proposal_type": "create_new_campaign",
                            "campaign_id": "5555",
                            "rationale": "Yeni kampanya olustur.",
                        },
                    )

        self.assertTrue(result.isError)
        self.assertIn("proposal_type", result.content[0].text)

    async def test_get_proposal_is_not_visible_to_another_principal(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        owner = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-owner")
        AdsAccountRepository(conn).link_account(owner.id, "1234567890", None)
        owner_token = "owner-token"
        self._issue_access_token(app, settings, principal_id=owner.id, token=owner_token)

        attacker = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-attacker")
        attacker_token = "attacker-token"
        self._issue_access_token(app, settings, principal_id=attacker.id, token=attacker_token)

        async with app.router.lifespan_context(app):
            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {owner_token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    prepared = await session.call_tool(
                        "prepare_proposal",
                        {
                            "customer_id": "1234567890",
                            "proposal_type": "campaign_pause",
                            "campaign_id": "5555",
                            "rationale": "Performans dusuk.",
                            "current_status": "ENABLED",
                        },
                    )
            proposal_id = json.loads(prepared.content[0].text)["proposal_id"]

            async with streamablehttp_client(
                f"{PUBLIC_BASE_URL}/mcp",
                headers={"Authorization": f"Bearer {attacker_token}"},
                httpx_client_factory=_http_client_factory(app),
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("get_proposal", {"proposal_id": proposal_id})

        self.assertTrue(result.isError)
        self.assertIn("proposal_id", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
