"""End-to-end tests for the full connector OAuth AS chain (todo.md 3.3):
``/authorize`` -> ``/authorize/consent`` -> ``/google/callback`` -> ``/token``.

Unlike ``test_auth_domain.py`` (pure state-machine unit tests) and
``test_auth_server_http.py`` (the ``/authorize/consent`` CSRF leg only), this file
drives the *whole* transaction over the real ASGI app so the state/PKCE/redirect_uri/
resource binding, single-use authorization codes, cross-client (confused-deputy)
redemption and open-redirect rejection are proven the way a real client would exercise
them, not just at the pure-function level. Concurrent-redeem atomicity is a storage
concern already covered by
``test_oauth_store.py::ConcurrentAuthorizationCodeClaimTests`` -- ``AuthContext.conn``
is bound to a single thread (see ``auth/server.py``'s module docstring), so a true
multi-thread race requires independent connections, not this shared-app HTTP harness.
"""

from __future__ import annotations

import re
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from backend.src.app import create_app
from backend.src.auth.domain import compute_code_challenge, hash_token
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from backend.src.db.oauth_store import ClientGrantRepository
from backend.src.db.repository import OAuthCredentialRepository, PrincipalRepository
from cryptography.fernet import Fernet

PUBLIC_BASE_URL = "https://connector.example.com"
CLIENT_A_ID_URL = "https://client-a.example.com/oauth-client.json"
CLIENT_A_REDIRECT_URI = "https://client-a.example.com/callback"
CLIENT_B_ID_URL = "https://client-b.example.com/oauth-client.json"
CLIENT_B_REDIRECT_URI = "https://client-b.example.com/callback"
FAKE_IPS = {"client-a.example.com": "93.184.216.34", "client-b.example.com": "93.184.216.35"}
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')
TRANSACTION_RE = re.compile(r'name="transaction_id" value="([^"]+)"')

CLIENT_DOCS = {
    "client-a.example.com": {
        "client_id": CLIENT_A_ID_URL,
        "redirect_uris": [CLIENT_A_REDIRECT_URI],
        "token_endpoint_auth_method": "none",
    },
    "client-b.example.com": {
        "client_id": CLIENT_B_ID_URL,
        "redirect_uris": [CLIENT_B_REDIRECT_URI],
        "token_endpoint_auth_method": "none",
    },
}


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
    """Same DNS-rebinding-pinned shape as test_auth_server_http.py's fake CIMD host,
    extended to serve two distinct registered clients."""

    def _handler(request: httpx.Request) -> httpx.Response:
        host_header = request.headers.get("host")
        doc = CLIENT_DOCS.get(host_header or "")
        if doc is not None and request.url.host == FAKE_IPS.get(host_header or ""):
            return httpx.Response(200, json=doc)
        return httpx.Response(404)

    return httpx.MockTransport(_handler)


def _resolve(hostname: str) -> list[str]:
    return [FAKE_IPS[hostname]]


