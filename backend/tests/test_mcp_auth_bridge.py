"""ASGI-level tests for PrincipalAuthMiddleware (docs/AUTH.md, TESTING.md #11).

Drives the middleware directly with hand-built ASGI ``scope``/``receive``/
``send`` primitives -- no real server or MCP client needed to prove the
401 + ``WWW-Authenticate`` contract and the state hand-off to the wrapped app.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.domain import AccessToken
from backend.src.db.connection import connect
from backend.src.db.oauth_store import TokenRepository
from backend.src.db.repository import PrincipalRepository
from backend.src.mcp.auth_bridge import PrincipalAuthMiddleware

EXPECTED_RESOURCE = "https://connector.example.com/mcp"
METADATA_URL = "https://connector.example.com/.well-known/oauth-protected-resource"


def _http_scope(*, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers or [],
        "query_string": b"",
    }


async def _empty_receive() -> dict:
    return {"type": "http.request", "body": b"", "more_body": False}


class _RecordingSend:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        for message in self.messages:
            if message["type"] == "http.response.start":
                return message["status"]
        return None

    def header(self, name: bytes) -> bytes | None:
        for message in self.messages:
            if message["type"] == "http.response.start":
                for key, value in message["headers"]:
                    if key.lower() == name.lower():
                        return value
        return None


class PrincipalAuthMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principal = PrincipalRepository(self.conn).get_or_create(
            "https://accounts.google.com", "sub-1"
        )
        self.tokens = TokenRepository(self.conn)

    def _middleware(self, downstream) -> PrincipalAuthMiddleware:
        return PrincipalAuthMiddleware(
            downstream,
            tokens_factory=lambda: TokenRepository(self.conn),
            expected_resource=EXPECTED_RESOURCE,
            protected_resource_metadata_url=METADATA_URL,
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _save_token(
        self, raw_token: str, *, resource: str = EXPECTED_RESOURCE, expired: bool = False
    ) -> None:
        expires_at = datetime.now(UTC) + (timedelta(minutes=-5) if expired else timedelta(hours=1))
        self.tokens.save_access(
            AccessToken(
                token=raw_token,
                principal_id=self.principal.id,
                client_id="https://client.example.com/metadata",
                resource=resource,
                scope="adwords",
                expires_at=expires_at,
            )
        )

    async def test_missing_authorization_header_is_401_with_www_authenticate(self) -> None:
        called = {"downstream": False}

        async def downstream(scope, receive, send):
            called["downstream"] = True

        middleware = self._middleware(downstream)
        send = _RecordingSend()
        await middleware(_http_scope(), _empty_receive, send)

        self.assertFalse(called["downstream"])
        self.assertEqual(send.status, 401)
        www_authenticate = send.header(b"www-authenticate").decode()
        self.assertIn("Bearer", www_authenticate)
        self.assertIn(f'resource_metadata="{METADATA_URL}"', www_authenticate)

    async def test_unknown_token_is_401(self) -> None:
        async def downstream(scope, receive, send):
            raise AssertionError("downstream must not run for an unknown token")

        middleware = self._middleware(downstream)
        send = _RecordingSend()
        headers = [(b"authorization", b"Bearer not-a-real-token")]
        await middleware(_http_scope(headers=headers), _empty_receive, send)

        self.assertEqual(send.status, 401)

    async def test_expired_token_is_401(self) -> None:
        self._save_token("expired-token", expired=True)

        async def downstream(scope, receive, send):
            raise AssertionError("downstream must not run for an expired token")

        middleware = self._middleware(downstream)
        send = _RecordingSend()
        headers = [(b"authorization", b"Bearer expired-token")]
        await middleware(_http_scope(headers=headers), _empty_receive, send)

        self.assertEqual(send.status, 401)

    async def test_token_for_a_different_resource_is_401(self) -> None:
        self._save_token("wrong-resource-token", resource="https://other-connector.example.com/mcp")

        async def downstream(scope, receive, send):
            raise AssertionError("downstream must not run for a wrong-audience token")

        middleware = self._middleware(downstream)
        send = _RecordingSend()
        headers = [(b"authorization", b"Bearer wrong-resource-token")]
        await middleware(_http_scope(headers=headers), _empty_receive, send)

        self.assertEqual(send.status, 401)

    async def test_valid_token_stashes_principal_and_calls_downstream(self) -> None:
        self._save_token("good-token")
        seen_scope: dict = {}

        async def downstream(scope, receive, send):
            seen_scope.update(scope)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = self._middleware(downstream)
        send = _RecordingSend()
        headers = [(b"authorization", b"Bearer good-token")]
        await middleware(_http_scope(headers=headers), _empty_receive, send)

        self.assertEqual(send.status, 200)
        principal = seen_scope["state"]["principal"]
        self.assertEqual(principal.principal_id, self.principal.id)

    async def test_non_http_scope_passes_through_untouched(self) -> None:
        called = {"downstream": False}

        async def downstream(scope, receive, send):
            called["downstream"] = True

        middleware = self._middleware(downstream)
        await middleware({"type": "lifespan"}, _empty_receive, _RecordingSend())

        self.assertTrue(called["downstream"])

    async def test_postgres_auth_transaction_closes_before_downstream_tool_runs(self) -> None:
        token = AccessToken(
            token="",
            principal_id=self.principal.id,
            client_id="client-1",
            resource=EXPECTED_RESOURCE,
            scope="adwords",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )

        class Tokens:
            def get_access(self, raw_token: str) -> AccessToken | None:
                return token if raw_token == "postgres-token" else None

        class Work:
            def __init__(self) -> None:
                self.repositories = SimpleNamespace(tokens=Tokens())
                self.bootstraps: list[str] = []
                self.exited = False

            def __enter__(self):  # noqa: ANN204
                return self

            def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
                self.exited = True

            def bootstrap_access_token(self, raw_token: str) -> str:
                self.bootstraps.append(raw_token)
                return token.principal_id

        work = Work()

        class Factory:
            def request(self) -> Work:
                return work

        async def downstream(scope, receive, send):
            self.assertTrue(work.exited, "DB transaction must close before any MCP tool runs")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = PrincipalAuthMiddleware(
            downstream,
            tokens_factory=lambda: self.tokens,
            expected_resource=EXPECTED_RESOURCE,
            protected_resource_metadata_url=METADATA_URL,
            postgres_uow_factory=Factory(),  # pyright: ignore[reportArgumentType]
        )
        send = _RecordingSend()
        headers = [(b"authorization", b"Bearer postgres-token")]

        await middleware(_http_scope(headers=headers), _empty_receive, send)

        self.assertEqual(send.status, 200)
        self.assertEqual(work.bootstraps, ["postgres-token"])


if __name__ == "__main__":
    unittest.main()
