"""SQLAlchemy metadata for the PostgreSQL production schema.

The sqlite3 schema in ``backend.src.db.schema`` remains the fast local/test
prototype. This module is the production metadata source for Alembic, per
ADR-0006. RLS policies are intentionally added in the follow-up 4.3 migration.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

UUID = postgresql.UUID(as_uuid=False)
JSONB = postgresql.JSONB(astext_type=Text())
TIMESTAMPTZ = DateTime(timezone=True)

RLS_TABLES = (
    "ads_account",
    "oauth_client_grant",
    "oauth_credential",
    "credential_revocation_job",
    "authorization_code",
    "access_token",
    "refresh_token",
    "web_session",
    "proposal",
    "approval",
    "execution",
    "audit_event",
)


def uuid_column(name: str, *, nullable: bool = False) -> Column[str]:
    """Return a PostgreSQL UUID text-mapped column."""
    return Column(name, UUID, nullable=nullable)


principal = Table(
    "principal",
    metadata,
    uuid_column("id"),
    Column("issuer", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    UniqueConstraint("issuer", "subject", name="uq_principal_issuer_subject"),
    CheckConstraint("status in ('active', 'disabled')", name="status"),
)

ads_account = Table(
    "ads_account",
    metadata,
    uuid_column("id"),
    uuid_column("principal_id"),
    Column("customer_id", Text, nullable=False),
    Column("login_customer_id", Text),
    Column("status", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    UniqueConstraint("principal_id", "customer_id", name="uq_ads_account_principal_customer"),
    CheckConstraint("customer_id ~ '^[0-9]{10}$'", name="customer_id"),
    CheckConstraint(
        "login_customer_id is null or login_customer_id ~ '^[0-9]{10}$'",
        name="login_customer_id",
    ),
    CheckConstraint("status in ('active', 'disconnected')", name="status"),
)

oauth_client_grant = Table(
    "oauth_client_grant",
    metadata,
    uuid_column("id"),
    uuid_column("principal_id"),
    Column("client_id", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    UniqueConstraint("principal_id", "client_id", name="uq_oauth_client_grant_principal_client"),
    CheckConstraint("status in ('active', 'revoked')", name="status"),
)

oauth_credential = Table(
    "oauth_credential",
    metadata,
    uuid_column("id"),
    uuid_column("principal_id"),
    Column("vault_ref", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("key_version", Integer, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    UniqueConstraint("id", "principal_id", name="uq_oauth_credential_id_principal"),
    CheckConstraint("status in ('pending', 'active', 'revoked', 'invalid')", name="status"),
    CheckConstraint("key_version >= 1", name="key_version"),
)

credential_revocation_job = Table(
    "credential_revocation_job",
    metadata,
    uuid_column("id"),
    uuid_column("principal_id"),
    uuid_column("credential_id"),
    Column("vault_ref", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", TIMESTAMPTZ, nullable=False),
    Column("last_error_code", Text),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Column("completed_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    ForeignKeyConstraint(
        ["credential_id", "principal_id"],
        ["oauth_credential.id", "oauth_credential.principal_id"],
    ),
    UniqueConstraint("credential_id", name="uq_credential_revocation_job_credential"),
    CheckConstraint("status in ('pending', 'completed')", name="status"),
    CheckConstraint("attempts >= 0", name="attempts"),
)

authorization_transaction = Table(
    "authorization_transaction",
    metadata,
    Column("id", Text, nullable=False),
    Column("client_id", Text, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_challenge", Text, nullable=False),
    Column("code_challenge_method", Text, nullable=False),
    Column("resource", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("client_state", Text, nullable=False),
    Column("consent_csrf_hash", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    CheckConstraint("code_challenge_method = 'S256'", name="pkce_s256"),
    CheckConstraint(
        "status in ('pending', 'consented', 'completed')",
        name="status",
    ),
)

authorization_code = Table(
    "authorization_code",
    metadata,
    Column("code_hash", Text, nullable=False),
    Column("transaction_id", Text, nullable=False),
    uuid_column("principal_id"),
    Column("client_id", Text, nullable=False),
    Column("redirect_uri", Text, nullable=False),
    Column("code_challenge", Text, nullable=False),
    Column("code_challenge_method", Text, nullable=False),
    Column("resource", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("consumed_at", TIMESTAMPTZ),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("code_hash"),
    ForeignKeyConstraint(["transaction_id"], ["authorization_transaction.id"]),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    CheckConstraint("code_challenge_method = 'S256'", name="pkce_s256"),
)

access_token = Table(
    "access_token",
    metadata,
    Column("token_hash", Text, nullable=False),
    uuid_column("principal_id"),
    Column("client_id", Text, nullable=False),
    Column("resource", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("revoked_at", TIMESTAMPTZ),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("token_hash"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
)

refresh_token = Table(
    "refresh_token",
    metadata,
    Column("token_hash", Text, nullable=False),
    Column("family_id", Text, nullable=False),
    uuid_column("principal_id"),
    Column("client_id", Text, nullable=False),
    Column("resource", Text, nullable=False),
    Column("scope", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    Column("rotated_at", TIMESTAMPTZ),
    PrimaryKeyConstraint("token_hash"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    CheckConstraint("status in ('active', 'rotated', 'revoked')", name="status"),
)

web_login_state = Table(
    "web_login_state",
    metadata,
    Column("state_hash", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("state_hash"),
    CheckConstraint("status in ('pending', 'consumed', 'expired')", name="status"),
)

web_session = Table(
    "web_session",
    metadata,
    Column("token_hash", Text, nullable=False),
    uuid_column("principal_id"),
    Column("csrf_token_hash", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("revoked_at", TIMESTAMPTZ),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("token_hash"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
)

proposal = Table(
    "proposal",
    metadata,
    uuid_column("id"),
    uuid_column("principal_id"),
    Column("customer_id", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("proposal_hash", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("expires_at", TIMESTAMPTZ, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    UniqueConstraint("id", "principal_id", name="uq_proposal_id_principal"),
    CheckConstraint("customer_id ~ '^[0-9]{10}$'", name="customer_id"),
    CheckConstraint(
        "status in ('pending_approval', 'approved', 'rejected', 'expired', "
        "'executing', 'applied', 'failed', 'stale')",
        name="status",
    ),
)

approval = Table(
    "approval",
    metadata,
    uuid_column("id"),
    uuid_column("proposal_id"),
    uuid_column("principal_id"),
    uuid_column("approver_id"),
    Column("decision", Text, nullable=False),
    Column("proposal_hash", Text, nullable=False),
    Column("decided_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    ForeignKeyConstraint(["approver_id"], ["principal.id"]),
    ForeignKeyConstraint(["proposal_id", "principal_id"], ["proposal.id", "proposal.principal_id"]),
    UniqueConstraint("proposal_id", "principal_id", name="uq_approval_proposal_principal"),
    CheckConstraint("decision in ('approve', 'reject')", name="decision"),
)

execution = Table(
    "execution",
    metadata,
    uuid_column("id"),
    uuid_column("proposal_id"),
    uuid_column("principal_id"),
    Column("idempotency_key", Text, nullable=False),
    Column("before", JSONB, nullable=False),
    Column("after", JSONB, nullable=False),
    Column("google_request_id", Text),
    Column("status", Text, nullable=False),
    Column("created_at", TIMESTAMPTZ, nullable=False),
    PrimaryKeyConstraint("id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    ForeignKeyConstraint(["proposal_id", "principal_id"], ["proposal.id", "proposal.principal_id"]),
    UniqueConstraint(
        "principal_id",
        "proposal_id",
        "idempotency_key",
        name="uq_execution_principal_proposal_idempotency",
    ),
    CheckConstraint("status in ('pending', 'applied', 'failed', 'unknown')", name="status"),
)

audit_event = Table(
    "audit_event",
    metadata,
    uuid_column("event_id"),
    Column("occurred_at", TIMESTAMPTZ, nullable=False),
    Column("actor", Text, nullable=False),
    uuid_column("principal_id", nullable=True),
    Column("customer_id", Text),
    Column("event_type", Text, nullable=False),
    uuid_column("proposal_id", nullable=True),
    uuid_column("approval_id", nullable=True),
    uuid_column("execution_id", nullable=True),
    Column("outcome", Text, nullable=False),
    Column("reason_code", Text),
    Column("correlation_id", Text, nullable=False),
    Column("google_request_id", Text),
    PrimaryKeyConstraint("event_id"),
    ForeignKeyConstraint(["principal_id"], ["principal.id"]),
    ForeignKeyConstraint(["proposal_id"], ["proposal.id"]),
    ForeignKeyConstraint(["approval_id"], ["approval.id"]),
    ForeignKeyConstraint(["execution_id"], ["execution.id"]),
    CheckConstraint("customer_id is null or customer_id ~ '^[0-9]{10}$'", name="customer_id"),
)

PRODUCTION_TABLES = tuple(metadata.tables)
