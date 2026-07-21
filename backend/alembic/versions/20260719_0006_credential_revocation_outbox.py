"""Add durable Google credential revocation outbox.

Revision ID: 20260719_0006
Revises: 20260719_0005
Create Date: 2026-07-19
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260719_0006"
down_revision = "20260719_0005"
branch_labels = None
depends_on = None

PRINCIPAL_POLICY = (
    "principal_id = nullif(current_setting('app.current_principal_id', true), '')::uuid"
)


def upgrade() -> None:
    """Create the principal-scoped durable revocation work queue."""
    op.create_unique_constraint(
        "uq_oauth_credential_id_principal", "oauth_credential", ["id", "principal_id"]
    )
    op.create_table(
        "credential_revocation_job",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("principal_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("credential_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("vault_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_credential_revocation_job"),
        sa.ForeignKeyConstraint(
            ["principal_id"], ["principal.id"], name="fk_revocation_job_principal"
        ),
        sa.ForeignKeyConstraint(
            ["credential_id", "principal_id"],
            ["oauth_credential.id", "oauth_credential.principal_id"],
            name="fk_revocation_job_credential_owner",
        ),
        sa.UniqueConstraint("credential_id", name="uq_credential_revocation_job_credential"),
        sa.CheckConstraint("status in ('pending', 'completed')", name="ck_revocation_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_revocation_attempts"),
    )
    op.execute("ALTER TABLE credential_revocation_job ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE credential_revocation_job FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY credential_revocation_job_principal_isolation "
        "ON credential_revocation_job "
        f"USING ({PRINCIPAL_POLICY}) WITH CHECK ({PRINCIPAL_POLICY})"
    )


def downgrade() -> None:
    """Remove the revocation outbox and supporting ownership constraint."""
    op.drop_table("credential_revocation_job")
    op.drop_constraint("uq_oauth_credential_id_principal", "oauth_credential", type_="unique")
