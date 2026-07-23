"""Deterministic, secret-free incident tabletop drills."""

from __future__ import annotations

from dataclasses import dataclass

REQUIRED_STAGES = (
    "detect",
    "contain",
    "preserve_evidence",
    "communicate",
    "recover",
    "postmortem",
)

DRILLS: dict[str, tuple[str, ...]] = {
    "credential_leak": REQUIRED_STAGES,
    "unauthorized_mutate": REQUIRED_STAGES,
    "audit_outage": REQUIRED_STAGES,
    "google_quota": REQUIRED_STAGES,
    "database_restore": REQUIRED_STAGES,
}


@dataclass(frozen=True, slots=True)
class DrillResult:
    """Bounded evidence from one simulated incident response."""

    scenario: str
    completed_stages: tuple[str, ...]
    elapsed_minutes: int
    secret_used: bool = False


def run_drill(scenario: str, *, minutes_per_stage: int = 5) -> DrillResult:
    """Execute a no-I/O tabletop drill and return auditable completion evidence."""
    if scenario not in DRILLS:
        raise ValueError("unknown incident drill")
    if minutes_per_stage < 1 or minutes_per_stage > 60:
        raise ValueError("minutes_per_stage must be between 1 and 60")
    stages = DRILLS[scenario]
    return DrillResult(
        scenario=scenario,
        completed_stages=stages,
        elapsed_minutes=len(stages) * minutes_per_stage,
    )
