"""Faz 1 read-only Google Ads reporting tools (docs/MCP.md, docs/PRODUCT.md).

Every tool here is ``readOnlyHint``; write tools do not exist yet (Faz 1.1,
blocked on ``docs/GOOGLE_API_ACCESS.md`` still being ``Taslak``). ``ctx``'s
principal always comes from the verified connector access token
(``mcp/auth_bridge.py``), never from a tool argument -- ``customer_id``
selects *which* linked account to read, it is never treated as proof of
access on its own (docs/AUTH.md).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..api.errors import AdsApiError, ErrorClass
from ..api.queries import DateRange
from ..api.reporting import GoogleAdsCredentials, GoogleAdsReportingClient
from ..auth.vault import VaultClient
from ..config import Settings
from ..db.repository import AdsAccountRepository, OAuthCredentialRepository
from .credentials import resolve_google_ads_credentials
from .tool_support import READ_ONLY, READ_ONLY_LOCAL, authenticated_principal_id, close_input_schema


@dataclass(frozen=True, slots=True)
class MCPToolContext:
    """Everything the reporting tools need, assembled once by ``mcp/server.py``."""

    settings: Settings
    conn: sqlite3.Connection
    vault: VaultClient
    reporting_client: GoogleAdsReportingClient


def _parse_date_range(start_date: str, end_date: str) -> DateRange:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as error:
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="invalid_date",
            message="start_date/end_date 'YYYY-MM-DD' formatinda olmalidir.",
            request_id=None,
        ) from error
    return DateRange(start=start, end=end)


def _resolve_credentials(context: MCPToolContext, ctx: Context, customer_id: str) -> GoogleAdsCredentials:
    principal_id = authenticated_principal_id(ctx)
    return resolve_google_ads_credentials(
        principal_id=principal_id,
        customer_id=customer_id,
        settings=context.settings,
        accounts=AdsAccountRepository(context.conn),
        oauth_credentials=OAuthCredentialRepository(context.conn),
        vault=context.vault,
    )


def register_reporting_tools(mcp: FastMCP, context: MCPToolContext) -> None:
    """Register every Faz 1 read-only tool and close each one's input schema."""

    @mcp.tool(title="Bağlı Google Ads hesaplarını listele", annotations=READ_ONLY_LOCAL, structured_output=False)
    def list_accessible_accounts(ctx: Context) -> list[dict[str, str | None]]:
        """List the Google Ads customer_ids linked to the caller's connector session."""
        principal_id = authenticated_principal_id(ctx)
        accounts = AdsAccountRepository(context.conn).list_accounts(principal_id)
        return [
            {"customer_id": account.customer_id, "login_customer_id": account.login_customer_id, "status": account.status}
            for account in accounts
        ]

    @mcp.tool(title="Kampanya performansı getir", annotations=READ_ONLY, structured_output=False)
    def get_campaign_performance(
        ctx: Context, customer_id: str, start_date: str, end_date: str, page_token: str | None = None
    ) -> dict[str, Any]:
        """Return paginated daily campaign performance for one linked Google Ads account."""
        credentials = _resolve_credentials(context, ctx, customer_id)
        page = context.reporting_client.get_campaign_performance(
            customer_id=customer_id,
            credentials=credentials,
            date_range=_parse_date_range(start_date, end_date),
            page_token=page_token,
        )
        return {"rows": list(page.rows), "next_page_token": page.next_page_token}

    @mcp.tool(title="Reklam grubu performansı getir", annotations=READ_ONLY, structured_output=False)
    def get_ad_group_performance(
        ctx: Context, customer_id: str, start_date: str, end_date: str, page_token: str | None = None
    ) -> dict[str, Any]:
        """Return paginated daily ad group performance for one linked Google Ads account."""
        credentials = _resolve_credentials(context, ctx, customer_id)
        page = context.reporting_client.get_ad_group_performance(
            customer_id=customer_id,
            credentials=credentials,
            date_range=_parse_date_range(start_date, end_date),
            page_token=page_token,
        )
        return {"rows": list(page.rows), "next_page_token": page.next_page_token}

    @mcp.tool(title="Anahtar kelime performansı getir", annotations=READ_ONLY, structured_output=False)
    def get_keyword_performance(
        ctx: Context, customer_id: str, start_date: str, end_date: str, page_token: str | None = None
    ) -> dict[str, Any]:
        """Return paginated daily keyword performance for one linked Google Ads account."""
        credentials = _resolve_credentials(context, ctx, customer_id)
        page = context.reporting_client.get_keyword_performance(
            customer_id=customer_id,
            credentials=credentials,
            date_range=_parse_date_range(start_date, end_date),
            page_token=page_token,
        )
        return {"rows": list(page.rows), "next_page_token": page.next_page_token}

    for tool_name in (
        "list_accessible_accounts",
        "get_campaign_performance",
        "get_ad_group_performance",
        "get_keyword_performance",
    ):
        close_input_schema(mcp, tool_name)
