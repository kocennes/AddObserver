"""Allow exact-hash access/refresh token bootstrap under RLS.

Revision ID: 20260719_0004
Revises: 20260719_0003
Create Date: 2026-07-19
"""

from __future__ import annotations

from alembic import op

revision = "20260719_0004"
down_revision = "20260719_0003"
branch_labels = None
depends_on = None

BOOTSTRAP_SETTING = "app.current_token_hash"
TOKEN_TABLES = ("access_token", "refresh_token")


def upgrade() -> None:
    """Permit only an exact transaction-local token hash to be selected."""
    for table_name in TOKEN_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_exact_hash_bootstrap ON {table_name} FOR SELECT "
            "USING (token_hash = nullif("
            f"current_setting('{BOOTSTRAP_SETTING}', true), ''))"
        )


def downgrade() -> None:
    """Remove exact-hash token bootstrap policies."""
    for table_name in reversed(TOKEN_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_exact_hash_bootstrap ON {table_name}")
