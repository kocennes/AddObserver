"""Enforce append-only audit events inside PostgreSQL.

Revision ID: 20260722_0007
Revises: 20260719_0006
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op

revision = "20260722_0007"
down_revision = "20260719_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Reject mutation of an existing audit row for every database role."""
    op.execute(
        """
        CREATE FUNCTION reject_audit_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only' USING ERRCODE = '42501';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_event_append_only "
        "BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION reject_audit_event_mutation()"
    )


def downgrade() -> None:
    """Remove the append-only database guard for a controlled rollback."""
    op.execute("DROP TRIGGER audit_event_append_only ON audit_event")
    op.execute("DROP FUNCTION reject_audit_event_mutation()")
