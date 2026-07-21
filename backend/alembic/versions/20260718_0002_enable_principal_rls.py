"""Enable principal-scoped row level security.

Revision ID: 20260718_0002
Revises: 20260718_0001
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision = "20260718_0002"
down_revision = "20260718_0001"
branch_labels = None
depends_on = None

RLS_TABLES: Sequence[str] = (
    "ads_account",
    "oauth_client_grant",
    "oauth_credential",
    "authorization_code",
    "access_token",
    "refresh_token",
    "web_session",
    "proposal",
    "approval",
    "execution",
    "audit_event",
)

PRINCIPAL_POLICY = (
    "principal_id = nullif(current_setting('app.current_principal_id', true), '')::uuid"
)


def upgrade() -> None:
    """Enable default-deny RLS plus a transaction-local principal policy."""
    for table_name in RLS_TABLES:
        policy_name = f"{table_name}_principal_isolation"
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {policy_name} ON {table_name} "
            f"USING ({PRINCIPAL_POLICY}) "
            f"WITH CHECK ({PRINCIPAL_POLICY})"
        )


def downgrade() -> None:
    """Remove principal-scoped RLS policies."""
    for table_name in reversed(RLS_TABLES):
        policy_name = f"{table_name}_principal_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
