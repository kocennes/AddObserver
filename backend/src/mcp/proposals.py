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
from datetime import datetime, timedelta, timezone
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from ..api.errors import AdsApiError, ErrorClass
from ..approval import ApprovalError, Proposal, build_proposal_payload, submit_proposal
from ..db.proposals import ProposalRepository
from ..db.repository import AdsAccountRepository
from .tool_support import LOCAL_WRITE, READ_ONLY_LOCAL, authenticated_principal_id, close_input_schema
from .tools import MCPToolContext

#: Bounds on how long a prepared proposal stays actionable before it expires
#: (docs/DATA_MODEL.md freshness -- an approval older than this is worthless
#: since the underlying Google Ads state may have moved on).
MIN_EXPIRY_MINUTES = 5
MAX_EXPIRY_MINUTES = 24 * 60
DEFAULT_EXPIRY_MINUTES = 60


def _verify_account_ownership(context: MCPToolContext, principal_id: str, customer_id: str) -> None:
    """Same ownership check and error shape as ``credentials.resolve_google_ads_credentials``.

    A proposal never needs Google Ads credentials (it makes no API call), but it
    must still be scoped to an account the caller has actually linked -- otherwise
    a principal could draft proposals against a ``customer_id`` it cannot access.
    """
    account = AdsAccountRepository(context.conn).get_account(principal_id, customer_id)
    if account is None:
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="account_not_linked",
            message="Bu customer_id bu baglantiya ait degil veya henuz baglanmamis.",
            request_id=None,
        )


def _proposal_to_dict(proposal: Proposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "customer_id": proposal.customer_id,
        "status": proposal.status.value,
        "proposal_hash": proposal.proposal_hash,
        "expires_at": proposal.expires_at.isoformat(),
        "payload": dict(proposal.payload),
    }


def register_proposal_tools(mcp: FastMCP, context: MCPToolContext, proposals: ProposalRepository) -> None:
    """Register the Faz 1 proposal-preparation tools and close each input schema."""

    @mcp.tool(title="Değişiklik önerisi hazırla", annotations=LOCAL_WRITE, structured_output=False)
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
        """Draft a Faz 1.1 allowlist proposal (campaign pause/enable or budget update) for human review.

        This never calls Google Ads and never applies anything by itself -- it
        only stores a draft the account owner must separately approve before any
        write can happen.
        """
        principal_id = authenticated_principal_id(ctx)
        _verify_account_ownership(context, principal_id, customer_id)
        if not (MIN_EXPIRY_MINUTES <= expires_in_minutes <= MAX_EXPIRY_MINUTES):
            raise ApprovalError(
                "invalid_expiry",
                f"expires_in_minutes {MIN_EXPIRY_MINUTES} ile {MAX_EXPIRY_MINUTES} arasinda olmalidir.",
            )

        payload = build_proposal_payload(
            proposal_type=proposal_type,
            campaign_id=campaign_id,
            rationale=rationale,
            current_status=current_status,
            current_budget_amount_micros=current_budget_amount_micros,
            proposed_budget_amount_micros=proposed_budget_amount_micros,
        )

        now = datetime.now(timezone.utc)
        draft = Proposal.create(
            proposal_id=str(uuid.uuid4()),
            principal_id=principal_id,
            customer_id=customer_id,
            payload=payload,
            expires_at=now + timedelta(minutes=expires_in_minutes),
        )
        pending = submit_proposal(draft, now=now)
        proposals.save(pending)
        return _proposal_to_dict(pending)

    @mcp.tool(title="Öneri durumunu getir", annotations=READ_ONLY_LOCAL, structured_output=False)
    def get_proposal(ctx: Context, proposal_id: str) -> dict[str, Any]:
        """Return a previously prepared proposal's current status, only if it belongs to the caller."""
        principal_id = authenticated_principal_id(ctx)
        proposal = proposals.get(principal_id, proposal_id)
        if proposal is None:
            raise ApprovalError("proposal_not_found", "Bu proposal_id bu baglantiya ait degil veya bulunamadi.")
        return _proposal_to_dict(proposal)

    for tool_name in ("prepare_proposal", "get_proposal"):
        close_input_schema(mcp, tool_name)
