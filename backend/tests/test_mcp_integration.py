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

import io
import json
import logging
import re
import sys
import unittest
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import grpc
import httpx
from backend.src.api.accounts import (
    FakeAccessibleCustomerService,
    FakeCustomerHierarchyService,
    GoogleAdsAccountDiscoveryClient,
)
from backend.src.api.reporting import FakeGoogleAdsSearchService, GoogleAdsReportingClient
from backend.src.app import create_app
from backend.src.auth.domain import AccessToken
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from backend.src.db.oauth_store import TokenRepository
from backend.src.db.proposals import AuditRepository
from backend.src.db.repository import (
    AdsAccountRepository,
    OAuthCredentialRepository,
    PrincipalRepository,
)
from backend.src.observability import JsonEventLogger
from cryptography.fernet import Fernet
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v24.errors.types import errors as error_types
from google.ads.googleads.v24.errors.types import quota_error
from google.ads.googleads.v24.services.types.google_ads_service import SearchGoogleAdsResponse
from mcp.client.streamable_http import streamable_http_client

from mcp import ClientSession


class _FakeRpcCall(grpc.Call, grpc.RpcError):
    def __init__(self, code: grpc.StatusCode) -> None:
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return "fake"


def _quota_exceeded_exception(*, request_id: str) -> GoogleAdsException:
    error = error_types.GoogleAdsError(
        error_code=error_types.ErrorCode(
            quota_error=quota_error.QuotaErrorEnum.QuotaError.RESOURCE_EXHAUSTED
        ),
        message="Too many requests",
    )
    failure = error_types.GoogleAdsFailure(errors=[error], request_id=request_id)
    call = _FakeRpcCall(grpc.StatusCode.INVALID_ARGUMENT)
    return GoogleAdsException(error=call, call=call, failure=failure, request_id=request_id)


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
        allowed_hosts=("connector.example.com",),
        cors_allowed_origins=(),
    )


