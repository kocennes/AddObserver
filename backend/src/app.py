"""Composition root: wires config, db, auth and MCP into one ASGI app.

Closes the ``AGENTS.md`` "Yerel calistirma" TODO. Local run:

    uvicorn backend.src.app:create_app --factory

``--factory`` is deliberate, not a style choice: a plain module-level
``app = create_app()`` would build a real sqlite connection, vault and Google
OAuth client as a side effect of merely *importing* this module, which would
break importing ``create_app`` itself from a test process that has no
``.env``. ``uvicorn``'s factory mode calls ``create_app()`` only when the
server actually starts.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .api.problems import problem_response
from .api.reporting import GoogleAdsReportingClient
from .api.routes import router as api_router
from .auth.approvals_routes import router as approvals_router
from .auth.google_oauth import GOOGLE_SCOPES, GoogleOAuthClient, GoogleWebFlowOAuthClient
from .auth.server import AuthContext
from .auth.server import router as auth_router
from .auth.vault import LocalEncryptedVault, VaultClient
from .config import Settings
from .db.connection import connect
from .db.postgres_uow import PostgresUnitOfWorkFactory
from .mcp.server import build_mcp_server, wrap_with_principal_auth

#: Scopes for the ``/approvals`` browser login only -- deliberately excludes
#: ``adwords`` (docs/AUTH.md): this flow only proves "this browser belongs to
#: principal X", it never re-authorizes or touches Google Ads access.
_LOGIN_ONLY_SCOPES = tuple(
    scope for scope in GOOGLE_SCOPES if scope != "https://www.googleapis.com/auth/adwords"
)
MAX_REQUEST_BODY_BYTES = 1_048_576
CORRELATION_ID_HEADER = b"x-correlation-id"
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SECURITY_RESPONSE_HEADERS = {
    b"cache-control": b"no-store",
    b"content-security-policy": (
        b"default-src 'none'; "
        b"base-uri 'none'; "
        b"form-action 'self'; "
        b"frame-ancestors 'none'; "
        b"object-src 'none'; "
        b"script-src 'none'"
    ),
    b"referrer-policy": b"no-referrer",
    b"x-content-type-options": b"nosniff",
}
#: Only attached outside ``environment == "local"`` (docs/SECURITY.md "Girdi, cikti ve web
#: guvenligi"): HSTS on a plain-HTTP local dev response is meaningless and would be actively
#: wrong (browsers cache it per-host and there is no cert to fall back to).
HSTS_HEADER = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")
PRODUCTION_ENVIRONMENTS = frozenset({"prod", "production"})


def _problem_response(
    *,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    correlation_id: str | None = None,
) -> JSONResponse:
    return problem_response(
        status_code=status_code,
        title=title,
        detail=detail,
        code=code,
        correlation_id=correlation_id,
    )


def _correlation_id_from_scope(scope: dict[str, Any]) -> str:
    correlation_id = scope.get("correlation_id")
    if isinstance(correlation_id, str) and correlation_id:
        return correlation_id
    return str(uuid.uuid4())


class RequestBodyLimitMiddleware:
    """Bound public HTTP request bodies even when the client omits Content-Length."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict]], send: Callable
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                body_size = int(content_length.decode("ascii"))
            except ValueError:
                await _problem_response(
                    status_code=400,
                    title="Invalid Content-Length",
                    detail="Content-Length basligi gecerli bir tamsayi olmalidir.",
                    code="invalid_content_length",
                    correlation_id=_correlation_id_from_scope(scope),
                )(scope, receive, send)
                return
            if body_size > self._max_body_bytes:
                await _problem_response(
                    status_code=413,
                    title="Request body too large",
                    detail=f"Istek govdesi en fazla {self._max_body_bytes} bayt olabilir.",
                    code="request_body_too_large",
                    correlation_id=_correlation_id_from_scope(scope),
                )(scope, receive, send)
                return

        body_size = 0
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal body_size
            message = await receive()
            if message["type"] == "http.request":
                body_size += len(message.get("body", b""))
                if body_size > self._max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        async def tracking_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self._app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await _problem_response(
                status_code=413,
                title="Request body too large",
                detail=f"Istek govdesi en fazla {self._max_body_bytes} bayt olabilir.",
                code="request_body_too_large",
                correlation_id=_correlation_id_from_scope(scope),
            )(scope, receive, send)


class RequestBodyTooLarge(Exception):
    """Raised when a streamed request body crosses the configured ingress limit."""


