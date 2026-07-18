"""End-to-end tests for the connector OAuth AS's ``/authorize`` -> ``/authorize/consent``
leg (docs/AUTH.md "Saldiri kontrolleri" -- "... ve account-linking CSRF test edilir").

Drives the real ASGI app over httpx.ASGITransport, mirroring the harness in
test_approvals_http.py. The CIMD client_id fetch is faked at the transport level
(``app.state.auth_context.http_client``) and DNS resolution is faked
(``app.state.auth_context.resolve``) so no real network call happens -- this mirrors
docs/TESTING.md's mock policy and lets the test drive a client_id/redirect_uri an
attacker could equally register for themselves.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from backend.src.app import create_app
from backend.src.auth.domain import compute_code_challenge
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from cryptography.fernet import Fernet

PUBLIC_BASE_URL = "https://connector.example.com"
CLIENT_ID_URL = "https://client.example.com/oauth-client.json"
CLIENT_REDIRECT_URI = "https://client.example.com/callback"
FAKE_PUBLIC_IP = "93.184.216.34"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
TRANSACTION_RE = re.compile(r'name="transaction_id" value="([^"]+)"')


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


def _cimd_transport() -> httpx.MockTransport:
    """The CIMD fetch pins its TCP target to the resolved IP (DNS-rebinding TOCTOU
    guard, see backend/src/auth/cimd.py), so the request URL's host is
    ``FAKE_PUBLIC_IP`` -- the original hostname only survives in the ``Host``
    header and the ``sni_hostname`` extension.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        if (
            request.url.host == FAKE_PUBLIC_IP
            and request.headers.get("host") == "client.example.com"
        ):
            return httpx.Response(
                200,
                json={
                    "client_id": CLIENT_ID_URL,
                    "redirect_uris": [CLIENT_REDIRECT_URI],
                    "token_endpoint_auth_method": "none",
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(_handler)


class AuthorizeConsentCsrfTests(unittest.IsolatedAsyncioTestCase):
    def _build_app(self):
        settings = _settings()
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(),
            login_google_client=FakeGoogleOAuthClient(),
        )
        # Fake the CIMD network fetch and its SSRF DNS check -- no real network I/O
        # (docs/TESTING.md mock policy); the app object stores a mutable AuthContext.
        app.state.auth_context.http_client = httpx.Client(transport=_cimd_transport())
        app.state.auth_context.resolve = lambda hostname: [FAKE_PUBLIC_IP]
        return app

    async def _get_authorize(
        self, client: httpx.AsyncClient, *, state: str = "client-state-1"
    ) -> httpx.Response:
        code_challenge = compute_code_challenge("a-code-verifier-that-is-long-enough-1234567890")
        return await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": CLIENT_ID_URL,
                "redirect_uri": CLIENT_REDIRECT_URI,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "resource": f"{PUBLIC_BASE_URL}/mcp",
                "state": state,
            },
        )

    async def test_happy_path_consent_with_matching_cookie_redirects_to_google(self) -> None:
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._get_authorize(client)
            self.assertEqual(page.status_code, 200)
            self.assertIn("authorize_csrf", page.cookies)
            csrf_match = CSRF_RE.search(page.text)
            txn_match = TRANSACTION_RE.search(page.text)
            assert csrf_match is not None and txn_match is not None

            response = await client.post(
                "/authorize/consent",
                data={
                    "transaction_id": txn_match.group(1),
                    "decision": "approve",
                    "csrf_token": csrf_match.group(1),
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.headers["location"].startswith("https://accounts.google.com"))

    async def test_consent_without_csrf_cookie_is_rejected(self) -> None:
        """The account-linking CSRF docs/AUTH.md names: an attacker who legitimately
        created the transaction (and so knows transaction_id/csrf_token) forges a
        cross-site POST that a victim's browser submits -- the victim's browser never
        loaded this attacker's /authorize page, so it never received the
        SameSite=Strict cookie, and the decision must be rejected."""
        app = self._build_app()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as attacker_client:
                page = await self._get_authorize(attacker_client)
                csrf_match = CSRF_RE.search(page.text)
                txn_match = TRANSACTION_RE.search(page.text)
                assert csrf_match is not None and txn_match is not None

            # A separate client with no cookie jar overlap models the victim's browser.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as victim_client:
                forged = await victim_client.post(
                    "/authorize/consent",
                    data={
                        "transaction_id": txn_match.group(1),
                        "decision": "approve",
                        "csrf_token": csrf_match.group(1),
                    },
                )
        self.assertEqual(forged.status_code, 400)

    async def test_oversized_transaction_id_is_rejected_before_lookup(self) -> None:
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.post(
                "/authorize/consent",
                data={
                    "transaction_id": "a" * 200,
                    "decision": "approve",
                    "csrf_token": "irrelevant",
                },
            )
        self.assertEqual(response.status_code, 400)

    async def test_oversized_authorize_client_id_is_rejected(self) -> None:
        """A megabyte-scale ``client_id`` must fail fast, before any DNS/network work."""
        app = self._build_app()
        code_challenge = compute_code_challenge("a-code-verifier-that-is-long-enough-1234567890")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": "https://client.example.com/" + "x" * 3000,
                    "redirect_uri": CLIENT_REDIRECT_URI,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                    "state": "client-state-1",
                },
            )
        self.assertEqual(response.status_code, 400)

    async def test_oversized_google_callback_state_is_rejected(self) -> None:
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.get(
                "/google/callback", params={"state": "a" * 200, "code": "some-code"}
            )
        self.assertEqual(response.status_code, 400)

    async def test_consent_with_wrong_csrf_cookie_is_rejected(self) -> None:
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._get_authorize(client)
            txn_match = TRANSACTION_RE.search(page.text)
            assert txn_match is not None

            client.cookies.set("authorize_csrf", "tampered-value", domain="connector.example.com")
            response = await client.post(
                "/authorize/consent",
                data={
                    "transaction_id": txn_match.group(1),
                    "decision": "approve",
                    "csrf_token": "tampered-value",
                },
            )
        self.assertEqual(response.status_code, 400)

    async def test_consent_cookie_from_a_different_transaction_is_rejected(self) -> None:
        """A cookie that is valid for *some* transaction must not authorize a decision
        on a *different* transaction_id -- the cookie is checked against the specific
        transaction being decided, not merely "is this cookie known to us"."""
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            first_page = await self._get_authorize(client, state="state-a")
            first_txn = TRANSACTION_RE.search(first_page.text)
            assert first_txn is not None

            # A second /authorize call overwrites the browser's authorize_csrf
            # cookie with the *second* transaction's value.
            await self._get_authorize(client, state="state-b")

            # Decide the *first* transaction_id while the cookie jar now only
            # holds the second transaction's csrf value.
            response = await client.post(
                "/authorize/consent",
                data={
                    "transaction_id": first_txn.group(1),
                    "decision": "approve",
                    "csrf_token": "irrelevant-the-cookie-is-what-is-checked",
                },
            )
        self.assertEqual(response.status_code, 400)


class TokenContentTypeTests(unittest.IsolatedAsyncioTestCase):
    """``/token`` is an OAuth 2.0 endpoint: RFC 6749 mandates
    ``application/x-www-form-urlencoded`` bodies, never JSON. A wrong Content-Type must
    fail closed with no stack trace, SQL, or secret in the response (docs/SECURITY.md
    "Hata cevaplari secret, SQL, stack trace ... acikca cikarmaz")."""

    async def test_json_content_type_is_rejected_without_leaking_internals(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await client.post(
                "/token",
                content=b'{"grant_type": "refresh_token", "refresh_token": "x"}',
                headers={"content-type": "application/json"},
            )
        self.assertIn(response.status_code, (400, 422))
        body = response.text.lower()
        for leak in ("traceback", "sqlite3", "select ", "client-secret", "dev-token"):
            self.assertNotIn(leak, body)


if __name__ == "__main__":
    unittest.main()
