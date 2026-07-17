"""Human-approval domain primitives."""

from .domain import (
    Approval,
    ApprovalError,
    Decision,
    ExecutionReservation,
    Proposal,
    ProposalStatus,
    approve_proposal,
    calculate_proposal_hash,
    reserve_execution,
    submit_proposal,
)
from .application import ExecutionClaim, MutationAdapter, MutationOutcome, execute_reserved_mutation
from .payload_schema import PROPOSAL_SCHEMA_VERSION, ProposalType, build_proposal_payload

__all__ = [
    "Approval",
    "ApprovalError",
    "Decision",
    "ExecutionReservation",
    "Proposal",
    "ProposalStatus",
    "approve_proposal",
    "calculate_proposal_hash",
    "reserve_execution",
    "submit_proposal",
    "MutationAdapter",
    "MutationOutcome",
    "ExecutionClaim",
    "execute_reserved_mutation",
    "PROPOSAL_SCHEMA_VERSION",
    "ProposalType",
    "build_proposal_payload",
]
