"""Allow exact-hash browser-session bootstrap under RLS.

Revision ID: 20260719_0005
Revises: 20260719_0004
Create Date: 2026-07-19
"""

from __future__ import annotations

from alembic import op

revision = "20260719_0005"
down_revision = "20260719_0004"
branch_labels = None
depends_on = None

BOOTSTRAP_SETTING = "app.current_web_session_hash"
POLICY_NAME = "web_session_exact_hash_bootstrap"


def upgrade() -> None:
    """Permit SELECT of only the browser session matching the transaction hash."""
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON web_session FOR SELECT "
        "USING (token_hash = nullif("
        f"current_setting('{BOOTSTRAP_SETTING}', true), ''))"
    )


def downgrade() -> None:
    """Remove the exact-hash browser-session bootstrap policy."""
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON web_session")
