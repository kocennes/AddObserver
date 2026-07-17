"""Persistence for backend.src.approval.domain objects and the append-only audit log.

Onay/hash/süre/sahiplik doğrulaması burada TEKRARLANMAZ — bu modül yalnız
``backend.src.approval.domain``'in ürettiği immutable ``Proposal``/``Approval``/
``ExecutionReservation`` nesnelerini saklar ve principal_id ile filtrelenmiş okur. İş
kurallarının tek kaynağı ``backend/src/approval/domain.py`` olarak kalır.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from types import MappingProxyType

from ..approval import (
    Approval,
    Decision,
    ExecutionClaim,
    ExecutionReservation,
    Proposal,
    ProposalStatus,
)
from .models import AuditEvent, ExecutionStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProposalRepository:
    """Stores Proposal snapshots; a status transition re-saves the same row by id."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, proposal: Proposal) -> None:
        """Insert or update only within the proposal's immutable owner/account scope."""
        cursor = self._conn.execute(
            "INSERT INTO proposal (id, principal_id, customer_id, payload, proposal_hash, status, "
            "expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status, payload = excluded.payload, "
            "proposal_hash = excluded.proposal_hash "
            "WHERE proposal.principal_id = excluded.principal_id "
            "AND proposal.customer_id = excluded.customer_id",
            (
                proposal.proposal_id, proposal.principal_id, proposal.customer_id,
                json.dumps(dict(proposal.payload)), proposal.proposal_hash, proposal.status.value,
                proposal.expires_at.isoformat(), _now(),
            ),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise ValueError("proposal_id farkli bir principal veya customer kapsaminda kullanilmis")
        self._conn.commit()

    def get(self, principal_id: str, proposal_id: str) -> Proposal | None:
        """Return the proposal only if it belongs to ``principal_id`` (cross-principal reads return None)."""
        row = self._conn.execute(
            "SELECT * FROM proposal WHERE id = ? AND principal_id = ?", (proposal_id, principal_id)
        ).fetchone()
        return None if row is None else _proposal_from_row(row)

    def list_pending(self, principal_id: str) -> list[Proposal]:
        """Return this principal's proposals awaiting a human decision, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM proposal WHERE principal_id = ? AND status = ? ORDER BY created_at",
            (principal_id, ProposalStatus.PENDING_APPROVAL.value),
        ).fetchall()
        return [_proposal_from_row(row) for row in rows]


def _proposal_from_row(row: sqlite3.Row) -> Proposal:
    return Proposal(
        proposal_id=row["id"],
        principal_id=row["principal_id"],
        customer_id=row["customer_id"],
        payload=MappingProxyType(json.loads(row["payload"])),
        proposal_hash=row["proposal_hash"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        status=ProposalStatus(row["status"]),
    )


class ApprovalRepository:
    """Stores every human decision. One proposal may accumulate several rows (rejections retried)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, approval: Approval) -> None:
        """Store a decision only when proposal and principal ownership match."""
        owner = self._conn.execute(
            "SELECT 1 FROM proposal WHERE id = ? AND principal_id = ?",
            (approval.proposal_id, approval.principal_id),
        ).fetchone()
        if owner is None:
            raise ValueError("approval proposal ve principal kapsami uyusmuyor")
        self._conn.execute(
            "INSERT INTO approval (id, proposal_id, principal_id, approver_id, decision, "
            "proposal_hash, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()), approval.proposal_id, approval.principal_id, approval.approver_id,
                approval.decision.value, approval.proposal_hash, approval.decided_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_latest(self, principal_id: str, proposal_id: str) -> Approval | None:
        """Return the most recent decision only if it belongs to ``principal_id``."""
        row = self._conn.execute(
            "SELECT * FROM approval WHERE proposal_id = ? AND principal_id = ? "
            "ORDER BY decided_at DESC LIMIT 1",
            (proposal_id, principal_id),
        ).fetchone()
        return None if row is None else _approval_from_row(row)


def _approval_from_row(row: sqlite3.Row) -> Approval:
    return Approval(
        proposal_id=row["proposal_id"],
        principal_id=row["principal_id"],
        approver_id=row["approver_id"],
        decision=Decision(row["decision"]),
        proposal_hash=row["proposal_hash"],
        decided_at=datetime.fromisoformat(row["decided_at"]),
    )


class ExecutionRepository:
    """Persists execution reservations and their eventual Google Ads outcome.

    ``record`` is idempotent on ``idempotency_key`` (SECURITY.md/TESTING.md — duplicate
    idempotency key tek execution üretir): a repeat call returns the existing row id instead
    of inserting a second one.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def record(
        self, reservation: ExecutionReservation, before: str, after: str
    ) -> ExecutionClaim:
        """Atomically claim a key; reject reuse for a different scoped operation."""
        owner = self._conn.execute(
            "SELECT customer_id, proposal_hash FROM proposal WHERE id = ? AND principal_id = ?",
            (reservation.proposal_id, reservation.principal_id),
        ).fetchone()
        if owner is None:
            raise ValueError("execution proposal ve principal kapsami uyusmuyor")
        if (
            owner["customer_id"] != reservation.customer_id
            or owner["proposal_hash"] != reservation.proposal_hash
        ):
            raise ValueError("execution reservation proposal snapshot'i ile uyusmuyor")
        execution_id = str(uuid.uuid4())
        cursor = self._conn.execute(
            "INSERT OR IGNORE INTO execution "
            "(id, proposal_id, principal_id, idempotency_key, before, after, "
            "google_request_id, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution_id, reservation.proposal_id, reservation.principal_id,
                reservation.idempotency_key, before, after, None,
                ExecutionStatus.PENDING.value, _now(),
            ),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM execution WHERE idempotency_key = ?", (reservation.idempotency_key,)
        ).fetchone()
        if row is None:
            raise RuntimeError("execution claim kaydedilemedi")
        expected = (reservation.proposal_id, reservation.principal_id, before, after)
        actual = (row["proposal_id"], row["principal_id"], row["before"], row["after"])
        if actual != expected:
            raise ValueError("idempotency_key farkli bir execution icin kullanilmis")
        return ExecutionClaim(
            execution_id=row["id"],
            created=cursor.rowcount == 1,
            status=ExecutionStatus(row["status"]),
            google_request_id=row["google_request_id"],
        )

    def mark_result(
        self,
        principal_id: str,
        execution_id: str,
        status: ExecutionStatus,
        google_request_id: str | None,
    ) -> None:
        """Record the Google Ads outcome. ``UNKNOWN`` is used when the mutate result is unclear
        (network failure after send) — ERROR_HANDLING.md forbids a blind retry in that case."""
        cursor = self._conn.execute(
            "UPDATE execution SET status = ?, google_request_id = ? "
            "WHERE id = ? AND principal_id = ?",
            (status.value, google_request_id, execution_id, principal_id),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise ValueError("execution sonucu principal kapsaminda bulunamadi")
        self._conn.commit()


class AuditRepository:
    """Append-only audit event store. Intentionally exposes no update/delete method."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def insert(self, event: AuditEvent) -> None:
        self._conn.execute(
            "INSERT INTO audit_event (event_id, occurred_at, actor, principal_id, customer_id, "
            "event_type, proposal_id, approval_id, execution_id, outcome, reason_code, "
            "correlation_id, google_request_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id, event.occurred_at.isoformat(), event.actor, event.principal_id,
                event.customer_id, event.event_type, event.proposal_id, event.approval_id,
                event.execution_id, event.outcome, event.reason_code, event.correlation_id,
                event.google_request_id,
            ),
        )
        self._conn.commit()

    def list_for_principal(self, principal_id: str) -> list[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM audit_event WHERE principal_id = ? ORDER BY occurred_at", (principal_id,)
        ).fetchall()
        return [_audit_from_row(row) for row in rows]


def _audit_from_row(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        event_id=row["event_id"],
        occurred_at=datetime.fromisoformat(row["occurred_at"]),
        actor=row["actor"],
        principal_id=row["principal_id"],
        customer_id=row["customer_id"],
        event_type=row["event_type"],
        proposal_id=row["proposal_id"],
        approval_id=row["approval_id"],
        execution_id=row["execution_id"],
        outcome=row["outcome"],
        reason_code=row["reason_code"],
        correlation_id=row["correlation_id"],
        google_request_id=row["google_request_id"],
    )