class AuthorizationFlowHttpTests(unittest.IsolatedAsyncioTestCase):
    def _build_app(self, *, google_client: FakeGoogleOAuthClient | None = None):
        settings = _settings()
        app = create_app(
            settings,
            google_client=google_client or FakeGoogleOAuthClient(),
            login_google_client=FakeGoogleOAuthClient(),
        )
        app.state.auth_context.http_client = httpx.Client(transport=_cimd_transport())
        app.state.auth_context.resolve = _resolve
        return app

    @staticmethod
    def _verifier(label: str) -> str:
        return f"a-code-verifier-that-is-long-enough-{label}-1234567890"

    async def _authorize(
        self,
        client: httpx.AsyncClient,
        *,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
        state: str,
        resource: str | None = None,
    ) -> httpx.Response:
        return await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": compute_code_challenge(code_verifier),
                "code_challenge_method": "S256",
                "resource": resource or f"{PUBLIC_BASE_URL}/mcp",
                "state": state,
            },
        )

    async def _approve(self, client: httpx.AsyncClient, page: httpx.Response) -> str:
        """Submit /authorize/consent 'approve'; return the transaction_id (the value
        server.py hands Google as its own opaque `state`, per
        ``build_authorization_url(state=transaction_id)``)."""
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
        assert response.status_code == 302
        google_url = response.headers["location"]
        assert google_url.startswith("https://accounts.google.com")
        echoed = parse_qs(urlsplit(google_url).query)["state"][0]
        assert echoed == txn_match.group(1)
        return txn_match.group(1)

    async def _redeem_via_google_callback(
        self, client: httpx.AsyncClient, *, transaction_id: str, google_code: str = "g-code-1"
    ) -> tuple[str, str]:
        """Drive /google/callback and return (our authorization_code, echoed client state)."""
        response = await client.get(
            "/google/callback", params={"state": transaction_id, "code": google_code}
        )
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        return query["code"][0], query["state"][0]

    async def test_full_flow_binds_client_state_and_issues_a_working_token_pair(self) -> None:
        app = self._build_app()
        verifier = self._verifier("full")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri=CLIENT_A_REDIRECT_URI,
                code_verifier=verifier,
                state="client-supplied-opaque-state",
            )
            self.assertEqual(page.status_code, 200)
            txn_id = await self._approve(client, page)

            auth_code, echoed_state = await self._redeem_via_google_callback(
                client, transaction_id=txn_id
            )
            # The *client's own* opaque state -- never our internal transaction_id --
            # must be what comes back on the redirect to the client.
            self.assertEqual(echoed_state, "client-supplied-opaque-state")

            token_response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": CLIENT_A_REDIRECT_URI,
                    "client_id": CLIENT_A_ID_URL,
                    "code_verifier": verifier,
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                },
            )
            self.assertEqual(token_response.status_code, 200)
            body = token_response.json()
            self.assertTrue(body["access_token"])
            self.assertTrue(body["refresh_token"])

    async def test_authorization_code_replay_at_token_endpoint_is_rejected(self) -> None:
        app = self._build_app()
        verifier = self._verifier("replay")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri=CLIENT_A_REDIRECT_URI,
                code_verifier=verifier,
                state="s1",
            )
            txn_id = await self._approve(client, page)
            auth_code, _ = await self._redeem_via_google_callback(client, transaction_id=txn_id)

            token_data = {
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": CLIENT_A_REDIRECT_URI,
                "client_id": CLIENT_A_ID_URL,
                "code_verifier": verifier,
                "resource": f"{PUBLIC_BASE_URL}/mcp",
            }
            first = await client.post("/token", data=token_data)
            self.assertEqual(first.status_code, 200)

            second = await client.post("/token", data=token_data)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.json()["error"], "invalid_grant")

    async def test_redeeming_another_clients_authorization_code_is_rejected(self) -> None:
        """Confused-deputy check: client B must not be able to redeem the code that was
        issued for client A's own transaction, even with its own (validly registered)
        redirect_uri/verifier -- the code is bound to client A at issuance time."""
        app = self._build_app()
        verifier_a = self._verifier("deputy-a")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri=CLIENT_A_REDIRECT_URI,
                code_verifier=verifier_a,
                state="s-a",
            )
            txn_id = await self._approve(client, page)
            auth_code, _ = await self._redeem_via_google_callback(client, transaction_id=txn_id)

            response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": CLIENT_B_REDIRECT_URI,
                    "client_id": CLIENT_B_ID_URL,
                    "code_verifier": self._verifier("deputy-b"),
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_client")

    async def test_pkce_verifier_mismatch_at_token_endpoint_is_rejected(self) -> None:
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri=CLIENT_A_REDIRECT_URI,
                code_verifier=self._verifier("pkce-real"),
                state="s1",
            )
            txn_id = await self._approve(client, page)
            auth_code, _ = await self._redeem_via_google_callback(client, transaction_id=txn_id)

            response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": CLIENT_A_REDIRECT_URI,
                    "client_id": CLIENT_A_ID_URL,
                    "code_verifier": self._verifier("pkce-wrong"),
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")

    async def test_open_redirect_to_unregistered_uri_is_rejected_at_authorize(self) -> None:
        """A ``redirect_uri`` outside the CIMD-registered set must fail closed *before*
        any transaction row or CSRF cookie is created -- there is no open redirect."""
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri="https://attacker.example.com/steal",
                code_verifier=self._verifier("open-redirect"),
                state="s1",
            )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("authorize_csrf", response.cookies)

    async def test_resource_mismatch_is_rejected_before_any_transaction_is_created(self) -> None:
        app = self._build_app()
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            response = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri=CLIENT_A_REDIRECT_URI,
                code_verifier=self._verifier("resource"),
                state="s1",
                resource="https://a-different-mcp-server.example.com/mcp",
            )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("authorize_csrf", response.cookies)

    async def test_expired_authorization_code_is_rejected_at_token_endpoint(self) -> None:
        """Zorunlu vaka: the code's TTL is enforced end-to-end, not only by the pure
        domain function -- here the stored row's ``expires_at`` is pushed into the past
        directly (the only way to observe a *past* expiry through the real /token route,
        since ``now`` inside the route is always the real wall clock)."""
        app = self._build_app()
        verifier = self._verifier("expiry")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            page = await self._authorize(
                client,
                client_id=CLIENT_A_ID_URL,
                redirect_uri=CLIENT_A_REDIRECT_URI,
                code_verifier=verifier,
                state="s1",
            )
            txn_id = await self._approve(client, page)
            auth_code, _ = await self._redeem_via_google_callback(client, transaction_id=txn_id)

            past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            app.state.auth_context.conn.execute(
                "UPDATE authorization_code SET expires_at = ? WHERE code_hash = ?",
                (past, hash_token(auth_code)),
            )
            app.state.auth_context.conn.commit()

            response = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": CLIENT_A_REDIRECT_URI,
                    "client_id": CLIENT_A_ID_URL,
                    "code_verifier": verifier,
                    "resource": f"{PUBLIC_BASE_URL}/mcp",
                },
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_grant")


