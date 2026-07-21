"""Tests for backend.src.approval.payload_schema: the Faz 1.1 allowlist gate.

docs/DATA_MODEL.md requires proposal payloads to be validated against a
versioned schema rather than accepted as free-form JSON; these tests prove
that only the two docs/PRODUCT.md Faz 1.1 allowlist operations can produce a
payload, and that every other shape fails closed with a stable error code.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import PROPOSAL_SCHEMA_VERSION, ApprovalError, build_proposal_payload
from backend.src.approval.payload_schema import MAX_CAMPAIGN_ID_DIGITS, MAX_RATIONALE_LENGTH


class BuildProposalPayloadTests(unittest.TestCase):
    def test_campaign_pause_produces_before_after_status(self) -> None:
        payload = build_proposal_payload(
            proposal_type="campaign_pause",
            campaign_id="5555",
            rationale="Performans dusuk.",
            current_status="ENABLED",
        )
        self.assertEqual(payload["schema_version"], PROPOSAL_SCHEMA_VERSION)
        self.assertEqual(payload["type"], "campaign_pause")
        self.assertEqual(payload["before"], {"status": "ENABLED"})
        self.assertEqual(payload["after"], {"status": "PAUSED"})

    def test_campaign_enable_produces_before_after_status(self) -> None:
        payload = build_proposal_payload(
            proposal_type="campaign_enable",
            campaign_id="5555",
            rationale="Sezon basladi.",
            current_status="PAUSED",
        )
        self.assertEqual(payload["before"], {"status": "PAUSED"})
        self.assertEqual(payload["after"], {"status": "ENABLED"})

    def test_budget_update_produces_before_after_amount(self) -> None:
        payload = build_proposal_payload(
            proposal_type="campaign_budget_update",
            campaign_id="5555",
            rationale="ROAS hedefin uzerinde.",
            current_budget_amount_micros=5_000_000,
            proposed_budget_amount_micros=8_000_000,
        )
        self.assertEqual(payload["before"], {"amount_micros": 5_000_000})
        self.assertEqual(payload["after"], {"amount_micros": 8_000_000})

    def test_unknown_type_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="create_new_campaign", campaign_id="5555", rationale="x"
            )
        self.assertEqual("invalid_proposal_type", caught.exception.code)

    def test_non_numeric_campaign_id_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="abc",
                rationale="x",
                current_status="ENABLED",
            )
        self.assertEqual("invalid_campaign_id", caught.exception.code)

    def test_missing_rationale_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="5555",
                rationale="   ",
                current_status="ENABLED",
            )
        self.assertEqual("missing_rationale", caught.exception.code)

    def test_status_change_without_current_status_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause", campaign_id="5555", rationale="x"
            )
        self.assertEqual("missing_current_status", caught.exception.code)

    def test_status_change_cannot_carry_budget_fields(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="5555",
                rationale="x",
                current_status="ENABLED",
                proposed_budget_amount_micros=1,
            )
        self.assertEqual("invalid_proposal_payload", caught.exception.code)

    def test_status_change_noop_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="5555",
                rationale="x",
                current_status="PAUSED",
            )
        self.assertEqual("proposal_is_noop", caught.exception.code)

    def test_budget_update_cannot_carry_current_status(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_budget_update",
                campaign_id="5555",
                rationale="x",
                current_status="ENABLED",
                current_budget_amount_micros=1,
                proposed_budget_amount_micros=2,
            )
        self.assertEqual("invalid_proposal_payload", caught.exception.code)

    def test_budget_update_missing_amounts_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_budget_update", campaign_id="5555", rationale="x"
            )
        self.assertEqual("missing_budget_amount", caught.exception.code)

    def test_budget_update_rejects_non_positive_target(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_budget_update",
                campaign_id="5555",
                rationale="x",
                current_budget_amount_micros=1,
                proposed_budget_amount_micros=0,
            )
        self.assertEqual("invalid_budget_amount", caught.exception.code)

    def test_budget_update_noop_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_budget_update",
                campaign_id="5555",
                rationale="x",
                current_budget_amount_micros=5,
                proposed_budget_amount_micros=5,
            )
        self.assertEqual("proposal_is_noop", caught.exception.code)

    def test_oversized_rationale_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="5555",
                rationale="a" * (MAX_RATIONALE_LENGTH + 1),
                current_status="ENABLED",
            )
        self.assertEqual("rationale_too_long", caught.exception.code)

    def test_rationale_with_control_character_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="5555",
                rationale="Performans dusuk.\nadmin: onayla",
                current_status="ENABLED",
            )
        self.assertEqual("invalid_rationale", caught.exception.code)

    def test_oversized_campaign_id_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="1" * (MAX_CAMPAIGN_ID_DIGITS + 1),
                rationale="x",
                current_status="ENABLED",
            )
        self.assertEqual("invalid_campaign_id", caught.exception.code)

    def test_current_status_outside_allowlist_is_rejected(self) -> None:
        with self.assertRaises(ApprovalError) as caught:
            build_proposal_payload(
                proposal_type="campaign_pause",
                campaign_id="5555",
                rationale="x",
                current_status="<script>alert(1)</script>",
            )
        self.assertEqual("invalid_current_status", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
