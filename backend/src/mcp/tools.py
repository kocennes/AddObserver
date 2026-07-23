"""Faz 1 read-only Google Ads reporting tools (docs/MCP.md, docs/PRODUCT.md).

Every tool here never mutates a real Google Ads resource; write tools do not
exist yet (Faz 1.1, blocked on ``docs/GOOGLE_API_ACCESS.md`` still being
``Taslak``). ``sync_accessible_accounts`` is the one exception to
``readOnlyHint`` -- it mirrors Google's account list into our own local
``ads_account`` bookkeeping (``LOCAL_SYNC`` annotation). ``ctx``'s principal
always comes from the verified connector access token (``mcp/auth_bridge.py``),
never from a tool argument -- ``customer_id`` selects *which* linked account
to read, it is never treated as proof of access on its own (docs/AUTH.md).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..api.accounts import GoogleAdsAccountDiscoveryClient
from ..api.errors import AdsApiError, ErrorClass
from ..api.queries import DateRange
from ..api.rate_limits import BucketPolicy, RateLimitExceeded, ScopedTokenBucketLimiter
from ..api.reporting import GoogleAdsCredentials, GoogleAdsReportingClient, ReportPage
from ..api.reporting_pagination import (
    InvalidReportCursorError,
    ReportCursorPosition,
    bound_report_rows,
    decode_report_cursor,
    encode_report_cursor,
)
from ..auth.vault import VaultClient
from ..config import Settings
from ..db.models import OAuthCredential
from ..db.postgres_uow import PostgresUnitOfWorkFactory
from ..db.repository import AdsAccountRepository, OAuthCredentialRepository
from ..observability import JsonEventLogger, Telemetry
from .credentials import (
    GoogleAdsCredentialReference,
    deactivate_credential_on_auth_failure,
    materialize_google_ads_credentials,
    resolve_connector_oauth_credential,
    resolve_google_ads_credential_reference,
    resolve_google_ads_credentials,
)
from .output_schemas import SCHEMA_VERSION, TOOL_OUTPUT_SCHEMAS
from .tool_support import (
    LOCAL_SYNC,
    READ_ONLY,
    READ_ONLY_LOCAL,
    authenticated_principal_id,
    close_input_schema,
    set_output_schema,
)


@dataclass(frozen=True, slots=True)
class MCPToolContext:
    """Everything the reporting tools need, assembled once by ``mcp/server.py``."""

    settings: Settings
    conn: sqlite3.Connection
    vault: VaultClient
    reporting_client: GoogleAdsReportingClient
    event_logger: JsonEventLogger
    telemetry: Telemetry = field(default_factory=Telemetry)
    postgres_uow_factory: PostgresUnitOfWorkFactory | None = None
    account_discovery_client: GoogleAdsAccountDiscoveryClient = field(
        default_factory=GoogleAdsAccountDiscoveryClient
    )
    rate_limiter: ScopedTokenBucketLimiter = field(default_factory=ScopedTokenBucketLimiter)


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


def _resolve_credentials(
    context: MCPToolContext, ctx: Context, customer_id: str
) -> GoogleAdsCredentials:
    principal_id = authenticated_principal_id(ctx)
    if context.postgres_uow_factory is not None:
        with context.postgres_uow_factory.request() as work:
            work.bind_principal(principal_id)
            if work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
            reference = resolve_google_ads_credential_reference(
                principal_id=principal_id,
                customer_id=customer_id,
                accounts=work.repositories.accounts,
                oauth_credentials=work.repositories.credentials,
            )
        return materialize_google_ads_credentials(
            reference=reference,
            settings=context.settings,
            vault=context.vault,
        )
    return resolve_google_ads_credentials(
        principal_id=principal_id,
        customer_id=customer_id,
        settings=context.settings,
        accounts=AdsAccountRepository(context.conn),
        oauth_credentials=OAuthCredentialRepository(context.conn),
        vault=context.vault,
    )


def _correlation_id_from_ctx(ctx: Context) -> str:
    """Reuse the HTTP correlation id ``CorrelationIdMiddleware`` already put on the ASGI
    scope this MCP request travels through, falling back to a fresh one if unavailable
    (e.g. a test double with no real request)."""
    request = ctx.request_context.request
    correlation_id = getattr(request, "scope", {}).get("correlation_id") if request else None
    if isinstance(correlation_id, str) and correlation_id:
        return correlation_id
    return str(uuid.uuid4())


def _log_google_ads_failure(
    context: MCPToolContext,
    ctx: Context,
    *,
    operation: str,
    principal_id: str,
    customer_id: str | None,
    error: AdsApiError,
) -> None:
    """Record a Google Ads-origin failure for support/telemetry (ERROR_HANDLING.md,
    todo.md 5.6): Google's own ``request_id`` travels here, never the raw exception
    text/payload -- ``AdsApiError.message`` is already Google's own safe user-facing
    text, but is deliberately not logged since it is not on the fixed event schema."""
    context.event_logger.emit(
        level="ERROR",
        operation=operation,
        outcome="failure",
        correlation_id=_correlation_id_from_ctx(ctx),
        reason_code=error.code,
        principal_id=principal_id,
        customer_id=customer_id,
        google_request_id=error.request_id,
    )


def _deactivate_credential(context: MCPToolContext, principal_id: str, error: AdsApiError) -> None:
    """Dual-path AUTH-class credential deactivation, shared by every Google Ads call site
    (ERROR_HANDLING.md 'Auth' row, todo.md 3.6)."""
    if context.postgres_uow_factory is None:
        deactivate_credential_on_auth_failure(
            error,
            principal_id=principal_id,
            oauth_credentials=OAuthCredentialRepository(context.conn),
        )
        return
    with context.postgres_uow_factory.request() as work:
        work.bind_principal(principal_id)
        if work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi") from None
        deactivate_credential_on_auth_failure(
            error,
            principal_id=principal_id,
            oauth_credentials=work.repositories.credentials,
        )


def _resolve_connector_credential(context: MCPToolContext, principal_id: str) -> OAuthCredential:
    """Resolve the principal's connector-level Google OAuth credential, dual-path.

    Used by discovery, which populates ``ads_account`` and so cannot depend on a
    linked account already existing (unlike ``_resolve_credentials`` above).
    """
    if context.postgres_uow_factory is None:
        return resolve_connector_oauth_credential(
            principal_id=principal_id, oauth_credentials=OAuthCredentialRepository(context.conn)
        )
    with context.postgres_uow_factory.request() as work:
        work.bind_principal(principal_id)
        if work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
        return resolve_connector_oauth_credential(
            principal_id=principal_id, oauth_credentials=work.repositories.credentials
        )


def _synchronize_accounts(
    context: MCPToolContext, principal_id: str, pairs: list[tuple[str, str | None]]
) -> dict[str, Any]:
    """Replace the caller's local account snapshot, dual-path, and return the active rows."""
    if context.postgres_uow_factory is None:
        linked = AdsAccountRepository(context.conn).synchronize_accounts(principal_id, pairs)
    else:
        with context.postgres_uow_factory.request() as work:
            work.bind_principal(principal_id)
            if work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
            linked = work.repositories.accounts.synchronize_accounts(principal_id, pairs)
    return {
        "schema_version": SCHEMA_VERSION,
        "accounts": [
            {
                "customer_id": account.customer_id,
                "login_customer_id": account.login_customer_id,
                "status": account.status,
            }
            for account in linked
        ],
        "warnings": [],
    }


