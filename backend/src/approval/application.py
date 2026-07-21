"""Application service for the fail-closed approved-mutation boundary."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from ..db.models import AuditEvent, ExecutionStatus
from .domain import ExecutionReservation


@dataclass(frozen=True, slots=True)
class MutationOutcome:
    """A classified provider outcome returned by a Google Ads adapter."""

    status: ExecutionStatus
    google_request_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    """Result of atomically claiming an idempotent execution key."""

    execution_id: str
    created: bool
    status: ExecutionStatus
    google_request_id: str | None = None


class MutationAdapter(Protocol):
    """Narrow provider boundary used only after approval and audit checks pass."""

    def apply(
        self, reservation: ExecutionReservation, payload: Mapping[str, Any]
    ) -> MutationOutcome:
        """Apply one already-approved mutation and return its classified outcome."""
        ...


class ExecutionStore(Protocol):
    """Persistence operations required by the execution application service."""

    def record(self, reservation: ExecutionReservation, before: str, after: str) -> ExecutionClaim:
        """Atomically claim a reservation or return its existing result."""
        ...

    def mark_result(
        self,
        principal_id: str,
        execution_id: str,
        status: ExecutionStatus,
        google_request_id: str | None,
    ) -> None:
        """Persist the classified provider result within its principal scope."""
        ...


class AuditStore(Protocol):
    """Append-only audit operation required before a provider mutation."""

    def insert(self, event: AuditEvent) -> None:
        """Append an audit event or raise when persistence is unavailable."""
        ...


def execute_reserved_mutation(
    reservation: ExecutionReservation,
    *,
    payload: Mapping[str, Any],
    before_json: str,
    after_json: str,
    actor: str,
    correlation_id: str,
    executions: ExecutionStore,
    audit: AuditStore,
    adapter: MutationAdapter,
) -> MutationOutcome:
    """Persist reservation and start audit before invoking the mutation adapter.

    An audit failure propagates and therefore prevents the provider call. Unexpected
    adapter exceptions are conservatively persisted as ``unknown`` and re-raised;
    callers must reconcile rather than blindly retrying the mutation.
    """
    if not actor or not correlation_id:
        raise ValueError("actor ve correlation_id zorunludur")

    claim = executions.record(reservation, before=before_json, after=after_json)
    if not claim.created:
        return MutationOutcome(
            status=claim.status,
            google_request_id=claim.google_request_id,
            reason_code="idempotent_replay",
        )

    execution_id = claim.execution_id
    try:
        audit.insert(
            _audit_event(
                reservation,
                execution_id=execution_id,
                actor=actor,
                correlation_id=correlation_id,
                event_type="execution.started",
                outcome="pending",
            )
        )
    except Exception:
        # The provider was never called, so this outcome is deterministically failed,
        # not unknown. Persisting it also prevents an idempotent replay from exposing
        # a permanently pending execution or attempting the mutation without an audit.
        executions.mark_result(reservation.principal_id, execution_id, ExecutionStatus.FAILED, None)
        raise

    try:
        outcome = adapter.apply(reservation, payload)
    except Exception as error:
        executions.mark_result(
            reservation.principal_id, execution_id, ExecutionStatus.UNKNOWN, None
        )
        try:
            audit.insert(
                _audit_event(
                    reservation,
                    execution_id=execution_id,
                    actor=actor,
                    correlation_id=correlation_id,
                    event_type="execution.completed",
                    outcome=ExecutionStatus.UNKNOWN.value,
                    reason_code="adapter_exception",
                )
            )
        except Exception as audit_error:
            raise audit_error from error
        raise

    if outcome.status not in {
        ExecutionStatus.APPLIED,
        ExecutionStatus.FAILED,
        ExecutionStatus.UNKNOWN,
    }:
        executions.mark_result(
            reservation.principal_id,
            execution_id,
            ExecutionStatus.UNKNOWN,
            outcome.google_request_id,
        )
        audit.insert(
            _audit_event(
                reservation,
                execution_id=execution_id,
                actor=actor,
                correlation_id=correlation_id,
                event_type="execution.completed",
                outcome=ExecutionStatus.UNKNOWN.value,
                reason_code="invalid_adapter_outcome",
                google_request_id=outcome.google_request_id,
            )
        )
        raise ValueError("adapter terminal bir mutation sonucu döndürmelidir")

    executions.mark_result(
        reservation.principal_id, execution_id, outcome.status, outcome.google_request_id
    )
    try:
        audit.insert(
            _audit_event(
                reservation,
                execution_id=execution_id,
                actor=actor,
                correlation_id=correlation_id,
                event_type="execution.completed",
                outcome=outcome.status.value,
                reason_code=outcome.reason_code,
                google_request_id=outcome.google_request_id,
            )
        )
    except Exception:
        executions.mark_result(
            reservation.principal_id,
            execution_id,
            ExecutionStatus.UNKNOWN,
            outcome.google_request_id,
        )
        raise
    return outcome


def _audit_event(
    reservation: ExecutionReservation,
    *,
    execution_id: str,
    actor: str,
    correlation_id: str,
    event_type: str,
    outcome: str,
    reason_code: str | None = None,
    google_request_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=str(uuid.uuid4()),
        occurred_at=datetime.now(UTC),
        actor=actor,
        principal_id=reservation.principal_id,
        customer_id=reservation.customer_id,
        event_type=event_type,
        proposal_id=reservation.proposal_id,
        approval_id=None,
        execution_id=execution_id,
        outcome=outcome,
        reason_code=reason_code,
        correlation_id=correlation_id,
        google_request_id=google_request_id,
    )
