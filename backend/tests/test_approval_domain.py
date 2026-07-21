"""Security-critical tests for the human-approval state machine."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import (
    ApprovalError,
    Decision,
    Proposal,
    ProposalStatus,
    approve_proposal,
    reserve_execution,
    submit_proposal,
)


class ApprovalDomainTests(unittest.TestCase):
    """Prove that execution cannot bypass ownership and immutable approval."""

    def setUp(self) -> None:
        self.now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        self.payload = {"type": "campaign_budget_update", "after": {"amount_micros": 5_000_000}}
        draft = Proposal.create(
            proposal_id="proposal-1",
            principal_id="principal-a",
            customer_id="1234567890",
            payload=self.payload,
            expires_at=self.now + timedelta(minutes=30),
        )
        self.pending = submit_proposal(draft, now=self.now)

    def _approved(self):
        return approve_proposal(
            self.pending,
            principal_id="principal-a",
            approver_id="principal-a",
            decision=Decision.APPROVE,
            now=self.now,
        )

    def test_execution_without_approval_is_rejected(self) -> None:
        """No approval means no execution reservation and therefore no mutate."""
        with self.assertRaisesRegex(ApprovalError, "açık insan onayı") as caught:
            reserve_execution(
                self.pending,
                None,
                principal_id="principal-a",
                current_payload=self.payload,
                idempotency_key="request-1",
                now=self.now,
            )
        self.assertEqual("approval_required", caught.exception.code)

    def test_cross_principal_approval_is_rejected(self) -> None:
        """A principal cannot decide on another principal's proposal."""
        with self.assertRaises(ApprovalError) as caught:
            approve_proposal(
                self.pending,
                principal_id="principal-b",
                approver_id="principal-b",
                decision=Decision.APPROVE,
                now=self.now,
            )
        self.assertEqual("ownership_mismatch", caught.exception.code)

    def test_changed_payload_invalidates_approval(self) -> None:
        """Any payload change after approval requires a new human decision."""
        proposal, approval = self._approved()
        changed = {"type": "campaign_budget_update", "after": {"amount_micros": 9_000_000}}
        with self.assertRaises(ApprovalError) as caught:
            reserve_execution(
                proposal,
                approval,
                principal_id="principal-a",
                current_payload=changed,
                idempotency_key="request-1",
                now=self.now,
            )
        self.assertEqual("proposal_changed", caught.exception.code)

    def test_expired_approval_cannot_be_executed(self) -> None:
        """Execution rechecks expiry instead of trusting the earlier decision."""
        proposal, approval = self._approved()
        with self.assertRaises(ApprovalError) as caught:
            reserve_execution(
                proposal,
                approval,
                principal_id="principal-a",
                current_payload=self.payload,
                idempotency_key="request-1",
                now=self.now + timedelta(hours=1),
            )
        self.assertEqual("proposal_expired", caught.exception.code)

    def test_valid_approval_reserves_execution(self) -> None:
        """An exact, current approval produces an immutable reservation."""
        proposal, approval = self._approved()
        executing, reservation = reserve_execution(
            proposal,
            approval,
            principal_id="principal-a",
            current_payload=self.payload,
            idempotency_key="request-1",
            now=self.now,
        )
        self.assertEqual(ProposalStatus.EXECUTING, executing.status)
        self.assertEqual(proposal.proposal_hash, reservation.proposal_hash)
        self.assertEqual("principal-a", reservation.principal_id)
