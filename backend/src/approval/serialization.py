"""Stable external serialization helpers for proposal reads."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .domain import ApprovalError, Proposal, ProposalStatus


def proposal_status_for_read(proposal: Proposal, *, now: datetime | None = None) -> str:
    """Return the externally visible proposal status, including time-based expiry."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ApprovalError("invalid_time", "now timezone bilgisi icermelidir.")
    if proposal.status is ProposalStatus.PENDING_APPROVAL and current_time >= proposal.expires_at:
        return ProposalStatus.EXPIRED.value
    return proposal.status.value


def proposal_to_dict(proposal: Proposal) -> dict[str, Any]:
    """Serialize a proposal for MCP and HTTP read endpoints without exposing owner internals."""
    return {
        "proposal_id": proposal.proposal_id,
        "customer_id": proposal.customer_id,
        "status": proposal_status_for_read(proposal),
        "proposal_hash": proposal.proposal_hash,
        "expires_at": proposal.expires_at.isoformat(),
        "payload": dict(proposal.payload),
    }