def _fetch_report_page(
    context: MCPToolContext,
    ctx: Context,
    customer_id: str,
    start_date: str,
    end_date: str,
    page_token: str | None,
    *,
    report_kind: str,
    fetch: Callable[..., ReportPage],
) -> dict[str, Any]:
    """Shared body for every reporting tool: resolve credentials, call ``fetch``, and
    react to an AUTH-class failure by deactivating the credential (ERROR_HANDLING.md
    'Auth' row, todo.md 3.6) before letting the error propagate as a tool error."""
    principal_id = authenticated_principal_id(ctx)
    try:
        context.rate_limiter.acquire(
            ("developer-token",),
            BucketPolicy(
                context.settings.rate_limit_global_capacity,
                context.settings.rate_limit_global_refill_per_second,
            ),
        )
        context.rate_limiter.acquire(
            ("principal", principal_id, "customer", customer_id),
            BucketPolicy(
                context.settings.rate_limit_principal_customer_capacity,
                context.settings.rate_limit_principal_customer_refill_per_second,
            ),
        )
    except RateLimitExceeded as error:
        raise AdsApiError(
            error_class=ErrorClass.RATE_LIMIT,
            code="rate_limited",
            message="Istek kotasi gecici olarak dolu; daha sonra yeniden deneyin.",
            request_id=None,
            retry_delay_seconds=error.retry_after_seconds,
        ) from error
    vault_key = context.settings.local_vault_key
    if not vault_key:
        raise RuntimeError("Reporting continuation signing key yapilandirilmadi")
    now = datetime.now(UTC)
    position = ReportCursorPosition(provider_page_token=None, row_offset=0)
    if page_token is not None:
        try:
            position = decode_report_cursor(
                vault_key,
                page_token,
                principal_id=principal_id,
                customer_id=customer_id,
                report_kind=report_kind,
                start_date=start_date,
                end_date=end_date,
                now=now,
            )
        except InvalidReportCursorError as error:
            raise AdsApiError(
                error_class=ErrorClass.VALIDATION,
                code="invalid_page_token",
                message="Reporting devam anahtari gecersiz veya suresi dolmus.",
                request_id=None,
            ) from error
    credentials = _resolve_credentials(context, ctx, customer_id)
    try:
        page = fetch(
            customer_id=customer_id,
            credentials=credentials,
            date_range=_parse_date_range(start_date, end_date),
            page_token=position.provider_page_token,
        )
    except AdsApiError as error:
        _log_google_ads_failure(
            context,
            ctx,
            operation=f"google_ads_{report_kind}_report",
            principal_id=principal_id,
            customer_id=customer_id,
            error=error,
        )
        _deactivate_credential(context, principal_id, error)
        raise
    try:
        bounded = bound_report_rows(page.rows, offset=position.row_offset)
    except InvalidReportCursorError as error:
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="invalid_page_token",
            message="Reporting devam anahtari gecersiz veya suresi dolmus.",
            request_id=None,
        ) from error
    except ValueError as error:
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="report_row_too_large",
            message="Google Ads reporting satiri guvenli response sinirini asiyor.",
            request_id=None,
        ) from error
    next_position = None
    if bounded.next_offset is not None:
        next_position = ReportCursorPosition(
            provider_page_token=position.provider_page_token,
            row_offset=bounded.next_offset,
        )
    elif page.next_page_token is not None:
        next_position = ReportCursorPosition(provider_page_token=page.next_page_token, row_offset=0)
    next_page_token = (
        encode_report_cursor(
            vault_key,
            principal_id=principal_id,
            customer_id=customer_id,
            report_kind=report_kind,
            start_date=start_date,
            end_date=end_date,
            position=next_position,
            now=now,
        )
        if next_position is not None
        else None
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "customer_id": customer_id,
        "date_range": {"start_date": start_date, "end_date": end_date},
        "rows": list(bounded.rows),
        "next_page_token": next_page_token,
        "row_count": len(bounded.rows),
        "truncated": bounded.next_offset is not None,
        "warnings": (["result_truncated"] if bounded.next_offset is not None else []),
    }


