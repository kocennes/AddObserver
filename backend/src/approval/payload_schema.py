"""Faz 1.1 allowlist proposal payload schema (docs/PRODUCT.md, docs/DATA_MODEL.md).

``Proposal.payload`` is free-form JSON at the domain layer (``approval/domain.py``
has no Google Ads dependency on purpose), but docs/DATA_MODEL.md requires it be
validated against a versioned schema before it becomes a proposal ("Proposal
input/output JSON'u surumlu semaya gore dogrulanir; serbest JSON kalici sozlesme
degildir"). This module is that schema: only the two docs/PRODUCT.md Faz 1.1
allowlist operations (campaign pause/enable, campaign budget update) may become a
proposal. That gate applies now, independently of execution itself still being
blocked on ``docs/GOOGLE_API_ACCESS.md`` -- a proposal always describes an
operation Faz 1.1 will eventually be allowed to apply, never an arbitrary mutate.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from .domain import ApprovalError

#: Bumped whenever the shape of a stored payload changes; DATA_MODEL.md forbids
#: silently reinterpreting an old proposal's payload under a new schema.
PROPOSAL_SCHEMA_VERSION = 1

_CAMPAIGN_ID_RE = re.compile(r"^\d+$")


class ProposalType(StrEnum):
    """The only operations a proposal may describe (docs/PRODUCT.md Faz 1.1)."""

    CAMPAIGN_PAUSE = "campaign_pause"
    CAMPAIGN_ENABLE = "campaign_enable"
    CAMPAIGN_BUDGET_UPDATE = "campaign_budget_update"


_TARGET_STATUS: dict[ProposalType, str] = {
    ProposalType.CAMPAIGN_PAUSE: "PAUSED",
    ProposalType.CAMPAIGN_ENABLE: "ENABLED",
}


def build_proposal_payload(
    *,
    proposal_type: str,
    campaign_id: str,
    rationale: str,
    current_status: str | None = None,
    current_budget_amount_micros: int | None = None,
    proposed_budget_amount_micros: int | None = None,
) -> dict[str, Any]:
    """Validate Faz 1.1 allowlist inputs and return the canonical payload dict.

    Raises ``ApprovalError`` for anything outside the allowlist, using the same
    fail-closed style as the rest of ``approval.domain`` -- a caller can branch
    on ``.code`` the same way it already does for e.g. ``approval_required``.
    """
    if not campaign_id or not _CAMPAIGN_ID_RE.match(campaign_id):
        raise ApprovalError("invalid_campaign_id", "campaign_id sayisal bir Google Ads kimligi olmalidir.")
    if not rationale or not rationale.strip():
        raise ApprovalError(
            "missing_rationale", "Oneri, hangi kaynak metriklere dayandigini aciklayan bir rationale icermelidir."
        )

    try:
        proposal_type_enum = ProposalType(proposal_type)
    except ValueError as error:
        allowed = ", ".join(member.value for member in ProposalType)
        raise ApprovalError("invalid_proposal_type", f"proposal_type su degerlerden biri olmalidir: {allowed}.") from error

    if proposal_type_enum in _TARGET_STATUS:
        before, after = _status_change(proposal_type_enum, current_status, current_budget_amount_micros, proposed_budget_amount_micros)
    else:
        before, after = _budget_change(current_status, current_budget_amount_micros, proposed_budget_amount_micros)

    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "type": proposal_type_enum.value,
        "campaign_id": campaign_id,
        "rationale": rationale,
        "before": before,
        "after": after,
    }


def _status_change(
    proposal_type: ProposalType,
    current_status: str | None,
    current_budget_amount_micros: int | None,
    proposed_budget_amount_micros: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if current_budget_amount_micros is not None or proposed_budget_amount_micros is not None:
        raise ApprovalError("invalid_proposal_payload", "Durum degisikligi onerisi butce alani iceremez.")
    if not current_status or not current_status.strip():
        raise ApprovalError(
            "missing_current_status", "current_status, gozlemlenen mevcut kampanya durumunu icermelidir."
        )
    target_status = _TARGET_STATUS[proposal_type]
    if current_status == target_status:
        raise ApprovalError("proposal_is_noop", "current_status zaten hedeflenen durumda; oneriye gerek yok.")
    return {"status": current_status}, {"status": target_status}


def _budget_change(
    current_status: str | None,
    current_budget_amount_micros: int | None,
    proposed_budget_amount_micros: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if current_status is not None:
        raise ApprovalError("invalid_proposal_payload", "Butce onerisi current_status alani iceremez.")
    if current_budget_amount_micros is None or proposed_budget_amount_micros is None:
        raise ApprovalError(
            "missing_budget_amount",
            "Butce onerisi current_budget_amount_micros ve proposed_budget_amount_micros gerektirir.",
        )
    if current_budget_amount_micros < 0 or proposed_budget_amount_micros <= 0:
        raise ApprovalError(
            "invalid_budget_amount", "Butce tutarlari negatif olamaz; hedef tutar sifirdan buyuk olmalidir."
        )
    if current_budget_amount_micros == proposed_budget_amount_micros:
        raise ApprovalError("proposal_is_noop", "Hedef butce mevcut butceyle ayni; oneriye gerek yok.")
    return {"amount_micros": current_budget_amount_micros}, {"amount_micros": proposed_budget_amount_micros}
