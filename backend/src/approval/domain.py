"""Fail-closed proposal and human-approval state transitions.

This module has no Google Ads dependency. Reserving an execution proves that an
immutable, unexpired approval exists; an adapter may perform a mutate only after
the reservation and audit-start record have both been persisted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class ProposalStatus(StrEnum):
    """Allowed proposal lifecycle states before provider execution."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTING = "executing"


class Decision(StrEnum):
    """Immutable decisions a human approver can make."""

    APPROVE = "approve"
    REJECT = "reject"


class ApprovalError(ValueError):
    """A safe, stable failure raised when an approval invariant is violated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApprovalError("invalid_time", "Zaman timezone bilgisi içermelidir.")
    return value.astimezone(timezone.utc)


def _freeze_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Detach a JSON payload from caller-owned mutable objects."""
    try:
        detached = json.loads(json.dumps(payload, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ApprovalError("invalid_payload", "Öneri payload'ı geçerli JSON olmalıdır.") from error
    if not isinstance(detached, dict):
        raise ApprovalError("invalid_payload", "Öneri payload'ı bir JSON object olmalıdır.")
    return MappingProxyType(detached)


def calculate_proposal_hash(payload: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 digest for a JSON proposal payload."""
    try:
        canonical = json.dumps(
            dict(payload),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ApprovalError("invalid_payload", "Öneri payload'ı geçerli JSON olmalıdır.") from error
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class Proposal:
    """An immutable, principal-scoped change proposal."""

    proposal_id: str
    principal_id: str
    customer_id: str
    payload: Mapping[str, Any]
    proposal_hash: str
    expires_at: datetime
    status: ProposalStatus = ProposalStatus.DRAFT

    @classmethod
    def create(
        cls,
        *,
        proposal_id: str,
        principal_id: str,
        customer_id: str,
        payload: Mapping[str, Any],
        expires_at: datetime,
    ) -> "Proposal":
        """Create a draft while copying and hashing its caller-provided payload."""
        if not proposal_id or not principal_id or not customer_id:
            raise ApprovalError("missing_identity", "Öneri kimlik alanları boş olamaz.")
        frozen_payload = _freeze_payload(payload)
        return cls(
            proposal_id=proposal_id,
            principal_id=principal_id,
            customer_id=customer_id,
            payload=frozen_payload,
            proposal_hash=calculate_proposal_hash(frozen_payload),
            expires_at=_utc(expires_at),
        )


@dataclass(frozen=True, slots=True)
class Approval:
    """An immutable human decision tied to an exact proposal hash."""

    proposal_id: str
    principal_id: str
    approver_id: str
    decision: Decision
    proposal_hash: str
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class ExecutionReservation:
    """Proof that execution invariants passed before audit/provider work."""

    proposal_id: str
    principal_id: str
    customer_id: str
    proposal_hash: str
    idempotency_key: str
    reserved_at: datetime


def submit_proposal(proposal: Proposal, *, now: datetime) -> Proposal:
    """Move a non-expired draft into the human decision queue."""
    current_time = _utc(now)
    if proposal.status is not ProposalStatus.DRAFT:
        raise ApprovalError("invalid_state", "Yalnız taslak öneri onaya gönderilebilir.")
    if current_time >= proposal.expires_at:
        return replace(proposal, status=ProposalStatus.EXPIRED)
    return replace(proposal, status=ProposalStatus.PENDING_APPROVAL)


def approve_proposal(
    proposal: Proposal,
    *,
    principal_id: str,
    approver_id: str,
    decision: Decision,
    now: datetime,
) -> tuple[Proposal, Approval]:
    """Record one human decision after ownership and expiry checks."""
    current_time = _utc(now)
    if proposal.principal_id != principal_id:
        raise ApprovalError("ownership_mismatch", "Öneri bu kullanıcıya ait değil.")
    if proposal.status is not ProposalStatus.PENDING_APPROVAL:
        raise ApprovalError("invalid_state", "Öneri karar beklemiyor.")
    if current_time >= proposal.expires_at:
        raise ApprovalError("proposal_expired", "Önerinin onay süresi dolmuş.")
    if not approver_id:
        raise ApprovalError("missing_approver", "Onaylayan kimliği gereklidir.")
    if not isinstance(decision, Decision):
        raise ApprovalError("invalid_decision", "Onay kararı geçersiz.")

    status = ProposalStatus.APPROVED if decision is Decision.APPROVE else ProposalStatus.REJECTED
    approval = Approval(
        proposal_id=proposal.proposal_id,
        principal_id=principal_id,
        approver_id=approver_id,
        decision=decision,
        proposal_hash=proposal.proposal_hash,
        decided_at=current_time,
    )
    return replace(proposal, status=status), approval


def reserve_execution(
    proposal: Proposal,
    approval: Approval | None,
    *,
    principal_id: str,
    current_payload: Mapping[str, Any],
    idempotency_key: str,
    now: datetime,
) -> tuple[Proposal, ExecutionReservation]:
    """Fail closed unless an exact, valid human approval permits execution."""
    current_time = _utc(now)
    if proposal.principal_id != principal_id:
        raise ApprovalError("ownership_mismatch", "Öneri bu kullanıcıya ait değil.")
    if approval is None or approval.decision is not Decision.APPROVE:
        raise ApprovalError("approval_required", "Uygulama için açık insan onayı gerekir.")
    if proposal.status is not ProposalStatus.APPROVED:
        raise ApprovalError("invalid_state", "Öneri uygulanabilir durumda değil.")
    if current_time >= proposal.expires_at:
        raise ApprovalError("proposal_expired", "Önerinin onay süresi dolmuş.")
    if approval.principal_id != principal_id or approval.proposal_id != proposal.proposal_id:
        raise ApprovalError("approval_mismatch", "Onay bu öneriye veya kullanıcıya ait değil.")
    current_hash = calculate_proposal_hash(current_payload)
    if approval.proposal_hash != proposal.proposal_hash or current_hash != proposal.proposal_hash:
        raise ApprovalError("proposal_changed", "Öneri onaydan sonra değişmiş.")
    if not idempotency_key:
        raise ApprovalError("missing_idempotency_key", "Idempotency anahtarı gereklidir.")

    reservation = ExecutionReservation(
        proposal_id=proposal.proposal_id,
        principal_id=principal_id,
        customer_id=proposal.customer_id,
        proposal_hash=proposal.proposal_hash,
        idempotency_key=idempotency_key,
        reserved_at=current_time,
    )
    return replace(proposal, status=ProposalStatus.EXECUTING), reservation
