"""PostgreSQL transaction-local principal context helpers.

The RLS policies added by ADR-0006/Faz 4.3 read this setting with
``current_setting(..., true)``. The setting is transaction-local so pooled
connections do not carry a previous request's principal after rollback/commit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Connection

from ..auth.domain import hash_token
from .sqlalchemy_schema import access_token, authorization_code, refresh_token, web_session

PRINCIPAL_CONTEXT_SETTING = "app.current_principal_id"
AUTHORIZATION_CODE_HASH_SETTING = "app.current_authorization_code_hash"
TOKEN_HASH_SETTING = "app.current_token_hash"  # nosec B105 - PostgreSQL setting name, not a secret
WEB_SESSION_HASH_SETTING = "app.current_web_session_hash"  # nosec B105 - PostgreSQL setting name, not a secret


def normalize_principal_uuid(principal_id: str) -> str:
    """Validate and canonicalize a principal UUID string for PostgreSQL RLS context."""
    return str(UUID(principal_id))


def set_transaction_principal(connection: Connection, principal_id: str) -> None:
    """Set the current principal for only the active PostgreSQL transaction."""
    normalized_principal_id = normalize_principal_uuid(principal_id)
    connection.execute(
        text("SELECT set_config(:setting_name, :principal_id, true)"),
        {
            "setting_name": PRINCIPAL_CONTEXT_SETTING,
            "principal_id": normalized_principal_id,
        },
    )


def clear_transaction_principal(connection: Connection) -> None:
    """Clear the transaction-local principal context before returning a connection."""
    connection.execute(
        text("SELECT set_config(:setting_name, '', true)"),
        {"setting_name": PRINCIPAL_CONTEXT_SETTING},
    )


def bootstrap_authorization_code_principal(connection: Connection, raw_code: str) -> str | None:
    """Resolve one exact code hash under RLS, then install its principal context.

    The bootstrap setting contains only a SHA-256 digest and is cleared before
    returning. The SELECT-only RLS policy cannot update or enumerate codes.
    """
    code_hash = hash_token(raw_code)
    connection.execute(
        text("SELECT set_config(:setting_name, :code_hash, true)"),
        {"setting_name": AUTHORIZATION_CODE_HASH_SETTING, "code_hash": code_hash},
    )
    try:
        principal_id = connection.execute(
            select(authorization_code.c.principal_id).where(
                authorization_code.c.code_hash == code_hash
            )
        ).scalar_one_or_none()
    finally:
        connection.execute(
            text("SELECT set_config(:setting_name, '', true)"),
            {"setting_name": AUTHORIZATION_CODE_HASH_SETTING},
        )
    if principal_id is None:
        return None
    normalized = normalize_principal_uuid(str(principal_id))
    set_transaction_principal(connection, normalized)
    return normalized


def bootstrap_access_token_principal(connection: Connection, raw_token: str) -> str | None:
    """Resolve one exact access-token hash and install its principal RLS context."""
    return _bootstrap_token_principal(connection, raw_token, access_token)


def bootstrap_refresh_token_principal(connection: Connection, raw_token: str) -> str | None:
    """Resolve one exact refresh-token hash and install its principal RLS context."""
    return _bootstrap_token_principal(connection, raw_token, refresh_token)


def bootstrap_web_session_principal(connection: Connection, raw_token: str) -> str | None:
    """Resolve one exact browser-session hash and install its principal context."""
    return _bootstrap_token_principal(
        connection,
        raw_token,
        web_session,
        setting_name=WEB_SESSION_HASH_SETTING,
    )


def _bootstrap_token_principal(
    connection: Connection,
    raw_token: str,
    table,  # noqa: ANN001
    *,
    setting_name: str = TOKEN_HASH_SETTING,
) -> str | None:
    token_hash = hash_token(raw_token)
    connection.execute(
        text("SELECT set_config(:setting_name, :token_hash, true)"),
        {"setting_name": setting_name, "token_hash": token_hash},
    )
    try:
        principal_id = connection.execute(
            select(table.c.principal_id).where(table.c.token_hash == token_hash)
        ).scalar_one_or_none()
    finally:
        connection.execute(
            text("SELECT set_config(:setting_name, '', true)"),
            {"setting_name": setting_name},
        )
    if principal_id is None:
        return None
    normalized = normalize_principal_uuid(str(principal_id))
    set_transaction_principal(connection, normalized)
    return normalized