class CorrelationIdMiddleware:
    """Ensure every HTTP response has a safe correlation ID for support and logs."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict]], send: Callable
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        incoming = None
        for key, value in scope.get("headers", []):
            if key.lower() == CORRELATION_ID_HEADER:
                try:
                    decoded = value.decode("ascii")
                except UnicodeDecodeError:
                    decoded = ""
                if CORRELATION_ID_PATTERN.fullmatch(decoded):
                    incoming = decoded
                break
        correlation_id = incoming or str(uuid.uuid4())
        scope["correlation_id"] = correlation_id

        async def send_with_correlation_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != CORRELATION_ID_HEADER
                ]
                headers.append((CORRELATION_ID_HEADER, correlation_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_correlation_id)


class SecurityHeadersMiddleware:
    """Attach conservative browser security headers to every public HTTP response."""

    def __init__(self, app: Callable[..., Awaitable[None]], *, hsts: bool = False) -> None:
        self._app = app
        self._hsts = hsts

    async def __call__(
        self, scope: dict[str, Any], receive: Callable[[], Awaitable[dict]], send: Callable
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_security_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = {key.lower() for key, _ in message.get("headers", [])}
                headers = list(message.get("headers", []))
                for key, value in SECURITY_RESPONSE_HEADERS.items():
                    if key not in existing:
                        headers.append((key, value))
                if self._hsts and HSTS_HEADER[0] not in existing:
                    headers.append(HSTS_HEADER)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_security_headers)


def create_app(
    settings: Settings | None = None,
    *,
    vault: VaultClient | None = None,
    google_client: GoogleOAuthClient | None = None,
    login_google_client: GoogleOAuthClient | None = None,
    reporting_client: GoogleAdsReportingClient | None = None,
    postgres_uow_factory: PostgresUnitOfWorkFactory | None = None,
) -> FastAPI:
    """Build the full connector app: OAuth 2.1 AS routes + the auth-protected ``/mcp`` endpoint.

    ``vault``/``google_client``/``login_google_client``/``reporting_client`` are injectable so
    tests can pass fakes (``FakeGoogleOAuthClient``, an in-memory
    ``LocalEncryptedVault``, a ``GoogleAdsReportingClient`` backed by
    ``FakeGoogleAdsSearchService``) without needing real Google credentials
    or making real Google Ads calls.
    """
    settings = settings or Settings.load()
    if settings.environment.lower() in PRODUCTION_ENVIRONMENTS:
        raise RuntimeError(
            "Production startup is disabled until every HTTP/MCP/auth repository path uses "
            "PostgreSQL request transactions with RLS context; falling back to "
            "LOCAL_SQLITE_DB_PATH "
            "would violate docs/DATABASE.md and principal isolation."
        )
    if settings.environment != "local" and not settings.public_base_url.startswith("https://"):
        raise RuntimeError(
            "PUBLIC_BASE_URL 'https://' ile baslamali (APP_ENVIRONMENT='local' disinda) -- "
            "connector AS'in issuer/authorization_endpoint/token_endpoint ve protected-resource "
            "metadata'si bu deger uzerinden kurulur; OAuth 2.1 ve MCP Authorization "
            "spesifikasyonu tum AS uc noktalarinin HTTPS uzerinden sunulmasini zorunlu kilar "
            "(bkz. docs/AUTH.md 'Saldiri kontrolleri')."
        )
    conn = connect(settings.sqlite_db_path)

    if vault is None:
        if not settings.local_vault_key:
            raise RuntimeError(
                "LOCAL_VAULT_KEY tanimli degil (bkz. .env.example) -- yerel/dev vault icin "
                "zorunlu; uretimde SECURITY.md'nin secrets manager karari uygulanmalidir "
                "(hala TBD)."
            )
        vault = LocalEncryptedVault(conn, settings.local_vault_key)

    if google_client is None:
        google_client = GoogleWebFlowOAuthClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
        )

    if login_google_client is None:
        login_google_client = GoogleWebFlowOAuthClient(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            redirect_uri=settings.google_redirect_uri,
            scopes=_LOGIN_ONLY_SCOPES,
        )

    http_client = httpx.Client(timeout=10.0)
    mcp_server = build_mcp_server(
        settings=settings,
        conn=conn,
        vault=vault,
        reporting_client=reporting_client,
        postgres_uow_factory=postgres_uow_factory,
    )
    mcp_app = wrap_with_principal_auth(
        mcp_server,
        settings=settings,
        conn=conn,
        postgres_uow_factory=postgres_uow_factory,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            async with mcp_server.session_manager.run():
                yield
        finally:
            http_client.close()
            conn.close()

    app = FastAPI(title="AddObserver connector", lifespan=lifespan)
    # Added innermost-first: the LAST middleware added wraps every one before it, so
    # SecurityHeadersMiddleware/CorrelationIdMiddleware end up outermost and therefore still
    # attach their headers to responses that TrustedHost/CORS short-circuit (e.g. a Host
    # mismatch 400 never reaches the route, but must still carry no-store/CSP/correlation-id).
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=MAX_REQUEST_BODY_BYTES)
    # CORS: closed by default (docs/SECURITY.md "CORS acik allowlist'tir"). No entry in
    # ``cors_allowed_origins`` means no cross-origin browser access at all -- never "*", never
    # allow_credentials, since our auth is bearer/cookie based and a wildcard+credentials
    # combination would let any site read a signed-in user's responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["authorization", "content-type", "x-correlation-id"],
        expose_headers=["x-correlation-id"],
    )
    # Host header validation (docs/SECURITY.md): rejects requests for a Host this deployment
    # was never configured to answer for. Derived from PUBLIC_BASE_URL/ALLOWED_HOSTS only --
    # DEPLOYMENT.md's proxy topology ADR is still open, so no `X-Forwarded-Host` trust here.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.environment != "local")
    app.state.auth_context = AuthContext(
        settings=settings,
        conn=conn,
        vault=vault,
        google_client=google_client,
        http_client=http_client,
        login_google_client=login_google_client,
        postgres_uow_factory=postgres_uow_factory,
    )
    app.include_router(auth_router)
    app.include_router(approvals_router)
    app.include_router(api_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness probe: process is up and able to answer HTTP."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness probe: required local dependencies are reachable."""
        try:
            conn.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001 -- readiness must not leak DB details
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return JSONResponse({"status": "ok"})

    app.mount("/", mcp_app)
    return app
