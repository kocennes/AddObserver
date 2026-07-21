"""Contract tests for connector OAuth discovery documents (todo.md 3.1).

Covers RFC 9728 protected-resource metadata, RFC 8414 authorization-server metadata
and the HTTPS-in-production invariant they both depend on (``PUBLIC_BASE_URL``).
Cross-client/confused-deputy binding, PKCE mismatch and resource-audience rejection
are already covered end-to-end by ``test_auth_authorization_flow_http.py``; this file
only asserts the discovery *documents themselves* are spec-correct, since nothing
previously read their JSON body directly.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.app import create_app
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings

PUBLIC_BASE_URL = "https://connector.example.com"


def _settings(**overrides: object) -> Settings:
    base = Settings(
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
    return dataclasses.replace(base, **overrides)


class HttpsProductionInvariantTests(unittest.TestCase):
    """OAuth 2.1 / MCP Authorization require every AS endpoint to be served over HTTPS."""

    def test_non_local_environment_with_plain_http_base_url_fails_closed(self) -> None:
        settings = _settings(environment="staging", public_base_url="http://connector.example.com")

        with self.assertRaises(RuntimeError):
            create_app(settings, google_client=FakeGoogleOAuthClient())

    def test_local_environment_may_use_plain_http(self) -> None:
        settings = _settings(environment="local", public_base_url="http://localhost:8000")

        app = create_app(settings, google_client=FakeGoogleOAuthClient())

        self.assertIsNotNone(app)

    def test_non_local_environment_with_https_base_url_succeeds(self) -> None:
        settings = _settings(environment="staging")

        app = create_app(settings, google_client=FakeGoogleOAuthClient())

        self.assertIsNotNone(app)


class ProtectedResourceMetadataTests(unittest.IsolatedAsyncioTestCase):
    """RFC 9728 protected-resource metadata served by the MCP resource server."""

    async def test_root_document_matches_resource_uri_and_authorization_server(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-protected-resource")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["resource"], f"{PUBLIC_BASE_URL}/mcp")
        self.assertEqual(body["authorization_servers"], [PUBLIC_BASE_URL])
        self.assertEqual(body["bearer_methods_supported"], ["header"])

    async def test_path_suffixed_variant_returns_the_same_document(self) -> None:
        """RFC 9728 s3.1: a resource at ``/mcp`` may also be probed at the suffixed
        well-known URI."""
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            root = await client.get("/.well-known/oauth-protected-resource")
            suffixed = await client.get("/.well-known/oauth-protected-resource/mcp")

        self.assertEqual(suffixed.status_code, 200)
        self.assertEqual(suffixed.json(), root.json())

    async def test_document_is_not_cached_by_intermediaries(self) -> None:
        """Discovery documents get the same default no-store posture as every other public
        response (docs/SECURITY.md 'Girdi, cikti ve web guvenligi'); there is no reason a stale
        cached copy should outlive a resource/audience rotation."""
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-protected-resource")

        self.assertEqual(response.headers["cache-control"], "no-store")


class AuthorizationServerMetadataTests(unittest.IsolatedAsyncioTestCase):
    """RFC 8414 authorization-server metadata for the connector's own hand-rolled AS (ADR-0002)."""

    async def test_document_advertises_exact_issuer_and_endpoints(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["issuer"], PUBLIC_BASE_URL)
        self.assertEqual(body["authorization_endpoint"], f"{PUBLIC_BASE_URL}/authorize")
        self.assertEqual(body["token_endpoint"], f"{PUBLIC_BASE_URL}/token")

    async def test_pkce_s256_is_advertised_as_required(self) -> None:
        """MCP clients MUST refuse to proceed if this field is absent (MCP Authorization,
        'Authorization Code Protection')."""
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.json()["code_challenge_methods_supported"], ["S256"])

    async def test_supported_grant_and_response_types_match_the_implemented_flow(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-authorization-server")

        body = response.json()
        self.assertEqual(body["response_types_supported"], ["code"])
        self.assertCountEqual(
            body["grant_types_supported"], ["authorization_code", "refresh_token"]
        )
        # ADR-0002: no client_secret is ever accepted -- CIMD-identified public clients only.
        self.assertEqual(body["token_endpoint_auth_methods_supported"], ["none"])

    async def test_cimd_support_is_advertised(self) -> None:
        """ADR-0002: DCR is out of scope, CIMD is the only client-identification mechanism."""
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-authorization-server")

        self.assertIs(response.json()["client_id_metadata_document_supported"], True)
        self.assertNotIn("registration_endpoint", response.json())

    async def test_document_is_not_cached_by_intermediaries(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get("/.well-known/oauth-authorization-server")

        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
