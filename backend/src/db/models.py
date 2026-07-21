"""Dataclasses/enums for entities that have no existing domain module.

``Proposal``, ``Approval`` ve ``ExecutionReservation`` kasıtlı olarak burada YOKTUR — onlar
``backend.src.approval.domain`` içinde tanımlı ve iş kuralları oradan gelir (bkz. paket
docstring'i). Bu modül yalnız kimlik/hesap/credential/audit gibi ayrı bir domain modülü
olmayan varlıkları taşır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PrincipalStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class CredentialStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
    INVALID = "invalid"


class CredentialRevocationStatus(StrEnum):
    """Durable vault-revocation work state (ADR-0007)."""

    PENDING = "pending"
    COMPLETED = "completed"


class ExecutionStatus(StrEnum):
    """DATABASE.md kararı: execution outbox ``pending -> applied|failed|unknown`` ilerler."""

    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Principal:
    """Connector kullanıcı izolasyon kökü (DATA_MODEL.md — `(issuer, subject)` benzersiz)."""

    id: str
    issuer: str
    subject: str
    status: PrincipalStatus
    created_at: datetime


@dataclass(frozen=True)
class AdsAccount:
    """Bir principal'ın erişebildiği Google Ads customer_id eşlemesi."""

    id: str
    principal_id: str
    customer_id: str
    login_customer_id: str | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class OAuthCredential:
    """Google OAuth credential'ının secrets manager referansı — secret değeri burada YOK."""

    id: str
    principal_id: str
    vault_ref: str
    status: CredentialStatus
    key_version: int
    created_at: datetime


@dataclass(frozen=True)
class CredentialRevocationJob:
    """Secret-free snapshot used to revoke one credential from the vault."""

    id: str
    principal_id: str
    credential_id: str
    vault_ref: str
    status: CredentialRevocationStatus
    attempts: int
    next_attempt_at: datetime
    last_error_code: str | None
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class AuditEvent:
    """Append-only denetim kaydı (DATA_MODEL.md)."""

    event_id: str
    occurred_at: datetime
    actor: str
    principal_id: str | None
    customer_id: str | None
    event_type: str
    proposal_id: str | None
    approval_id: str | None
    execution_id: str | None
    outcome: str
    reason_code: str | None
    correlation_id: str
    google_request_id: str | None