@asynccontextmanager
async def _mcp_session(
    app,
    *,
    token: str,
    extra_headers: dict[str, str] | None = None,
    response_headers_sink: list[httpx.Headers] | None = None,
) -> AsyncIterator[ClientSession]:
    headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}

    async def _capture_response(response: httpx.Response) -> None:
        if response_headers_sink is not None:
            response_headers_sink.append(response.headers)

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url=PUBLIC_BASE_URL,
            headers=headers,
            timeout=30,
            event_hooks={"response": [_capture_response]},
        ) as client,
        streamable_http_client(f"{PUBLIC_BASE_URL}/mcp", http_client=client) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def _build_app(
        self,
        *,
        reporting_client: GoogleAdsReportingClient | None = None,
        account_discovery_client: GoogleAdsAccountDiscoveryClient | None = None,
        login_google_client: FakeGoogleOAuthClient | None = None,
        event_logger=None,
    ):
        settings = _settings()
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(),
            reporting_client=reporting_client,
            account_discovery_client=account_discovery_client,
            login_google_client=login_google_client,
            event_logger=event_logger,
        )
        return settings, app

    def _issue_access_token(
        self, app, settings: Settings, *, principal_id: str, token: str
    ) -> None:
        TokenRepository(app.state.auth_context.conn).save_access(
            AccessToken(
                token=token,
                principal_id=principal_id,
                client_id="https://client.example.com/metadata",
                resource=settings.mcp_resource_uri,
                scope="adwords",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    async def test_unauthenticated_request_gets_401_with_www_authenticate(self) -> None:
        settings, app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Accept": "application/json, text/event-stream"},
            )
        self.assertEqual(response.status_code, 401)
        www_authenticate = response.headers["www-authenticate"]
        self.assertIn("Bearer", www_authenticate)
        self.assertIn(
            f'resource_metadata="{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"',
            www_authenticate,
        )

    async def test_registered_tools_have_closed_schemas_and_readonly_annotations(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            tools = (await session.list_tools()).tools

        names = {tool.name for tool in tools}
        read_only_names = {
            "list_accessible_accounts",
            "get_campaign_performance",
            "get_ad_group_performance",
            "get_keyword_performance",
            "get_proposal",
            "list_proposals",
        }
        local_sync_names = {"sync_accessible_accounts"}
        local_write_names = {"prepare_proposal"}
        self.assertEqual(names, read_only_names | local_sync_names | local_write_names)
        open_world_names = {
            "get_campaign_performance",
            "get_ad_group_performance",
            "get_keyword_performance",
        } | local_sync_names
        for tool in tools:
            self.assertLessEqual(len(tool.name), 64)
            self.assertIsNotNone(tool.title)
            self.assertTrue(tool.description)
            self.assertIs(tool.inputSchema.get("additionalProperties"), False)
            self.assertNotIn("principal_id", tool.inputSchema.get("properties", {}))
            self.assertIsNotNone(tool.outputSchema)
            self._assert_object_schemas_are_closed(tool.outputSchema)
            self.assertFalse(tool.annotations.destructiveHint)
            if tool.name in read_only_names:
                self.assertTrue(tool.annotations.readOnlyHint)
                self.assertTrue(tool.annotations.idempotentHint)
            elif tool.name in local_sync_names:
                # Writes our own ads_account bookkeeping from a live Google Ads read,
                # so it is not readOnlyHint, but repeated calls converge to the same
                # local snapshot (idempotentHint) -- see tool_support.py::LOCAL_SYNC.
                self.assertFalse(tool.annotations.readOnlyHint)
                self.assertTrue(tool.annotations.idempotentHint)
            else:
                self.assertFalse(tool.annotations.readOnlyHint)
                self.assertFalse(tool.annotations.idempotentHint)
                self.assertEqual(tool.name, "prepare_proposal")
            self.assertEqual(tool.annotations.openWorldHint, tool.name in open_world_names)

    def _assert_object_schemas_are_closed(self, schema) -> None:  # noqa: ANN001
        """Recursively require every declared object shape to reject unknown fields."""
        if not isinstance(schema, dict):
            return
        if schema.get("type") == "object":
            self.assertIs(schema.get("additionalProperties"), False)
        for value in schema.values():
            if isinstance(value, dict):
                self._assert_object_schemas_are_closed(value)
            elif isinstance(value, list):
                for item in value:
                    self._assert_object_schemas_are_closed(item)

    async def test_call_tool_returns_linked_account_and_mapped_campaign_rows(self) -> None:
        fake_search = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[], next_page_token="")}
        )
        reporting_client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: fake_search
        )
        settings, app = self._build_app(reporting_client=reporting_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        vault_ref = vault.store("google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            accounts_result = await session.call_tool("list_accessible_accounts", {})
            campaign_result = await session.call_tool(
                "get_campaign_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                },
            )

        self.assertFalse(accounts_result.isError)
        self.assertIn('"customer_id": "1234567890"', accounts_result.content[0].text)
        self.assertEqual(
            accounts_result.structuredContent,
            {
                "schema_version": 1,
                "accounts": [
                    {
                        "customer_id": "1234567890",
                        "login_customer_id": None,
                        "status": "active",
                    }
                ],
                "warnings": [],
            },
        )
        self.assertFalse(campaign_result.isError)
        self.assertIn('"rows": []', campaign_result.content[0].text)
        self.assertEqual(
            campaign_result.structuredContent,
            {
                "schema_version": 1,
                "customer_id": "1234567890",
                "date_range": {"start_date": "2026-07-01", "end_date": "2026-07-10"},
                "rows": [],
                "next_page_token": None,
                "row_count": 0,
                "truncated": False,
                "warnings": [],
            },
        )
        self.assertEqual(len(fake_search.calls), 1)
        self.assertEqual(fake_search.calls[0]["customer_id"], "1234567890")

    async def test_reporting_continuation_is_signed_bound_and_unwraps_provider_token(self) -> None:
        fake_search = FakeGoogleAdsSearchService(
            pages_by_token={
                None: SearchGoogleAdsResponse(results=[], next_page_token="provider-page-2"),
                "provider-page-2": SearchGoogleAdsResponse(results=[], next_page_token=""),
            }
        )
        reporting_client = GoogleAdsReportingClient(
            search_service_factory=lambda _credentials: fake_search
        )
        settings, app = self._build_app(reporting_client=reporting_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        vault_ref = vault.store("google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            first = await session.call_tool(
                "get_campaign_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                },
            )
            continuation = first.structuredContent["next_page_token"]
            second = await session.call_tool(
                "get_campaign_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                    "page_token": continuation,
                },
            )
            wrong_report = await session.call_tool(
                "get_keyword_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                    "page_token": continuation,
                },
            )

        self.assertFalse(first.isError)
        self.assertIsInstance(continuation, str)
        self.assertNotIn("provider-page-2", continuation)
        self.assertFalse(second.isError)
        self.assertIsNone(second.structuredContent["next_page_token"])
        self.assertTrue(wrong_report.isError)
        self.assertIn("gecersiz", wrong_report.content[0].text.lower())
        self.assertEqual(
            [call["page_token"] for call in fake_search.calls], [None, "provider-page-2"]
        )

    async def test_sync_accessible_accounts_discovers_and_replaces_local_snapshot(self) -> None:
        """todo.md 5.1: direct access + one manager's hierarchy get linked, and a
        previously-linked account no longer discovered is marked disconnected --
        never deleted (docs/AUTH.md disconnect decision)."""
        direct = FakeAccessibleCustomerService(["customers/1234567890"])
        hierarchy = FakeCustomerHierarchyService(["1234567890", "2345678901"])
        discovery_client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: direct,
            hierarchy_service_factory=lambda _credentials, _manager_id: hierarchy,
        )
        settings, app = self._build_app(account_discovery_client=discovery_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "9999999999", None)
        vault_ref = vault.store("google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            result = await session.call_tool("sync_accessible_accounts", {})
            stale = AdsAccountRepository(conn).get_account(principal.id, "9999999999")

        self.assertFalse(result.isError)
        self.assertEqual(
            result.structuredContent,
            {
                "schema_version": 1,
                "accounts": [
                    {"customer_id": "1234567890", "login_customer_id": None, "status": "active"},
                    {
                        "customer_id": "2345678901",
                        "login_customer_id": "1234567890",
                        "status": "active",
                    },
                ],
                "warnings": [],
            },
        )
        assert stale is not None
        self.assertEqual(stale.status, "disconnected")

    async def test_sync_accessible_accounts_deactivates_credential_on_auth_failure(self) -> None:
        from google.auth.exceptions import RefreshError

        direct = FakeAccessibleCustomerService(raises=RefreshError("invalid_grant: revoked"))
        discovery_client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: direct
        )
        settings, app = self._build_app(account_discovery_client=discovery_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault
        oauth_credentials = OAuthCredentialRepository(conn)

        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        vault_ref = vault.store("google-refresh-token")
        oauth_credentials.upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            result = await session.call_tool("sync_accessible_accounts", {})
            self.assertIsNone(oauth_credentials.get_active(principal.id))

        self.assertTrue(result.isError)

    async def test_sync_accessible_accounts_is_scoped_to_the_callers_principal(self) -> None:
        direct = FakeAccessibleCustomerService(["customers/1234567890"])
        discovery_client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: direct,
            hierarchy_service_factory=lambda _credentials, manager_id: FakeCustomerHierarchyService(
                [manager_id]
            ),
        )
        settings, app = self._build_app(account_discovery_client=discovery_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        owner = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-owner")
        vault_ref = vault.store("owners-refresh-token")
        OAuthCredentialRepository(conn).upsert(owner.id, vault_ref, key_version=1)

        other = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-other")
        AdsAccountRepository(conn).link_account(other.id, "5555555555", None)
        other_token = "other-token"
        self._issue_access_token(app, settings, principal_id=other.id, token=other_token)

        async with (
            app.router.lifespan_context(app),
            _mcp_session(app, token=other_token) as session,
        ):
            result = await session.call_tool("sync_accessible_accounts", {})
            # The un-synced principal's pre-existing link must survive untouched.
            unaffected = AdsAccountRepository(conn).get_active_account(other.id, "5555555555")
            # `owner`'s credential -- never the caller's -- must be untouched by this call.
            owner_credential = OAuthCredentialRepository(conn).get_active(owner.id)

        self.assertTrue(result.isError)
        self.assertIn("yeniden baglanmaniz gerekiyor", result.content[0].text)
        self.assertIsNotNone(unaffected)
        self.assertIsNotNone(owner_credential)

    async def test_auth_class_tool_failure_deactivates_the_credential(self) -> None:
        """ERROR_HANDLING.md 'Auth' row (todo.md 3.6): a revoked/expired refresh token
        discovered mid-call must deactivate the credential, not just fail this one
        request -- proven here through the real MCP tool-call path, not just the
        underlying helper unit test (test_mcp_credentials.py)."""
        from google.auth.exceptions import RefreshError

        fake_search = FakeGoogleAdsSearchService(
            raises=RefreshError("invalid_grant: token has been revoked")
        )
        reporting_client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: fake_search
        )
        settings, app = self._build_app(reporting_client=reporting_client)
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault
        oauth_credentials = OAuthCredentialRepository(conn)

        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        vault_ref = vault.store("google-refresh-token")
        oauth_credentials.upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            first = await session.call_tool(
                "get_campaign_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                },
            )
            # A second call must never reach Google again -- the credential is
            # already deactivated, so it fails fast on our own DB check instead.
            second = await session.call_tool(
                "get_campaign_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                },
            )

            self.assertIsNone(oauth_credentials.get_active(principal.id))
            # Deactivation, not destruction -- the vault secret itself is untouched.
            self.assertEqual(vault.read(vault_ref), "google-refresh-token")

        self.assertTrue(first.isError)
        self.assertTrue(second.isError)
        self.assertIn("yeniden baglanmaniz gerekiyor", second.content[0].text)
        self.assertEqual(len(fake_search.calls), 1)

    async def test_google_ads_failure_logs_the_google_request_id(self) -> None:
        """todo.md 5.6: a Google Ads-origin failure must carry Google's own
        ``request_id`` into structured logging (never the raw exception text/
        payload), so support can correlate a user report with Google's side --
        proven end-to-end through the real MCP tool-call path."""
        fake_search = FakeGoogleAdsSearchService(
            raises=_quota_exceeded_exception(request_id="req-google-abc123")
        )
        reporting_client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: fake_search
        )
        stream = io.StringIO()
        logger = logging.getLogger(f"mcp-google-request-id-test-{id(self)}")
        logger.handlers = [logging.StreamHandler(stream)]
        logger.propagate = False
        logger.setLevel(logging.DEBUG)
        event_logger = JsonEventLogger(
            logger, service_version="0.1.0", environment="test", pseudonym_key=b"k" * 32
        )
        settings, app = self._build_app(
            reporting_client=reporting_client, event_logger=event_logger
        )
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        vault_ref = vault.store("google-refresh-token")
        OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            result = await session.call_tool(
                "get_campaign_performance",
                {
                    "customer_id": "1234567890",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                },
            )
            # The credential deactivated on AUTH failures is a different class here --
            # rate-limit is not AUTH, so the credential must remain active.
            still_active = OAuthCredentialRepository(conn).get_active(principal.id)

        self.assertTrue(result.isError)
        self.assertIsNotNone(still_active)
        events = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        failure_events = [
            event for event in events if event["operation"] == "google_ads_campaign_report"
        ]
        self.assertEqual(len(failure_events), 1)
        event = failure_events[0]
        self.assertEqual(event["outcome"], "failure")
        self.assertEqual(event["google_request_id"], "req-google-abc123")
        self.assertEqual(event["reason_code"], "quota_error.resource_exhausted")

    async def test_principal_cannot_read_another_principals_linked_account(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        owner = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-owner")
        AdsAccountRepository(conn).link_account(owner.id, "1234567890", None)
        vault_ref = vault.store("owners-refresh-token")
        OAuthCredentialRepository(conn).upsert(owner.id, vault_ref, key_version=1)

        attacker = PrincipalRepository(conn).get_or_create(
            "https://accounts.google.com", "sub-attacker"
        )
        attacker_token = "attacker-token"
        self._issue_access_token(app, settings, principal_id=attacker.id, token=attacker_token)

        async with (
            app.router.lifespan_context(app),
            _mcp_session(app, token=attacker_token) as session,
        ):
            results = [
                await session.call_tool(
                    tool_name,
                    {
                        "customer_id": "1234567890",
                        "start_date": "2026-07-01",
                        "end_date": "2026-07-10",
                    },
                )
                for tool_name in (
                    "get_campaign_performance",
                    "get_ad_group_performance",
                    "get_keyword_performance",
                )
            ]

        for result in results:
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

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
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
            fetched = await session.call_tool(
                "get_proposal", {"proposal_id": created["proposal_id"]}
            )

        self.assertEqual(created["status"], "pending_approval")
        self.assertEqual(created["customer_id"], "1234567890")
        self.assertEqual(created["payload"]["type"], "campaign_budget_update")
        self.assertFalse(fetched.isError)
        fetched_body = json.loads(fetched.content[0].text)
        self.assertEqual(fetched_body, created)

    async def test_get_proposal_reports_time_expired_pending_proposal(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        AdsAccountRepository(conn).link_account(principal.id, "1234567890", None)
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
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
            self.assertFalse(prepared.isError)
            created = json.loads(prepared.content[0].text)
            conn.execute(
                "UPDATE proposal SET expires_at = ? WHERE id = ?",
                (
                    (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                    created["proposal_id"],
                ),
            )
            conn.commit()
            fetched = await session.call_tool(
                "get_proposal", {"proposal_id": created["proposal_id"]}
            )

        self.assertFalse(fetched.isError)
        self.assertEqual(json.loads(fetched.content[0].text)["status"], "expired")

    async def test_list_proposals_returns_only_callers_pending_proposals(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        owner = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-owner")
        AdsAccountRepository(conn).link_account(owner.id, "1234567890", None)
        AdsAccountRepository(conn).link_account(owner.id, "2222222222", None)
        owner_token = "owner-token"
        self._issue_access_token(app, settings, principal_id=owner.id, token=owner_token)

        other = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-other")
        AdsAccountRepository(conn).link_account(other.id, "1234567890", None)
        other_token = "other-token"
        self._issue_access_token(app, settings, principal_id=other.id, token=other_token)

        async with app.router.lifespan_context(app):
            async with _mcp_session(app, token=owner_token) as session:
                first = await session.call_tool(
                    "prepare_proposal",
                    {
                        "customer_id": "1234567890",
                        "proposal_type": "campaign_pause",
                        "campaign_id": "5555",
                        "rationale": "Performans dusuk.",
                        "current_status": "ENABLED",
                    },
                )
                second = await session.call_tool(
                    "prepare_proposal",
                    {
                        "customer_id": "2222222222",
                        "proposal_type": "campaign_enable",
                        "campaign_id": "6666",
                        "rationale": "Kampanya yeniden acilmaya hazir.",
                        "current_status": "PAUSED",
                    },
                )
                owner_list = await session.call_tool("list_proposals", {})
                filtered_list = await session.call_tool(
                    "list_proposals", {"customer_id": "2222222222", "limit": 1}
                )

            async with _mcp_session(app, token=other_token) as session:
                other_list = await session.call_tool("list_proposals", {})

        self.assertFalse(first.isError)
        self.assertFalse(second.isError)
        self.assertFalse(owner_list.isError)
        self.assertFalse(filtered_list.isError)
        owner_body = json.loads(owner_list.content[0].text)
        self.assertEqual(
            [proposal["proposal_id"] for proposal in owner_body["proposals"]],
            [
                json.loads(first.content[0].text)["proposal_id"],
                json.loads(second.content[0].text)["proposal_id"],
            ],
        )
        filtered_body = json.loads(filtered_list.content[0].text)
        self.assertEqual(
            [proposal["proposal_id"] for proposal in filtered_body["proposals"]],
            [json.loads(second.content[0].text)["proposal_id"]],
        )
        self.assertFalse(other_list.isError)
        self.assertEqual(
            json.loads(other_list.content[0].text), {"proposals": [], "has_more": False}
        )

    async def test_list_proposals_rejects_unlinked_customer_filter(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            result = await session.call_tool("list_proposals", {"customer_id": "1234567890"})
            invalid_limit = await session.call_tool("list_proposals", {"limit": 101})

        self.assertTrue(result.isError)
        self.assertIn("baglanti", result.content[0].text.lower())
        self.assertTrue(invalid_limit.isError)
        self.assertIn("limit", invalid_limit.content[0].text)

    async def test_prepare_proposal_rejects_unlinked_customer_id(self) -> None:
        settings, app = self._build_app()
        conn = app.state.auth_context.conn
        principal = PrincipalRepository(conn).get_or_create("https://accounts.google.com", "sub-1")
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
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

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
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

        attacker = PrincipalRepository(conn).get_or_create(
            "https://accounts.google.com", "sub-attacker"
        )
        attacker_token = "attacker-token"
        self._issue_access_token(app, settings, principal_id=attacker.id, token=attacker_token)

        async with app.router.lifespan_context(app):
            async with _mcp_session(app, token=owner_token) as session:
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

            async with _mcp_session(app, token=attacker_token) as session:
                result = await session.call_tool("get_proposal", {"proposal_id": proposal_id})

        self.assertTrue(result.isError)
        self.assertIn("proposal_id", result.content[0].text)

    async def test_get_proposal_rejects_oversized_identifier(self) -> None:
        settings, app = self._build_app()
        principal = PrincipalRepository(app.state.auth_context.conn).get_or_create(
            "https://accounts.google.com", "sub-1"
        )
        token = "token-1"
        self._issue_access_token(app, settings, principal_id=principal.id, token=token)

        async with app.router.lifespan_context(app), _mcp_session(app, token=token) as session:
            result = await session.call_tool("get_proposal", {"proposal_id": "a" * 129})

        self.assertTrue(result.isError)
        self.assertIn("proposal_id 1-128", result.content[0].text)

    async def test_full_chain_connect_accounts_reporting_proposal_approval_disconnect(
        self,
    ) -> None:
        """todo.md Faz 13.2: local staging-equivalent of the whole product journey --
        connect -> accounts -> reporting -> proposal -> browser approval -> audit ->
        disconnect -- driven through the real MCP client SDK *and* the real HTTP
        approvals surface in one test, proving every hop actually threads a
        caller-supplied ``X-Correlation-Id`` end to end. No real staging/production
        infra exists yet (docs/OPERATIONS.md "Faz 13.1"), so this is the closest
        available proxy; it is also the first test to ever exercise correlation-id
        propagation through the mounted ``/mcp`` ASGI app specifically -- every other
        correlation-id test only covers the FastAPI-routed HTTP surface.
        """
        customer_id = "1112223333"
        direct = FakeAccessibleCustomerService([f"customers/{customer_id}"])
        hierarchy = FakeCustomerHierarchyService([])
        discovery_client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: direct,
            hierarchy_service_factory=lambda _credentials, _manager_id: hierarchy,
        )
        fake_search = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[], next_page_token="")}
        )
        reporting_client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: fake_search
        )
        settings, app = self._build_app(
            reporting_client=reporting_client,
            account_discovery_client=discovery_client,
            login_google_client=FakeGoogleOAuthClient(
                google_subject="sub-chain", email="chain-reviewer@example.com"
            ),
        )
        conn = app.state.auth_context.conn
        vault = app.state.auth_context.vault

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as browser,
        ):
            # -- Connect: the Claude/MCP-side OAuth grant that creates the principal
            # in the first place (the actual authorization_code+PKCE dance is proven
            # end to end by test_auth_authorization_flow_http.py; recreating it here
            # would just duplicate that coverage instead of adding new chain coverage).
            principal = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "sub-chain"
            )
            # The connector-level Google Ads credential normally comes from a separate
            # adwords-scope consent (test_auth_authorization_flow_http.py already
            # proves that dance); this test's focus is the chain from that point on.
            vault_ref = vault.store("google-refresh-token")
            OAuthCredentialRepository(conn).upsert(principal.id, vault_ref, key_version=1)
            mcp_token = "chain-mcp-token"
            self._issue_access_token(app, settings, principal_id=principal.id, token=mcp_token)

            # -- Accounts + Reporting + Proposal, all over the real MCP Streamable
            # HTTP transport, all carrying the same caller-supplied correlation id --
            mcp_responses: list[httpx.Headers] = []
            async with _mcp_session(
                app,
                token=mcp_token,
                extra_headers={"X-Correlation-Id": "chain-mcp-leg"},
                response_headers_sink=mcp_responses,
            ) as session:
                sync_result = await session.call_tool("sync_accessible_accounts", {})
                report_result = await session.call_tool(
                    "get_campaign_performance",
                    {
                        "customer_id": customer_id,
                        "start_date": "2026-07-01",
                        "end_date": "2026-07-10",
                    },
                )
                proposal_result = await session.call_tool(
                    "prepare_proposal",
                    {
                        "customer_id": customer_id,
                        "proposal_type": "campaign_pause",
                        "campaign_id": "5551001",
                        "rationale": "Faz 13.2 uctan uca zincir testi.",
                        "current_status": "ENABLED",
                    },
                )

            self.assertFalse(sync_result.isError)
            self.assertFalse(report_result.isError)
            self.assertFalse(proposal_result.isError)
            proposal_id = proposal_result.structuredContent["proposal_id"]
            # Every MCP-leg HTTP response (initialize, list/call_tool POSTs) must
            # echo the exact correlation id the client sent -- proving
            # ``CorrelationIdMiddleware`` really wraps the mounted MCP ASGI app,
            # not just the FastAPI-routed surface every other test covers.
            self.assertTrue(mcp_responses, "MCP session made no HTTP calls to inspect")
            for headers in mcp_responses:
                self.assertEqual(headers.get("x-correlation-id"), "chain-mcp-leg")

            # -- Browser leg: prove this browser belongs to the same principal
            # ("connect" already happened above; this is the separate, adwords-scope-
            # free login docs/AUTH.md requires for the /approvals surface) --
            login_redirect = await browser.get(
                "/login", headers={"X-Correlation-Id": "chain-browser-connect"}
            )
            self.assertEqual(login_redirect.status_code, 302)
            self.assertEqual(login_redirect.headers["x-correlation-id"], "chain-browser-connect")
            state = parse_qs(urlsplit(login_redirect.headers["location"]).query)["state"][0]
            callback = await browser.get(
                "/google/callback",
                params={"state": state, "code": "fake-code"},
                headers={"X-Correlation-Id": "chain-browser-connect"},
            )
            self.assertEqual(callback.status_code, 302)
            self.assertIn("web_session", callback.cookies)

            # -- Browser approval: same principal, a fresh browser-leg correlation id --
            page = await browser.get(
                "/approvals", headers={"X-Correlation-Id": "chain-browser-approve"}
            )
            self.assertEqual(page.status_code, 200)
            self.assertIn(customer_id, page.text)
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert match is not None
            csrf_token = match.group(1)

            decision = await browser.post(
                f"/approvals/{proposal_id}/decision",
                data={"decision": "approve", "csrf_token": csrf_token},
                headers={"X-Correlation-Id": "chain-browser-approve"},
            )
            self.assertEqual(decision.status_code, 302)
            self.assertEqual(decision.headers["x-correlation-id"], "chain-browser-approve")

            # -- Audit: the decision's own correlation id must be on the audit trail --
            events = AuditRepository(conn).list_for_principal(principal.id)
            decided = [e for e in events if e.event_type == "approval.decided"]
            self.assertEqual(len(decided), 1)
            self.assertEqual(decided[0].correlation_id, "chain-browser-approve")

            # -- Disconnect: closes the loop, same browser session --
            disconnect = await browser.post(
                "/disconnect",
                data={"csrf_token": csrf_token},
                headers={"X-Correlation-Id": "chain-browser-disconnect"},
            )
            self.assertEqual(disconnect.status_code, 302)
            self.assertEqual(disconnect.headers["location"], "/login")
            self.assertEqual(disconnect.headers["x-correlation-id"], "chain-browser-disconnect")
            self.assertIsNone(OAuthCredentialRepository(conn).get_active(principal.id))
            after_disconnect = await browser.get("/approvals")
            self.assertEqual(after_disconnect.status_code, 302)
            self.assertEqual(after_disconnect.headers["location"], "/login")


if __name__ == "__main__":
    unittest.main()