def register_reporting_tools(mcp: FastMCP, context: MCPToolContext) -> None:
    """Register every Faz 1 read-only tool and close each one's input schema."""

    @mcp.tool(
        title="Bağlı Google Ads hesaplarını listele",
        annotations=READ_ONLY_LOCAL,
        structured_output=True,
    )
    def list_accessible_accounts(ctx: Context) -> dict[str, Any]:
        """List the Google Ads customer_ids linked to the caller's connector session."""
        principal_id = authenticated_principal_id(ctx)
        if context.postgres_uow_factory is None:
            accounts = AdsAccountRepository(context.conn).list_active_accounts(principal_id)
        else:
            with context.postgres_uow_factory.request() as work:
                work.bind_principal(principal_id)
                if work.repositories is None:
                    raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
                accounts = work.repositories.accounts.list_active_accounts(principal_id)
        return {
            "schema_version": SCHEMA_VERSION,
            "accounts": [
                {
                    "customer_id": account.customer_id,
                    "login_customer_id": account.login_customer_id,
                    "status": account.status,
                }
                for account in accounts
            ],
            "warnings": [],
        }

    @mcp.tool(
        title="Erişilebilir Google Ads hesaplarını Google'dan senkronize et",
        annotations=LOCAL_SYNC,
        structured_output=True,
    )
    def sync_accessible_accounts(ctx: Context) -> dict[str, Any]:
        """Discover every Google Ads customer_id this connection can access (direct
        access plus any manager hierarchy beneath it) and replace the caller's local
        account snapshot with it. Accounts no longer accessible are marked
        disconnected, never deleted (docs/AUTH.md disconnect decision); nothing here
        ever calls a Google Ads mutate RPC."""
        principal_id = authenticated_principal_id(ctx)
        credential = _resolve_connector_credential(context, principal_id)
        credentials = materialize_google_ads_credentials(
            reference=GoogleAdsCredentialReference(
                vault_ref=credential.vault_ref, login_customer_id=None
            ),
            settings=context.settings,
            vault=context.vault,
        )
        try:
            discovered = context.account_discovery_client.discover_accounts(credentials)
        except AdsApiError as error:
            _log_google_ads_failure(
                context,
                ctx,
                operation="google_ads_account_discovery",
                principal_id=principal_id,
                customer_id=None,
                error=error,
            )
            _deactivate_credential(context, principal_id, error)
            raise
        pairs = [(account.customer_id, account.login_customer_id) for account in discovered]
        return _synchronize_accounts(context, principal_id, pairs)

    @mcp.tool(title="Kampanya performansı getir", annotations=READ_ONLY, structured_output=True)
    def get_campaign_performance(
        ctx: Context,
        customer_id: str,
        start_date: str,
        end_date: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Return paginated daily campaign performance for one linked Google Ads account."""
        return _fetch_report_page(
            context,
            ctx,
            customer_id,
            start_date,
            end_date,
            page_token,
            report_kind="campaign",
            fetch=context.reporting_client.get_campaign_performance,
        )

    @mcp.tool(title="Reklam grubu performansı getir", annotations=READ_ONLY, structured_output=True)
    def get_ad_group_performance(
        ctx: Context,
        customer_id: str,
        start_date: str,
        end_date: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Return paginated daily ad group performance for one linked Google Ads account."""
        return _fetch_report_page(
            context,
            ctx,
            customer_id,
            start_date,
            end_date,
            page_token,
            report_kind="ad_group",
            fetch=context.reporting_client.get_ad_group_performance,
        )

    @mcp.tool(
        title="Anahtar kelime performansı getir", annotations=READ_ONLY, structured_output=True
    )
    def get_keyword_performance(
        ctx: Context,
        customer_id: str,
        start_date: str,
        end_date: str,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Return paginated daily keyword performance for one linked Google Ads account."""
        return _fetch_report_page(
            context,
            ctx,
            customer_id,
            start_date,
            end_date,
            page_token,
            report_kind="keyword",
            fetch=context.reporting_client.get_keyword_performance,
        )

    for tool_name in (
        "list_accessible_accounts",
        "sync_accessible_accounts",
        "get_campaign_performance",
        "get_ad_group_performance",
        "get_keyword_performance",
    ):
        close_input_schema(mcp, tool_name)
        set_output_schema(mcp, tool_name, TOOL_OUTPUT_SCHEMAS[tool_name])
