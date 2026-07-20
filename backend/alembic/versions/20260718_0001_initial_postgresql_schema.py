"""Initial PostgreSQL production schema.

Revision ID: 20260718_0001
Revises:
Create Date: 2026-07-18
"""
# ruff: noqa: E501

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260718_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the initial principal-scoped PostgreSQL schema."""
    op.create_table(
        "principal",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('active', 'disabled')", name="status"),
        sa.PrimaryKeyConstraint("id", name="pk_principal"),
        sa.UniqueConstraint("issuer", "subject", name="uq_principal_issuer_subject"),
    )
    op.create_table(
        "ads_account",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("login_customer_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("customer_id ~ '^[0-9]{10}$'", name="customer_id"),
        sa.CheckConstraint(
            "login_customer_id is null or login_customer_id ~ '^[0-9]{10}$'",
            name="login_customer_id",
        ),
        sa.CheckConstraint("status in ('active', 'disconnected')", name="status"),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_ads_account_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ads_account"),
        sa.UniqueConstraint(
            "principal_id", "customer_id", name="uq_ads_account_principal_customer"
        ),
    )
    op.create_table(
        "oauth_client_grant",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('active', 'revoked')", name="status"),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_oauth_client_grant_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oauth_client_grant"),
        sa.UniqueConstraint(
            "principal_id", "client_id", name="uq_oauth_client_grant_principal_client"
        ),
    )
    op.create_table(
        "oauth_credential",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("vault_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("key_version >= 1", name="key_version"),
        sa.CheckConstraint(
            "status in ('pending', 'active', 'revoked', 'invalid')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_oauth_credential_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oauth_credential"),
    )
    op.create_table(
        "authorization_transaction",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("code_challenge_method", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("client_state", sa.Text(), nullable=False),
        sa.Column("consent_csrf_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("code_challenge_method = 'S256'", name="pkce_s256"),
        sa.CheckConstraint(
            "status in ('pending', 'consented', 'completed')",
            name="status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_authorization_transaction"),
    )
    op.create_table(
        "authorization_code",
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("transaction_id", sa.Text(), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("code_challenge_method", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("code_challenge_method = 'S256'", name="pkce_s256"),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_authorization_code_principal_id_principal"
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["authorization_transaction.id"],
            name="fk_authorization_code_transaction_id_authorization_transaction",
        ),
        sa.PrimaryKeyConstraint("code_hash", name="pk_authorization_code"),
    )
    op.create_table(
        "access_token",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_access_token_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_access_token"),
    )
    op.create_table(
        "refresh_token",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("family_id", sa.Text(), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status in ('active', 'rotated', 'revoked')", name="status"),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_refresh_token_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_refresh_token"),
    )
    op.create_table(
        "web_login_state",
        sa.Column("state_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('pending', 'consumed', 'expired')", name="status"),
        sa.PrimaryKeyConstraint("state_hash", name="pk_web_login_state"),
    )
    op.create_table(
        "web_session",
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_web_session_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_web_session"),
    )
    op.create_table(
        "proposal",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("proposal_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("customer_id ~ '^[0-9]{10}$'", name="customer_id"),
        sa.CheckConstraint(
            "status in ('pending_approval', 'approved', 'rejected', 'expired', 'executing', 'applied', 'failed', 'stale')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_proposal_principal_id_principal"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposal"),
        sa.UniqueConstraint("id", "principal_id", name="uq_proposal_id_principal"),
    )
    op.create_table(
        "approval",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("approver_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("proposal_hash", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision in ('approve', 'reject')", name="decision"),
        sa.ForeignKeyConstraint(
            ["approver_id"], ["principal.id"], name="fk_approval_approver_id_principal"
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_approval_principal_id_principal"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "principal_id"],
            ["proposal.id", "proposal.principal_id"],
            name="fk_approval_proposal_id_principal_id_proposal",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approval"),
        sa.UniqueConstraint("proposal_id", "principal_id", name="uq_approval_proposal_principal"),
    )
    op.create_table(
        "execution",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("google_request_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status in ('pending', 'applied', 'failed', 'unknown')", name="status"),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_execution_principal_id_principal"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id", "principal_id"],
            ["proposal.id", "proposal.principal_id"],
            name="fk_execution_proposal_id_principal_id_proposal",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_execution"),
        sa.UniqueConstraint(
            "principal_id",
            "proposal_id",
            "idempotency_key",
            name="uq_execution_principal_proposal_idempotency",
        ),
    )
    op.create_table(
        "audit_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("customer_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("proposal_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=False),
        sa.Column("google_request_id", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "customer_id is null or customer_id ~ '^[0-9]{10}$'", name="customer_id"
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"], ["approval.id"], name="fk_audit_event_approval_id_approval"
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["execution.id"], name="fk_audit_event_execution_id_execution"
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_audit_event_principal_id_principal"
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposal.id"], name="fk_audit_event_proposal_id_proposal"
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_event"),
    )


def downgrade() -> None:
    """Drop the initial schema in dependency order."""
    tables: Sequence[str] = (
        "audit_event",
        "execution",
        "approval",
        "proposal",
        "web_session",
        "web_login_state",
        "refresh_token",
        "access_token",
        "authorization_code",
        "authorization_transaction",
        "oauth_credential",
        "oauth_client_grant",
        "ads_account",
        "principal",
    )
    for table_name in tables:
        op.drop_table(table_name)
