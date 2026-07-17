"""Assembles the auth-protected ``/mcp`` Streamable HTTP app (docs/MCP.md, docs/CONNECTOR_SUBMISSION.md).

Single public endpoint, matching ``docs/CONNECTOR_SUBMISSION.md`` ("Tek public
endpoint: https://<domain>/mcp, Streamable HTTP"). Auth is deliberately left
disabled at the FastMCP layer and enforced instead by
``PrincipalAuthMiddleware`` wrapping the returned app -- see
``mcp/auth_bridge.py`` for why.

Building the ``FastMCP`` instance and wrapping its ASGI app are kept as two
separate functions because ``mcp.session_manager`` (created lazily inside
``streamable_http_app()``) must be entered as an async context manager by the
*outer* FastAPI app's own ``lifespan`` -- Starlette does not cascade lifespan
startup/shutdown into a mounted sub-application on its own (verified against
``starlette.routing.Router.lifespan``, which only ever calls the root app's
own lifespan context manager). ``backend/src/app.py`` needs the ``FastMCP``
instance itself to wire that up, so this module hands it back rather than
hiding it inside a single "build everything" call.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp

from ..api.reporting import GoogleAdsReportingClient
from ..auth.vault import VaultClient
from ..config import Settings
from ..db.oauth_store import TokenRepository
from ..db.proposals import ProposalRepository
from .auth_bridge import PrincipalAuthMiddleware
from .proposals import register_proposal_tools
from .tools import MCPToolContext, register_reporting_tools


def _transport_security(settings: Settings) -> TransportSecuritySettings:
    """DNS-rebinding protection scoped to our own public host (docs/CONNECTOR_SUBMISSION.md).

    The SDK default is safe-but-broken for a real deployment: DNS-rebinding
    protection is on by default with an *empty* allowlist, which rejects
    every request regardless of Host/Origin. ``public_base_url`` is the one
    host this server is ever meant to answer for, so it is the allowlist.
    """
    origin = settings.public_base_url.rstrip("/")
    host = urlsplit(origin).netloc
    return TransportSecuritySettings(allowed_hosts=[host], allowed_origins=[origin])


def build_mcp_server(
    *,
    settings: Settings,
    conn: sqlite3.Connection,
    vault: VaultClient,
    reporting_client: GoogleAdsReportingClient | None = None,
) -> FastMCP:
    """Build the ``FastMCP`` instance with every Faz 1 reporting and proposal tool registered.

    ``reporting_client`` is injectable so tests can supply one backed by
    ``FakeGoogleAdsSearchService`` instead of making real Google Ads calls
    (docs/TESTING.md mock policy); production callers can omit it.
    """
    mcp = FastMCP(
        name="AddObserver Google Ads",
        instructions=(
            "Read-only Google Ads performance reporting for the accounts the connected "
            "user has approved, plus draft proposal preparation (campaign pause/enable, "
            "budget update) for human review. Nothing is ever written back to Google Ads "
            "through this connector yet -- a prepared proposal only records a suggestion; "
            "applying it is not possible until it is approved outside of Claude."
        ),
        streamable_http_path=settings.mcp_resource_path,
        transport_security=_transport_security(settings),
    )
    tool_context = MCPToolContext(
        settings=settings,
        conn=conn,
        vault=vault,
        reporting_client=reporting_client or GoogleAdsReportingClient(),
    )
    register_reporting_tools(mcp, tool_context)
    register_proposal_tools(mcp, tool_context, ProposalRepository(conn))
    return mcp


def wrap_with_principal_auth(mcp: FastMCP, *, settings: Settings, conn: sqlite3.Connection) -> ASGIApp:
    """Return ``mcp``'s Streamable HTTP ASGI app behind the connector's own bearer-token check.

    Calling ``mcp.streamable_http_app()`` here (rather than in
    ``build_mcp_server``) is what actually creates ``mcp.session_manager`` --
    callers must enter ``mcp.session_manager.run()`` in their own app
    lifespan *after* calling this function, or the mounted app will 500 on
    every request (no running session manager to hand requests to).
    """
    protected_resource_metadata_url = f"{settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource"
    return PrincipalAuthMiddleware(
        mcp.streamable_http_app(),
        tokens_factory=lambda: TokenRepository(conn),
        expected_resource=settings.mcp_resource_uri,
        protected_resource_metadata_url=protected_resource_metadata_url,
    )
