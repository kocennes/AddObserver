"""ASGI bridge between the connector's OAuth resource-server checks and MCP.

FastMCP has its own built-in ``auth``/``token_verifier`` wiring, but plugging
into it would duplicate the RFC 9728 protected-resource metadata already
served at the application root by ``backend.src.auth.server`` (ADR-0001 --
Authlib lacks RFC 9728 support, so that endpoint is hand-written there and is
the single source of truth). Instead, this module wraps the *plain*,
auth-disabled MCP ASGI app with one small middleware that reuses the exact
same verification path as every other protected route
(``backend.src.auth.deps``), then stashes the result on the ASGI ``scope`` so
tool handlers can read it back through ``Context.request_context.request``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..auth.deps import (
    AuthenticatedPrincipal,
    BearerTokenError,
    extract_bearer_token,
    verify_access_token,
    www_authenticate_header,
)
from ..db.oauth_store import TokenRepository


class PrincipalAuthMiddleware:
    """Requires a valid, audience-bound connector access token on every request.

    ``tokens_factory`` builds a fresh ``TokenRepository`` per request rather
    than reusing one instance -- the class itself is a stateless wrapper
    around the shared connection, so this costs nothing and keeps request
    handling free of any cross-request mutable state.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        tokens_factory: Callable[[], TokenRepository],
        expected_resource: str,
        protected_resource_metadata_url: str,
    ) -> None:
        self._app = app
        self._tokens_factory = tokens_factory
        self._expected_resource = expected_resource
        self._metadata_url = protected_resource_metadata_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request = Request(scope, receive)
        raw_token = extract_bearer_token(request.headers.get("authorization"))
        header = www_authenticate_header(
            protected_resource_metadata_url=self._metadata_url,
            error="invalid_token" if raw_token else None,
        )
        if raw_token is None:
            await self._deny(scope, receive, send, header, "Authentication required.")
            return

        try:
            principal = verify_access_token(
                raw_token,
                self._tokens_factory(),
                expected_resource=self._expected_resource,
                now=datetime.now(timezone.utc),
            )
        except BearerTokenError as error:
            await self._deny(scope, receive, send, header, error.description)
            return

        # Same scope dict flows through every downstream ASGI call, including
        # the one FastMCP later builds its own ``Request`` from -- Starlette's
        # ``request.state`` wraps ``scope["state"]`` by reference, not a copy.
        scope.setdefault("state", {})["principal"] = principal
        await self._app(scope, receive, send)

    @staticmethod
    async def _deny(scope: Scope, receive: Receive, send: Send, www_authenticate: str, description: str) -> None:
        response = JSONResponse(
            {"error": "invalid_token", "error_description": description},
            status_code=401,
            headers={"WWW-Authenticate": www_authenticate},
        )
        await response(scope, receive, send)


def get_authenticated_principal_from_request(request: Request) -> AuthenticatedPrincipal:
    """Read back the principal ``PrincipalAuthMiddleware`` verified for this request."""
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, AuthenticatedPrincipal):
        raise RuntimeError(
            "PrincipalAuthMiddleware calismadan bir MCP istegi islendi; bu bir programlama hatasidir."
        )
    return principal
