"""Lifecycle tests for the ASGI composition root."""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.app import (
    MAX_REQUEST_BODY_BYTES,
    RequestBodyLimitMiddleware,
    SECURITY_RESPONSE_HEADERS,
    create_app,
)
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings

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


class AppLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_streamed_oversized_request_without_content_length(self) -> None:
        sent_messages: list[dict[str, Any]] = []
        receive_messages = [
            {"type": "http.request", "body": b"x" * 5, "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ]

        async def receive() -> dict[str, Any]:
            return receive_messages.pop(0)

        async def send(message: dict[str, Any]) -> None:
            sent_messages.append(message)

        async def draining_app(scope, receive, send) -> None:
            while True:
                message = await receive()
                if message["type"] != "http.request" or not message.get("more_body", False):
                    break
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/drain",
            "raw_path": b"/drain",
            "query_string": b"",
            "headers": [(b"host", b"connector.example.com")],
            "client": ("127.0.0.1", 12345),
            "server": ("connector.example.com", 443),
            "root_path": "",
        }

        middleware = RequestBodyLimitMiddleware(draining_app, max_body_bytes=5)
        await middleware(scope, receive, send)

        response_start = next(message for message in sent_messages if message["type"] == "http.response.start")
        response_body = b"".join(
            message.get("body", b"") for message in sent_messages if message["type"] == "http.response.body"
        )
        self.assertEqual(response_start["status"], 413)
        self.assertIn(b"request_body_too_large", response_body)

    async def test_rejects_oversized_request_before_mcp_auth(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC_BASE_URL,
            ) as client:
                response = await client.post(
                    "/mcp",
                    content=b"x" * (MAX_REQUEST_BODY_BYTES + 1),
                    headers={"Accept": "application/json, text/event-stream"},
                )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "request_body_too_large")

    async def test_rejects_invalid_content_length(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC_BASE_URL,
            ) as client:
                request = client.build_request("POST", "/mcp", content=b"{}")
                request.headers["content-length"] = "not-a-number"
                request.headers["x-correlation-id"] = "test-correlation-1"
                response = await client.send(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        self.assertEqual(response.json()["code"], "invalid_content_length")
        self.assertEqual(response.headers["x-correlation-id"], "test-correlation-1")
        self.assertEqual(response.json()["correlation_id"], "test-correlation-1")

    async def test_health_and_readiness_endpoints(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC_BASE_URL,
            ) as client:
                health = await client.get("/healthz")
                ready = await client.get("/readyz")

                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json(), {"status": "ok"})
                self.assertEqual(ready.status_code, 200)
                self.assertEqual(ready.json(), {"status": "ok"})

                app.state.auth_context.conn.close()
                unavailable = await client.get("/readyz")
                self.assertEqual(unavailable.status_code, 503)
                self.assertEqual(unavailable.json(), {"status": "unavailable"})

    async def test_lifespan_closes_sqlite_connection(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())
        conn = app.state.auth_context.conn
        http_client = app.state.auth_context.http_client

        async with app.router.lifespan_context(app):
            conn.execute("SELECT 1").fetchone()
            self.assertFalse(http_client.is_closed)

        self.assertTrue(http_client.is_closed)
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1").fetchone()

    async def test_security_headers_are_attached(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC_BASE_URL,
            ) as client:
                response = await client.get("/healthz")

        for header, expected in SECURITY_RESPONSE_HEADERS.items():
            self.assertEqual(response.headers[header.decode("ascii")], expected.decode("ascii"))

    async def test_correlation_id_is_generated_or_preserved(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=PUBLIC_BASE_URL,
            ) as client:
                generated = await client.get("/healthz")
                preserved = await client.get("/healthz", headers={"X-Correlation-ID": "client.id-123"})
                sanitized = await client.get("/healthz", headers={"X-Correlation-ID": "bad value with spaces"})

        self.assertRegex(generated.headers["x-correlation-id"], r"^[A-Za-z0-9._-]{1,128}$")
        self.assertEqual(preserved.headers["x-correlation-id"], "client.id-123")
        self.assertRegex(sanitized.headers["x-correlation-id"], r"^[A-Za-z0-9._-]{1,128}$")
        self.assertNotEqual(sanitized.headers["x-correlation-id"], "bad value with spaces")


if __name__ == "__main__":
    unittest.main()
