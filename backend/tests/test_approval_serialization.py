"""Tests for stable proposal serialization shared by MCP and HTTP reads."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import (
    ApprovalError,
    Proposal,
    ProposalStatus,
    proposal_status_for_read,
    proposal_to_dict,
    submit_proposal,
)


class ProposalSerializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 17, 12, tzinfo=UTC)
        self.payload = {
            "schema_version": "1",
            "type": "campaign_pause",
            "resource_name": "customers/1234567890/campaigns/42",
            "before": {"status": "ENABLED"},
            "after": {"status": "PAUSED"},
            "reason": "test",
            "evidence_refs": [],
            "risk": "low",
        }

    def _proposal(self, *, expires_at: datetime | None = None) -> Proposal:
        draft = Proposal.create(
            proposal_id="proposal-1",
            principal_id="principal-a",
            customer_id="1234567890",
            payload=self.payload,
            expires_at=expires_at or self.now + timedelta(minutes=30),
        )
        return submit_proposal(draft, now=self.now)

    def test_proposal_status_for_read_reports_time_based_expiry_without_mutating_status(
        self,
    ) -> None:
        proposal = self._proposal(expires_at=self.now + timedelta(minutes=1))

        status = proposal_status_for_read(proposal, now=self.now + timedelta(minutes=2))

        self.assertEqual(status, ProposalStatus.EXPIRED.value)
        self.assertEqual(proposal.status, ProposalStatus.PENDING_APPROVAL)

    def test_proposal_status_for_read_rejects_naive_now(self) -> None:
        proposal = self._proposal()

        with self.assertRaises(ApprovalError) as caught:
            proposal_status_for_read(proposal, now=datetime(2026, 7, 17, 12))

        self.assertEqual(caught.exception.code, "invalid_time")

    def test_proposal_to_dict_excludes_principal_and_includes_payload_snapshot(self) -> None:
        proposal = self._proposal(expires_at=datetime.now(UTC) + timedelta(minutes=30))

        serialized = proposal_to_dict(proposal)

        self.assertNotIn("principal_id", serialized)
        self.assertEqual(serialized["proposal_id"], "proposal-1")
        self.assertEqual(serialized["customer_id"], "1234567890")
        self.assertEqual(serialized["status"], "pending_approval")
        self.assertEqual(serialized["payload"], self.payload)
        self.assertEqual(serialized["proposal_hash"], proposal.proposal_hash)
        self.assertEqual(serialized["expires_at"], proposal.expires_at.isoformat())


if __name__ == "__main__":
    unittest.main()
