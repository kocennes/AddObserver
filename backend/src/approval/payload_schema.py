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

#: A short justification, not a document -- bounded so a compromised/careless MCP
#: client cannot use the stored proposal payload as unbounded storage, and so a
#: later approval-page render (Faz 7.1) has a known worst-case size to escape.
MAX_RATIONALE_LENGTH = 2000
#: Google Ads resource IDs are ``int64``; 19 digits covers the full range.
MAX_CAMPAIGN_ID_DIGITS = 19
MAX_EVIDENCE_REFS = 20
MAX_EVIDENCE_REF_LENGTH = 128

_CAMPAIGN_ID_RE = re.compile(rf"^\d{{1,{MAX_CAMPAIGN_ID_DIGITS}}}$")
#: C0 controls and DEL -- ``rationale`` is stored verbatim and may reach logs/audit
#: rows and a future HTML render, so control characters (incl. newlines used for
#: log-injection-style smuggling) are rejected the same way approval-form fields are.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
#: The only ``current_status`` values Google Ads actually reports for a campaign
#: (docs/API_CONTRACTS.md); free text here would let the "observed current state"
#: field become untrusted narrative text.
_CAMPAIGN_STATUS_VALUES = frozenset({"ENABLED", "PAUSED", "REMOVED"})
_RISK_VALUES = frozenset({"low", "medium", "high"})


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
    evidence_refs: list[str] | None = None,
    risk: str = "medium",
) -> dict[str, Any]:
    """Validate Faz 1.1 allowlist inputs and return the canonical payload dict.

    Raises ``ApprovalError`` for anything outside the allowlist, using the same
    fail-closed style as the rest of ``approval.domain`` -- a caller can branch
    on ``.code`` the same way it already does for e.g. ``approval_required``.
    """
    if not campaign_id or not _CAMPAIGN_ID_RE.match(campaign_id):
        raise ApprovalError(
            "invalid_campaign_id", "campaign_id sayisal bir Google Ads kimligi olmalidir."
        )
    if not rationale or not rationale.strip():
        raise ApprovalError(
            "missing_rationale",
            "Oneri, hangi kaynak metriklere dayandigini aciklayan bir rationale icermelidir.",
        )
    if len(rationale) > MAX_RATIONALE_LENGTH:
        raise ApprovalError(
            "rationale_too_long", f"rationale en fazla {MAX_RATIONALE_LENGTH} karakter olabilir."
        )
    if _CONTROL_CHAR_RE.search(rationale):
        raise ApprovalError("invalid_rationale", "rationale kontrol karakteri iceremez.")

    try:
        proposal_type_enum = ProposalType(proposal_type)
    except ValueError as error:
        allowed = ", ".join(member.value for member in ProposalType)
        raise ApprovalError(
            "invalid_proposal_type", f"proposal_type su degerlerden biri olmalidir: {allowed}."
        ) from error

    refs = evidence_refs or []
    if len(refs) > MAX_EVIDENCE_REFS or any(
        not ref or len(ref) > MAX_EVIDENCE_REF_LENGTH or _CONTROL_CHAR_RE.search(ref)
        for ref in refs
    ):
        raise ApprovalError(
            "invalid_evidence_refs", "evidence_refs sinirli ve bos olmayan kimlikler olmalidir."
        )
    if len(set(refs)) != len(refs):
        raise ApprovalError("invalid_evidence_refs", "evidence_refs tekrar eden kimlik iceremez.")
    if risk not in _RISK_VALUES:
        raise ApprovalError("invalid_risk", "risk low, medium veya high olmalidir.")

    if proposal_type_enum in _TARGET_STATUS:
        before, after = _status_change(
            proposal_type_enum,
            current_status,
            current_budget_amount_micros,
            proposed_budget_amount_micros,
        )
    else:
        before, after = _budget_change(
            current_status, current_budget_amount_micros, proposed_budget_amount_micros
        )

    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "type": proposal_type_enum.value,
        "campaign_id": campaign_id,
        "rationale": rationale,
        "evidence_refs": refs,
        "risk": risk,
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
        raise ApprovalError(
            "invalid_proposal_payload", "Durum degisikligi onerisi butce alani iceremez."
        )
    if not current_status or not current_status.strip():
        raise ApprovalError(
            "missing_current_status",
            "current_status, gozlemlenen mevcut kampanya durumunu icermelidir.",
        )
    if current_status not in _CAMPAIGN_STATUS_VALUES:
        allowed = ", ".join(sorted(_CAMPAIGN_STATUS_VALUES))
        raise ApprovalError(
            "invalid_current_status", f"current_status su degerlerden biri olmalidir: {allowed}."
        )
    target_status = _TARGET_STATUS[proposal_type]
    if current_status == target_status:
        raise ApprovalError(
            "proposal_is_noop", "current_status zaten hedeflenen durumda; oneriye gerek yok."
        )
    return {"status": current_status}, {"status": target_status}


def _budget_change(
    current_status: str | None,
    current_budget_amount_micros: int | None,
    proposed_budget_amount_micros: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if current_status is not None:
        raise ApprovalError(
            "invalid_proposal_payload", "Butce onerisi current_status alani iceremez."
        )
    if current_budget_amount_micros is None or proposed_budget_amount_micros is None:
        raise ApprovalError(
            "missing_budget_amount",
            "Butce onerisi current_budget_amount_micros ve "
            "proposed_budget_amount_micros gerektirir.",
        )
    if current_budget_amount_micros < 0 or proposed_budget_amount_micros <= 0:
        raise ApprovalError(
            "invalid_budget_amount",
            "Butce tutarlari negatif olamaz; hedef tutar sifirdan buyuk olmalidir.",
        )
    if current_budget_amount_micros == proposed_budget_amount_micros:
        raise ApprovalError(
            "proposal_is_noop", "Hedef butce mevcut butceyle ayni; oneriye gerek yok."
        )
    return {"amount_micros": current_budget_amount_micros}, {
        "amount_micros": proposed_budget_amount_micros
    }
