"""Durable credential-revocation worker core (ADR-0007).

Scheduling is deliberately outside this module. One invocation claims at most
one principal-scoped job, closes that transaction, calls the vault, then records
completion or a sanitized retry in a second short transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db.postgres_uow import PostgresUnitOfWorkFactory
from .vault import VaultClient

_LEASE_DURATION = timedelta(minutes=2)
_MAX_RETRY_DELAY = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class RevocationWorkResult:
    """Secret-free outcome suitable for metrics and scheduler decisions."""

    processed: bool
    completed: bool
    attempts: int


def process_one_revocation(
    principal_id: str,
    *,
    uow_factory: PostgresUnitOfWorkFactory,
    vault: VaultClient,
    now: datetime,
) -> RevocationWorkResult:
    """Process one due vault-revocation job without spanning an external call."""
    with uow_factory.request() as claim_work:
        claim_work.bind_principal(principal_id)
        if claim_work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
        job = claim_work.repositories.credential_revocations.claim_due(
            principal_id,
            now=now,
            lease_until=now + _LEASE_DURATION,
        )
    if job is None:
        return RevocationWorkResult(processed=False, completed=False, attempts=0)

    try:
        vault.revoke(job.vault_ref)
    except Exception:  # noqa: BLE001 -- provider details must never enter DB/log/result
        retry_at = now + _retry_delay(job.attempts)
        with uow_factory.request() as retry_work:
            retry_work.bind_principal(principal_id)
            if retry_work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi") from None
            retry_work.repositories.credential_revocations.retry(
                principal_id,
                job.id,
                claimed_attempt=job.attempts,
                error_code="VAULT_UNAVAILABLE",
                next_attempt_at=retry_at,
            )
        return RevocationWorkResult(processed=True, completed=False, attempts=job.attempts)

    with uow_factory.request() as completion_work:
        completion_work.bind_principal(principal_id)
        if completion_work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
        completed = completion_work.repositories.credential_revocations.complete(
            principal_id, job.id, claimed_attempt=job.attempts, completed_at=now
        )
    return RevocationWorkResult(
        processed=True,
        completed=completed,
        attempts=job.attempts,
    )


def _retry_delay(attempts: int) -> timedelta:
    """Return capped exponential retry delay for a one-based attempt count."""
    exponent = max(0, min(attempts - 1, 8))
    delay = timedelta(minutes=2**exponent)
    return min(delay, _MAX_RETRY_DELAY)
