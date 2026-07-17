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

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI

from .api.reporting import GoogleAdsReportingClient
from .auth.approvals_routes import router as approvals_router
from .auth.google_oauth import GOOGLE_SCOPES, GoogleOAuthClient, GoogleWebFlowOAuthClient
from .auth.server import AuthContext
from .auth.server import router as auth_router
from .auth.vault import LocalEncryptedVault, VaultClient
from .config import Settings
from .db.connection import connect
from .mcp.server import build_mcp_server, wrap_with_principal_auth

#: Scopes for the ``/approvals`` browser login only -- deliberately excludes
#: ``adwords`` (docs/AUTH.md): this flow only proves "this browser belongs to
#: principal X", it never re-authorizes or touches Google Ads access.
_LOGIN_ONLY_SCOPES = tuple(scope for scope in GOOGLE_SCOPES if scope != "https://www.googleapis.com/auth/adwords")


def create_app(
    settings: Settings | None = None,
    *,
    vault: VaultClient | None = None,
    google_client: GoogleOAuthClient | None = None,
    login_google_client: GoogleOAuthClient | None = None,
    reporting_client: GoogleAdsReportingClient | None = None,
) -> FastAPI:
    """Build the full connector app: OAuth 2.1 AS routes + the auth-protected ``/mcp`` endpoint.

    ``vault``/``google_client``/``login_google_client``/``reporting_client`` are injectable so
    tests can pass fakes (``FakeGoogleOAuthClient``, an in-memory
    ``LocalEncryptedVault``, a ``GoogleAdsReportingClient`` backed by
    ``FakeGoogleAdsSearchService``) without needing real Google credentials
    or making real Google Ads calls.
    """
    settings = settings or Settings.load()
    conn = connect(settings.sqlite_db_path)

    if vault is None:
        if not settings.local_vault_key:
            raise RuntimeError(
                "LOCAL_VAULT_KEY tanimli degil (bkz. .env.example) -- yerel/dev vault icin "
                "zorunlu; uretimde SECURITY.md'nin secrets manager karari uygulanmalidir (hala TBD)."
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
    mcp_server = build_mcp_server(settings=settings, conn=conn, vault=vault, reporting_client=reporting_client)
    mcp_app = wrap_with_principal_auth(mcp_server, settings=settings, conn=conn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp_server.session_manager.run():
            yield
        http_client.close()

    app = FastAPI(title="AddObserver connector", lifespan=lifespan)
    app.state.auth_context = AuthContext(
        settings=settings,
        conn=conn,
        vault=vault,
        google_client=google_client,
        http_client=http_client,
        login_google_client=login_google_client,
    )
    app.include_router(auth_router)
    app.include_router(approvals_router)
    app.mount("/", mcp_app)
    return app
