"""Faz 1 proposal-preparation MCP tools (docs/MCP.md, docs/PRODUCT.md).

``prepare_proposal`` never calls Google Ads and never mutates a live account --
it only stores a draft, human-reviewable proposal in our own DB, which is why
it is in scope now even though the *apply* tool (Faz 1.1) stays blocked on
``docs/GOOGLE_API_ACCESS.md`` still being ``Taslak``. Its payload is restricted
to the Faz 1.1 allowlist (``backend.src.approval.payload_schema``) so every
proposal this connector ever creates already describes an operation Faz 1.1
will eventually be allowed to apply -- never an arbitrary mutate. As with the
reporting tools, ``ctx``'s principal always comes from the verified connector
access token, never from a tool argument (docs/MCP.md).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..api.errors import AdsApiError, ErrorClass
from ..api.identifiers import validate_opaque_id
from ..api.queries import validate_customer_id
from ..approval import ApprovalError, Proposal, build_proposal_payload, submit_proposal
from ..approval.serialization import proposal_to_dict
from ..db.postgres_repository import PostgresAdsAccountRepository, PostgresProposalRepository
from ..db.proposals import ProposalRepository
from ..db.repository import AdsAccountRepository
from .output_schemas import TOOL_OUTPUT_SCHEMAS
from .tool_support import (
    LOCAL_WRITE,
    READ_ONLY_LOCAL,
    authenticated_principal_id,
    close_input_schema,
    set_output_schema,
)
from .tools import MCPToolContext

#: Bounds on how long a prepared proposal stays actionable before it expires
#: (docs/DATA_MODEL.md freshness -- an approval older than this is worthless
#: since the underlying Google Ads state may have moved on).
MIN_EXPIRY_MINUTES = 5
MAX_EXPIRY_MINUTES = 24 * 60
DEFAULT_EXPIRY_MINUTES = 60
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 100

AccountStore = AdsAccountRepository | PostgresAdsAccountRepository
ProposalStore = ProposalRepository | PostgresProposalRepository


@contextmanager
def _proposal_repositories(
    context: MCPToolContext,
    fallback: ProposalRepository,
    principal_id: str,
) -> Iterator[tuple[AccountStore, ProposalStore]]:
    """Provide one principal-scoped transaction for a local-only proposal tool."""
    if context.postgres_uow_factory is None:
        yield AdsAccountRepository(context.conn), fallback
        return

    with context.postgres_uow_factory.request() as work:
        work.bind_principal(principal_id)
        if work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
        yield work.repositories.accounts, work.repositories.proposals


def _verify_account_ownership(accounts: AccountStore, principal_id: str, customer_id: str) -> None:
    """Same ownership check and error shape as ``credentials.resolve_google_ads_credentials``.

    A proposal never needs Google Ads credentials (it makes no API call), but it
    must still be scoped to an account the caller has actually linked -- otherwise
    a principal could draft proposals against a ``customer_id`` it cannot access.
    """
    account = accounts.get_active_account(principal_id, customer_id)
    if account is None:
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="account_not_linked",
            message="Bu customer_id bu baglantiya ait degil veya henuz baglanmamis.",
            request_id=None,
        )


def register_proposal_tools(
    mcp: FastMCP, context: MCPToolContext, proposals: ProposalRepository
) -> None:
    """Register the Faz 1 proposal-preparation tools and close each input schema."""

    @mcp.tool(title="Değişiklik önerisi hazırla", annotations=LOCAL_WRITE, structured_output=True)
    def prepare_proposal(
        ctx: Context,
        customer_id: str,
        proposal_type: str,
        campaign_id: str,
        rationale: str,
        current_status: str | None = None,
        current_budget_amount_micros: int | None = None,
        proposed_budget_amount_micros: int | None = None,
        expires_in_minutes: int = DEFAULT_EXPIRY_MINUTES,
    ) -> dict[str, Any]:
        """Draft a Faz 1.1 allowlist proposal (campaign pause/enable or budget update) for
        human review.

        This never calls Google Ads and never applies anything by itself -- it
        only stores a draft the account owner must separately approve before any
        write can happen.
        """
        principal_id = authenticated_principal_id(ctx)
        with _proposal_repositories(context, proposals, principal_id) as (accounts, scoped):
            _verify_account_ownership(accounts, principal_id, customer_id)
            if not (MIN_EXPIRY_MINUTES <= expires_in_minutes <= MAX_EXPIRY_MINUTES):
                raise ApprovalError(
                    "invalid_expiry",
                    f"expires_in_minutes {MIN_EXPIRY_MINUTES} ile {MAX_EXPIRY_MINUTES} "
                    "arasinda olmalidir.",
                )

            payload = build_proposal_payload(
                proposal_type=proposal_type,
                campaign_id=campaign_id,
                rationale=rationale,
                current_status=current_status,
                current_budget_amount_micros=current_budget_amount_micros,
                proposed_budget_amount_micros=proposed_budget_amount_micros,
            )

            now = datetime.now(UTC)
            draft = Proposal.create(
                proposal_id=str(uuid.uuid4()),
                principal_id=principal_id,
                customer_id=customer_id,
                payload=payload,
                expires_at=now + timedelta(minutes=expires_in_minutes),
            )
            pending = submit_proposal(draft, now=now)
            scoped.save(pending)
            return proposal_to_dict(pending)

    @mcp.tool(title="Öneri durumunu getir", annotations=READ_ONLY_LOCAL, structured_output=True)
    def get_proposal(ctx: Context, proposal_id: str) -> dict[str, Any]:
        """Return a previously prepared proposal's current status, only if it belongs to the
        caller."""
        principal_id = authenticated_principal_id(ctx)
        try:
            validate_opaque_id(proposal_id, field_name="proposal_id")
        except ValueError as error:
            raise ApprovalError("invalid_proposal_id", str(error)) from error
        with _proposal_repositories(context, proposals, principal_id) as (_accounts, scoped):
            proposal = scoped.get(principal_id, proposal_id)
            if proposal is None:
                raise ApprovalError(
                    "proposal_not_found",
                    "Bu proposal_id bu baglantiya ait degil veya bulunamadi.",
                )
            return proposal_to_dict(proposal)

    @mcp.tool(
        title="Bekleyen önerileri listele", annotations=READ_ONLY_LOCAL, structured_output=True
    )
    def list_proposals(
        ctx: Context,
        customer_id: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> dict[str, Any]:
        """Return pending human-review proposals owned by the caller's connector principal.

        ``customer_id`` is optional, but when supplied it is still checked
        against the caller's linked account list so enumeration attempts get the
        same fail-closed behavior as proposal creation and reporting tools.
        """
        principal_id = authenticated_principal_id(ctx)
        with _proposal_repositories(context, proposals, principal_id) as (accounts, scoped):
            if customer_id is not None:
                validate_customer_id(customer_id)
                _verify_account_ownership(accounts, principal_id, customer_id)
            if not (1 <= limit <= MAX_LIST_LIMIT):
                raise ApprovalError(
                    "invalid_limit", f"limit 1 ile {MAX_LIST_LIMIT} arasinda olmalidir."
                )
            page = scoped.list_pending(principal_id, customer_id=customer_id, limit=limit)
            return {
                "proposals": [proposal_to_dict(proposal) for proposal in page.proposals],
                # Cursor-based continuation is HTTP-only for now (docs/API_DESIGN.md "Pagination
                # sozlesmesi", todo.md 6.1 still open for the MCP tool contract) -- ``has_more``
                # at least tells the caller a truncated page happened instead of silently implying
                # completeness.
                "has_more": page.has_more,
            }

    for tool_name in ("prepare_proposal", "get_proposal", "list_proposals"):
        close_input_schema(mcp, tool_name)
        set_output_schema(mcp, tool_name, TOOL_OUTPUT_SCHEMAS[tool_name])
