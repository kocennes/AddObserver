"""Allow exact-hash authorization-code bootstrap under RLS.

Revision ID: 20260719_0003
Revises: 20260718_0002
Create Date: 2026-07-19
"""

from __future__ import annotations

from alembic import op

revision = "20260719_0003"
down_revision = "20260718_0002"
branch_labels = None
depends_on = None

POLICY_NAME = "authorization_code_exact_hash_bootstrap"
BOOTSTRAP_SETTING = "app.current_authorization_code_hash"


def upgrade() -> None:
    """Permit only the exact transaction-local code hash to be selected."""
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON authorization_code FOR SELECT "
        "USING (code_hash = nullif("
        f"current_setting('{BOOTSTRAP_SETTING}', true), ''))"
    )


def downgrade() -> None:
    """Remove the exact-hash OAuth bootstrap policy."""
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON authorization_code")
