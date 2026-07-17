"""Tests for backend.src.db.proposals: persistence around the approval domain state machine.

Bu testler backend.src.approval.domain'in ürettiği nesnelerin doğru saklanıp yalnız sahibi
principal tarafından okunabildiğini ve execution kaydının idempotency_key ile tek satıra
indirgendiğini doğrular (TESTING.md "Zorunlu güvenlik vakaları" madde 3 ve 8).
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import (
    Decision,
    Proposal,
    ProposalStatus,
    approve_proposal,
    reserve_execution,
    submit_proposal,
)
from backend.src.db.connection import connect
from backend.src.db.models import AuditEvent, ExecutionStatus
from backend.src.db.proposals import (
    ApprovalRepository,
    AuditRepository,
    ExecutionRepository,
    ProposalRepository,
)
from backend.src.db.repository import PrincipalRepository


class ProposalPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.proposals = ProposalRepository(self.conn)
        self.approvals = ApprovalRepository(self.conn)
        self.audit = AuditRepository(self.conn)
        self.executions = ExecutionRepository(self.conn)
        self.principal_a = self.principals.get_or_create("iss", "user-a")
        self.principal_b = self.principals.get_or_create("iss", "user-b")
        self.now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        self.payload = {"type": "campaign_budget_update", "after": {"amount_micros": 5_000_000}}

    def tearDown(self) -> None:
        self.conn.close()

    def _pending_proposal(self, principal_id: str | None = None) -> Proposal:
        draft = Proposal.create(
            proposal_id="proposal-1",
            principal_id=principal_id or self.principal_a.id,
            customer_id="1234567890",
            payload=self.payload,
            expires_at=self.now + timedelta(minutes=30),
        )
        return submit_proposal(draft, now=self.now)

    def _pending_proposal_with(
        self,
        *,
        proposal_id: str,
        principal_id: str | None = None,
        customer_id: str = "1234567890",
        expires_at: datetime | None = None,
    ) -> Proposal:
        draft = Proposal.create(
            proposal_id=proposal_id,
            principal_id=principal_id or self.principal_a.id,
            customer_id=customer_id,
            payload=self.payload,
            expires_at=expires_at or self.now + timedelta(minutes=30),
        )
        return submit_proposal(draft, now=self.now)

    def test_round_trip_preserves_hash_and_payload(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        loaded = self.proposals.get(self.principal_a.id, "proposal-1")
        self.assertEqual(loaded.proposal_hash, pending.proposal_hash)
        self.assertEqual(dict(loaded.payload), dict(pending.payload))
        self.assertEqual(loaded.status, pending.status)

    def test_cross_principal_read_returns_none(self) -> None:
        """IDOR koruması: başka principal'ın proposal'ı görünmez."""
        self.proposals.save(self._pending_proposal())
        self.assertIsNone(self.proposals.get(self.principal_b.id, "proposal-1"))

    def test_list_pending_scopes_filters_and_limits_results(self) -> None:
        first = self._pending_proposal_with(proposal_id="proposal-1", customer_id="1234567890")
        second = self._pending_proposal_with(proposal_id="proposal-2", customer_id="2222222222")
        other = self._pending_proposal_with(proposal_id="proposal-3", principal_id=self.principal_b.id)
        self.proposals.save(first)
        self.proposals.save(second)
        self.proposals.save(other)

        listed = self.proposals.list_pending(self.principal_a.id, limit=1, now=self.now)
        self.assertEqual([proposal.proposal_id for proposal in listed], ["proposal-1"])

        filtered = self.proposals.list_pending(self.principal_a.id, customer_id="2222222222", now=self.now)
        self.assertEqual([proposal.proposal_id for proposal in filtered], ["proposal-2"])

    def test_list_pending_hides_rows_that_expired_after_submission(self) -> None:
        fresh = self._pending_proposal_with(proposal_id="proposal-1")
        expired = self._pending_proposal_with(
            proposal_id="proposal-2",
            expires_at=self.now + timedelta(minutes=1),
        )
        self.proposals.save(fresh)
        self.proposals.save(expired)

        listed = self.proposals.list_pending(self.principal_a.id, now=self.now + timedelta(minutes=2))
        self.assertEqual([proposal.proposal_id for proposal in listed], ["proposal-1"])
        stored_expired = self.proposals.get(self.principal_a.id, "proposal-2")
        self.assertEqual(stored_expired.status.value, "pending_approval")

    def test_list_pending_rejects_invalid_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "pozitif"):
            self.proposals.list_pending(self.principal_a.id, limit=0)
        with self.assertRaisesRegex(ValueError, "en fazla"):
            self.proposals.list_pending(self.principal_a.id, limit=101)
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.proposals.list_pending(self.principal_a.id, now=datetime(2026, 7, 17, 12))

    def test_cross_principal_save_cannot_modify_existing_proposal(self) -> None:
        """A colliding external id cannot become a cross-principal write primitive."""
        original = self._pending_proposal()
        self.proposals.save(original)
        foreign = self._pending_proposal(self.principal_b.id)

        with self.assertRaisesRegex(ValueError, "farkli bir principal"):
            self.proposals.save(foreign)

        loaded = self.proposals.get(self.principal_a.id, original.proposal_id)
        self.assertEqual(original.principal_id, loaded.principal_id)
        self.assertEqual(original.proposal_hash, loaded.proposal_hash)
        self.assertEqual(original.status, loaded.status)

    def test_cross_customer_save_cannot_move_existing_proposal(self) -> None:
        original = self._pending_proposal()
        self.proposals.save(original)
        foreign_customer = Proposal.create(
            proposal_id=original.proposal_id,
            principal_id=self.principal_a.id,
            customer_id="9999999999",
            payload={"type": "campaign_budget_update", "after": {"amount_micros": 1}},
            expires_at=self.now + timedelta(minutes=30),
        )

        with self.assertRaisesRegex(ValueError, "customer kapsaminda"):
            self.proposals.save(foreign_customer)

        loaded = self.proposals.get(self.principal_a.id, original.proposal_id)
        self.assertEqual(original.customer_id, loaded.customer_id)
        self.assertEqual(original.proposal_hash, loaded.proposal_hash)

    def test_same_scope_save_cannot_replace_proposal_payload(self) -> None:
        original = self._pending_proposal()
        self.proposals.save(original)
        changed = submit_proposal(
            Proposal.create(
                proposal_id=original.proposal_id,
                principal_id=self.principal_a.id,
                customer_id=original.customer_id,
                payload={"type": "campaign_budget_update", "after": {"amount_micros": 9_000_000}},
                expires_at=original.expires_at,
            ),
            now=self.now,
        )

        with self.assertRaisesRegex(ValueError, "payload/hash"):
            self.proposals.save(changed)

        loaded = self.proposals.get(self.principal_a.id, original.proposal_id)
        self.assertEqual(original.proposal_hash, loaded.proposal_hash)
        self.assertEqual(dict(original.payload), dict(loaded.payload))

    def test_save_after_approval_updates_status(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        executing, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        self.proposals.save(executing)
        self.approvals.save(approval)

        self.assertEqual(self.proposals.get(self.principal_a.id, "proposal-1").status, executing.status)
        latest = self.approvals.get_latest(self.principal_a.id, "proposal-1")
        self.assertEqual(latest.decision, Decision.APPROVE)

    def test_save_decision_with_audit_is_atomic(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        approval_id = self.approvals.save_decision_with_audit(
            approved,
            approval,
            AuditEvent(
                event_id="evt-approval-1",
                occurred_at=self.now,
                actor=self.principal_a.id,
                principal_id=self.principal_a.id,
                customer_id=approved.customer_id,
                event_type="approval.decided",
                proposal_id=approved.proposal_id,
                approval_id=None,
                execution_id=None,
                outcome=Decision.APPROVE.value,
                reason_code=None,
                correlation_id="corr-approval-1",
                google_request_id=None,
            ),
        )

        self.assertEqual(self.proposals.get(self.principal_a.id, "proposal-1").status, approved.status)
        latest = self.approvals.get_latest(self.principal_a.id, "proposal-1")
        assert latest is not None
        self.assertEqual(latest.decision, Decision.APPROVE)
        events = self.audit.list_for_principal(self.principal_a.id)
        self.assertEqual(events[0].event_type, "approval.decided")
        self.assertEqual(events[0].approval_id, approval_id)
        self.assertEqual(events[0].correlation_id, "corr-approval-1")

    def test_save_decision_with_audit_rolls_back_on_audit_failure(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        self.audit.insert(AuditEvent(
            event_id="evt-duplicate",
            occurred_at=self.now,
            actor=self.principal_a.id,
            principal_id=self.principal_a.id,
            customer_id=pending.customer_id,
            event_type="seed",
            proposal_id=None,
            approval_id=None,
            execution_id=None,
            outcome="ok",
            reason_code=None,
            correlation_id="corr-seed",
            google_request_id=None,
        ))
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )

        with self.assertRaises(sqlite3.IntegrityError):
            self.approvals.save_decision_with_audit(
                approved,
                approval,
                AuditEvent(
                    event_id="evt-duplicate",
                    occurred_at=self.now,
                    actor=self.principal_a.id,
                    principal_id=self.principal_a.id,
                    customer_id=approved.customer_id,
                    event_type="approval.decided",
                    proposal_id=approved.proposal_id,
                    approval_id=None,
                    execution_id=None,
                    outcome=Decision.APPROVE.value,
                    reason_code=None,
                    correlation_id="corr-approval-1",
                    google_request_id=None,
                ),
            )

        self.assertEqual(
            self.proposals.get(self.principal_a.id, "proposal-1").status,
            ProposalStatus.PENDING_APPROVAL,
        )
        self.assertIsNone(self.approvals.get_latest(self.principal_a.id, "proposal-1"))

    def test_save_decision_with_audit_cannot_advance_tampered_payload_hash(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        tampered_pending = submit_proposal(
            Proposal.create(
                proposal_id=pending.proposal_id,
                principal_id=self.principal_a.id,
                customer_id=pending.customer_id,
                payload={"type": "campaign_budget_update", "after": {"amount_micros": 9_000_000}},
                expires_at=pending.expires_at,
            ),
            now=self.now,
        )
        tampered_approved, tampered_approval = approve_proposal(
            tampered_pending,
            principal_id=self.principal_a.id,
            approver_id=self.principal_a.id,
            decision=Decision.APPROVE,
            now=self.now,
        )

        with self.assertRaisesRegex(ValueError, "hash"):
            self.approvals.save_decision_with_audit(
                tampered_approved,
                tampered_approval,
                AuditEvent(
                    event_id="evt-tampered",
                    occurred_at=self.now,
                    actor=self.principal_a.id,
                    principal_id=self.principal_a.id,
                    customer_id=tampered_approved.customer_id,
                    event_type="approval.decided",
                    proposal_id=tampered_approved.proposal_id,
                    approval_id=None,
                    execution_id=None,
                    outcome=Decision.APPROVE.value,
                    reason_code=None,
                    correlation_id="corr-tampered",
                    google_request_id=None,
                ),
            )

        self.assertEqual(
            self.proposals.get(self.principal_a.id, pending.proposal_id).status,
            ProposalStatus.PENDING_APPROVAL,
        )
        self.assertIsNone(self.approvals.get_latest(self.principal_a.id, pending.proposal_id))

    def test_save_decision_with_audit_rejects_mismatched_audit_event(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )

        with self.assertRaisesRegex(ValueError, "approval karari"):
            self.approvals.save_decision_with_audit(
                approved,
                approval,
                AuditEvent(
                    event_id="evt-approval-1",
                    occurred_at=self.now,
                    actor=self.principal_a.id,
                    principal_id=self.principal_a.id,
                    customer_id=approved.customer_id,
                    event_type="approval.decided",
                    proposal_id=approved.proposal_id,
                    approval_id=None,
                    execution_id=None,
                    outcome=Decision.REJECT.value,
                    reason_code=None,
                    correlation_id="corr-approval-1",
                    google_request_id=None,
                ),
            )

        self.assertEqual(
            self.proposals.get(self.principal_a.id, "proposal-1").status,
            ProposalStatus.PENDING_APPROVAL,
        )
        self.assertIsNone(self.approvals.get_latest(self.principal_a.id, "proposal-1"))

    def test_approval_not_visible_to_other_principal(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        _, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        self.approvals.save(approval)
        self.assertIsNone(self.approvals.get_latest(self.principal_b.id, "proposal-1"))

    def test_approval_cannot_reference_another_principals_proposal(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        _, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        foreign = type(approval)(
            proposal_id=approval.proposal_id,
            principal_id=self.principal_b.id,
            approver_id=self.principal_b.id,
            decision=approval.decision,
            proposal_hash=approval.proposal_hash,
            decided_at=approval.decided_at,
        )

        with self.assertRaisesRegex(ValueError, "proposal ve principal"):
            self.approvals.save(foreign)
        self.assertIsNone(self.approvals.get_latest(self.principal_b.id, pending.proposal_id))

    def test_duplicate_idempotency_key_reuses_same_execution_row(self) -> None:
        """Zorunlu vaka: duplicate idempotency key tek execution üretir."""
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        self.approvals.save(approval)
        _, reservation = reserve_execution(
            approved, approval, principal_id=self.principal_a.id,
            current_payload=self.payload, idempotency_key="request-1", now=self.now,
        )
        first = self.executions.record(reservation, before="{}", after="{}")
        second = self.executions.record(reservation, before="{}", after="{}")
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.execution_id, second.execution_id)

    def test_idempotency_key_cannot_cross_principal_scope(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        _, reservation = reserve_execution(
            approved, approval, principal_id=self.principal_a.id,
            current_payload=self.payload, idempotency_key="shared-key", now=self.now,
        )
        self.executions.record(reservation, before="{}", after="{}")
        foreign = type(reservation)(
            proposal_id=reservation.proposal_id, principal_id=self.principal_b.id,
            customer_id=reservation.customer_id, proposal_hash=reservation.proposal_hash,
            idempotency_key=reservation.idempotency_key, reserved_at=reservation.reserved_at,
        )
        with self.assertRaisesRegex(ValueError, "principal"):
            self.executions.record(foreign, before="{}", after="{}")

    def test_new_execution_cannot_reference_another_principals_proposal(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        _, reservation = reserve_execution(
            approved, approval, principal_id=self.principal_a.id,
            current_payload=self.payload, idempotency_key="foreign-first-use", now=self.now,
        )
        foreign = type(reservation)(
            proposal_id=reservation.proposal_id, principal_id=self.principal_b.id,
            customer_id=reservation.customer_id, proposal_hash=reservation.proposal_hash,
            idempotency_key=reservation.idempotency_key, reserved_at=reservation.reserved_at,
        )

        with self.assertRaisesRegex(ValueError, "proposal ve principal"):
            self.executions.record(foreign, before="{}", after="{}")
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0])

    def test_execution_reservation_must_match_proposal_snapshot(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        _, reservation = reserve_execution(
            approved, approval, principal_id=self.principal_a.id,
            current_payload=self.payload, idempotency_key="stale-snapshot", now=self.now,
        )
        stale = type(reservation)(
            proposal_id=reservation.proposal_id, principal_id=reservation.principal_id,
            customer_id="9999999999", proposal_hash=reservation.proposal_hash,
            idempotency_key=reservation.idempotency_key, reserved_at=reservation.reserved_at,
        )

        with self.assertRaisesRegex(ValueError, "proposal snapshot"):
            self.executions.record(stale, before="{}", after="{}")
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM execution").fetchone()[0])

    def test_mark_result_updates_status_and_request_id(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        self.approvals.save(approval)
        _, reservation = reserve_execution(
            approved, approval, principal_id=self.principal_a.id,
            current_payload=self.payload, idempotency_key="request-2", now=self.now,
        )
        claim = self.executions.record(reservation, before="{}", after="{}")
        self.executions.mark_result(
            self.principal_a.id, claim.execution_id, ExecutionStatus.APPLIED, "gads-req-1"
        )
        row = self.conn.execute(
            "SELECT status, google_request_id FROM execution WHERE id = ?", (claim.execution_id,)
        ).fetchone()
        self.assertEqual(row["status"], ExecutionStatus.APPLIED.value)
        self.assertEqual(row["google_request_id"], "gads-req-1")

    def test_mark_result_cannot_update_another_principals_execution(self) -> None:
        pending = self._pending_proposal()
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending, principal_id=self.principal_a.id, approver_id=self.principal_a.id,
            decision=Decision.APPROVE, now=self.now,
        )
        _, reservation = reserve_execution(
            approved, approval, principal_id=self.principal_a.id,
            current_payload=self.payload, idempotency_key="scoped-result", now=self.now,
        )
        claim = self.executions.record(reservation, before="{}", after="{}")

        with self.assertRaisesRegex(ValueError, "principal kapsaminda"):
            self.executions.mark_result(
                self.principal_b.id, claim.execution_id, ExecutionStatus.APPLIED, "foreign-request"
            )

        row = self.conn.execute(
            "SELECT status, google_request_id FROM execution WHERE id = ?", (claim.execution_id,)
        ).fetchone()
        self.assertEqual(ExecutionStatus.PENDING.value, row["status"])
        self.assertIsNone(row["google_request_id"])


class AuditAppendOnlyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.audit = AuditRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_audit_repository_has_no_mutation_methods(self) -> None:
        """Zorunlu vaka: audit append-only'dir; update/delete API yüzeyinde yoktur."""
        public_methods = {name for name in dir(self.audit) if not name.startswith("_")}
        self.assertEqual(public_methods, {"insert", "list_for_principal"})

    def test_insert_and_list_round_trip(self) -> None:
        event = AuditEvent(
            event_id="evt-1",
            occurred_at=datetime.now(timezone.utc),
            actor="system",
            principal_id="principal-1",
            customer_id="1234567890",
            event_type="proposal.created",
            proposal_id=None,
            approval_id=None,
            execution_id="execution-1",
            outcome="success",
            reason_code=None,
            correlation_id="corr-1",
            google_request_id=None,
        )
        self.audit.insert(event)
        events = self.audit.list_for_principal("principal-1")
        self.assertEqual([e.event_id for e in events], ["evt-1"])
        self.assertEqual("execution-1", events[0].execution_id)

    def test_events_scoped_to_principal(self) -> None:
        for principal_id, event_id in (("principal-1", "evt-1"), ("principal-2", "evt-2")):
            self.audit.insert(AuditEvent(
                event_id=event_id, occurred_at=datetime.now(timezone.utc), actor="system",
                principal_id=principal_id, customer_id=None, event_type="proposal.created",
                proposal_id=None, approval_id=None, execution_id=None, outcome="success", reason_code=None,
                correlation_id="corr", google_request_id=None,
            ))
        self.assertEqual([e.event_id for e in self.audit.list_for_principal("principal-1")], ["evt-1"])


if __name__ == "__main__":
    unittest.main()