class ScopeDenialAtGoogleCallbackTests(unittest.IsolatedAsyncioTestCase):
    """todo.md 3.6 -- 'scope denial': a user can approve some scopes on Google's
    multi-scope consent screen while declining others. The redirect still carries a
    successful ``code`` (this is not the ``error=`` branch), so it can only be caught
    after the exchange, by inspecting what was actually granted."""

    def _build_app(self, *, granted_scopes: tuple[str, ...] | None):
        settings = _settings()
        app = create_app(
            settings,
            google_client=FakeGoogleOAuthClient(granted_scopes=granted_scopes),
            login_google_client=FakeGoogleOAuthClient(),
        )
        app.state.auth_context.http_client = httpx.Client(transport=_cimd_transport())
        app.state.auth_context.resolve = _resolve
        return app

    @staticmethod
    def _verifier(label: str) -> str:
        return f"a-code-verifier-that-is-long-enough-{label}-1234567890"

    async def _authorize_and_approve(self, client: httpx.AsyncClient, verifier: str) -> str:
        page = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": CLIENT_A_ID_URL,
                "redirect_uri": CLIENT_A_REDIRECT_URI,
                "code_challenge": compute_code_challenge(verifier),
                "code_challenge_method": "S256",
                "resource": f"{PUBLIC_BASE_URL}/mcp",
                "state": "client-state",
            },
        )
        csrf_match = CSRF_RE.search(page.text)
        txn_match = TRANSACTION_RE.search(page.text)
        assert csrf_match is not None and txn_match is not None
        approval = await client.post(
            "/authorize/consent",
            data={
                "transaction_id": txn_match.group(1),
                "decision": "approve",
                "csrf_token": csrf_match.group(1),
            },
        )
        assert approval.status_code == 302
        return txn_match.group(1)

    async def test_partial_grant_without_adwords_is_treated_as_denied(self) -> None:
        app = self._build_app(granted_scopes=("openid", "email"))  # adwords declined
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            verifier = self._verifier("scope-denied")
            txn_id = await self._authorize_and_approve(client, verifier)

            response = await client.get(
                "/google/callback", params={"state": txn_id, "code": "g-code-1"}
            )

            conn = app.state.auth_context.conn
            self.assertIsNone(_principal_credential(conn, "google-sub-1"))
            principal = PrincipalRepository(conn).get("https://accounts.google.com", "google-sub-1")
            if principal is not None:
                self.assertFalse(
                    ClientGrantRepository(conn).has_active_grant(principal.id, CLIENT_A_ID_URL)
                )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertEqual(query["error"], ["access_denied"])
        self.assertEqual(query["state"], ["client-state"])

    async def test_full_grant_including_adwords_still_succeeds(self) -> None:
        """Regression guard: the new check must not reject the ordinary, unchanged
        case where Google's response simply omits ``scope`` (granted == requested,
        RFC 6749 s5.1) or reports it back verbatim."""
        app = self._build_app(
            granted_scopes=("openid", "email", "https://www.googleapis.com/auth/adwords")
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            verifier = self._verifier("scope-granted")
            txn_id = await self._authorize_and_approve(client, verifier)

            response = await client.get(
                "/google/callback", params={"state": txn_id, "code": "g-code-1"}
            )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertIn("code", query)
        self.assertNotIn("error", query)

    async def test_omitted_granted_scopes_is_treated_as_full_grant(self) -> None:
        """``granted_scopes=None`` (Google omitted ``scope`` in the token response)
        must mean 'identical to what was requested', not 'nothing was granted'."""
        app = self._build_app(granted_scopes=None)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            verifier = self._verifier("scope-omitted")
            txn_id = await self._authorize_and_approve(client, verifier)

            response = await client.get(
                "/google/callback", params={"state": txn_id, "code": "g-code-1"}
            )

        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.assertIn("code", query)
        self.assertNotIn("error", query)


def _principal_credential(conn, google_subject: str):
    principal = PrincipalRepository(conn).get("https://accounts.google.com", google_subject)
    if principal is None:
        return None
    return OAuthCredentialRepository(conn).get_active(principal.id)


if __name__ == "__main__":
    unittest.main()
