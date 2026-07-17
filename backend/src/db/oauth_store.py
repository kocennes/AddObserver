"""Persistence for backend.src.auth.domain objects (connector OAuth 2.1 AS, ADR-0002).

Business rules are NOT repeated here -- this module only stores/loads the immutable
dataclasses ``backend.src.auth.domain`` produces and performs the storage-layer
operations that inherently need multi-row atomicity (single-use code claims, refresh
token rotation + reuse-triggered family revocation). The single source of truth for
validity rules stays ``backend/src/auth/domain.py``, mirroring how
``backend/src/db/proposals.py`` relates to ``backend/src/approval/domain.py``.

Token/code values are never stored raw -- only their SHA-256 hash
(``backend.src.auth.domain.hash_token``), so a database read alone cannot yield a
usable bearer credential.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from ..auth.domain import (
    AccessToken,
    AuthError,
    AuthorizationCode,
    AuthorizationTransaction,
    RefreshOutcome,
    RefreshToken,
    RefreshTokenStatus,
    TransactionStatus,
    hash_token,
    rotate_refresh_token,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClientGrantRepository:
    """Records that a principal consented to a client_id + scope (oauth_client_grant)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def record_consent(self, principal_id: str, client_id: str, scope: str) -> None:
        self._conn.execute(
            "INSERT INTO oauth_client_grant (id, principal_id, client_id, scope, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?) "
            "ON CONFLICT(principal_id, client_id) DO UPDATE SET scope = excluded.scope, status = 'active'",
            (str(uuid.uuid4()), principal_id, client_id, scope, _now()),
        )
        self._conn.commit()

    def has_active_grant(self, principal_id: str, client_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM oauth_client_grant WHERE principal_id = ? AND client_id = ? AND status = 'active'",
            (principal_id, client_id),
        ).fetchone()
        return row is not None


class AuthorizationTransactionRepository:
    """Stores in-flight /authorize transactions, pre- and post-consent."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, transaction: AuthorizationTransaction) -> None:
        self._conn.execute(
            "INSERT INTO authorization_transaction (id, client_id, redirect_uri, code_challenge, "
            "code_challenge_method, resource, scope, client_state, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status = excluded.status",
            (
                transaction.transaction_id, transaction.client_id, transaction.redirect_uri,
                transaction.code_challenge, transaction.code_challenge_method, transaction.resource,
                transaction.scope, transaction.client_state, transaction.status.value,
                transaction.expires_at.isoformat(), _now(),
            ),
        )
        self._conn.commit()

    def get(self, transaction_id: str) -> AuthorizationTransaction | None:
        row = self._conn.execute(
            "SELECT * FROM authorization_transaction WHERE id = ?", (transaction_id,)
        ).fetchone()
        return None if row is None else _transaction_from_row(row)


def _transaction_from_row(row: sqlite3.Row) -> AuthorizationTransaction:
    return AuthorizationTransaction(
        transaction_id=row["id"],
        client_id=row["client_id"],
        redirect_uri=row["redirect_uri"],
        code_challenge=row["code_challenge"],
        code_challenge_method=row["code_challenge_method"],
        resource=row["resource"],
        scope=row["scope"],
        client_state=row["client_state"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        status=TransactionStatus(row["status"]),
    )


class AuthorizationCodeRepository:
    """Stores single-use authorization codes by their SHA-256 hash."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save(self, code: AuthorizationCode) -> None:
        self._conn.execute(
            "INSERT INTO authorization_code (code_hash, transaction_id, principal_id, client_id, "
            "redirect_uri, code_challenge, code_challenge_method, resource, scope, expires_at, "
            "consumed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                hash_token(code.code), code.transaction_id, code.principal_id, code.client_id,
                code.redirect_uri, code.code_challenge, code.code_challenge_method, code.resource,
                code.scope, code.expires_at.isoformat(), _now(),
            ),
        )
        self._conn.commit()

    def claim(self, raw_code: str) -> tuple[AuthorizationCode, bool]:
        """Atomically mark a code consumed. Returns ``(record, already_consumed)``.

        The ``UPDATE ... WHERE consumed_at IS NULL`` is the single-use enforcement: a
        second, concurrent redemption of the same code always loses this race and gets
        ``already_consumed=True`` back, which ``auth.domain.consume_authorization_code``
        turns into a fail-closed ``invalid_grant``.
        """
        code_hash = hash_token(raw_code)
        cursor = self._conn.execute(
            "UPDATE authorization_code SET consumed_at = ? WHERE code_hash = ? AND consumed_at IS NULL",
            (_now(), code_hash),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM authorization_code WHERE code_hash = ?", (code_hash,)
        ).fetchone()
        if row is None:
            raise AuthError("invalid_grant", "Yetkilendirme kodu bulunamadi.")
        return _code_from_row(row), cursor.rowcount == 0


def _code_from_row(row: sqlite3.Row) -> AuthorizationCode:
    return AuthorizationCode(
        code="",  # never reconstructed -- consumption logic never reads the raw value back
        transaction_id=row["transaction_id"],
        principal_id=row["principal_id"],
        client_id=row["client_id"],
        redirect_uri=row["redirect_uri"],
        code_challenge=row["code_challenge"],
        code_challenge_method=row["code_challenge_method"],
        resource=row["resource"],
        scope=row["scope"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )


class TokenRepository:
    """Stores access/refresh tokens by hash and performs rotation + reuse detection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def save_access(self, token: AccessToken) -> None:
        self._conn.execute(
            "INSERT INTO access_token (token_hash, principal_id, client_id, resource, scope, "
            "expires_at, revoked_at, created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                hash_token(token.token), token.principal_id, token.client_id, token.resource,
                token.scope, token.expires_at.isoformat(), _now(),
            ),
        )
        self._conn.commit()

    def get_access(self, raw_token: str) -> AccessToken | None:
        """Return the token record regardless of expiry/revocation; callers must check both."""
        row = self._conn.execute(
            "SELECT * FROM access_token WHERE token_hash = ?", (hash_token(raw_token),)
        ).fetchone()
        if row is None:
            return None
        if row["revoked_at"] is not None:
            return None
        return AccessToken(
            token="",
            principal_id=row["principal_id"],
            client_id=row["client_id"],
            resource=row["resource"],
            scope=row["scope"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
        )

    def save_refresh(self, token: RefreshToken) -> None:
        self._conn.execute(
            "INSERT INTO refresh_token (token_hash, family_id, principal_id, client_id, resource, "
            "scope, status, expires_at, created_at, rotated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                hash_token(token.token), token.family_id, token.principal_id, token.client_id,
                token.resource, token.scope, token.status.value, token.expires_at.isoformat(), _now(),
            ),
        )
        self._conn.commit()

    def rotate(self, raw_refresh_token: str, *, now: datetime) -> RefreshOutcome:
        """Redeem a refresh token for a new pair, or fail closed + revoke the family on reuse.

        Reuse detection needs to touch every row sharing ``family_id``, which is why it
        lives here rather than in the pure ``auth.domain.rotate_refresh_token`` -- that
        function only judges a single already-loaded record.
        """
        token_hash = hash_token(raw_refresh_token)
        row = self._conn.execute(
            "SELECT * FROM refresh_token WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            raise AuthError("invalid_grant", "refresh_token bulunamadi.")
        stored = _refresh_from_row(row)
        if stored.status is not RefreshTokenStatus.ACTIVE:
            self._conn.execute(
                "UPDATE refresh_token SET status = ? WHERE family_id = ? AND status != ?",
                (RefreshTokenStatus.REVOKED.value, stored.family_id, RefreshTokenStatus.REVOKED.value),
            )
            self._conn.commit()
            raise AuthError("invalid_grant", "refresh_token yeniden kullanilmis; oturum ailesi iptal edildi.")

        outcome = rotate_refresh_token(stored, now=now)
        self._conn.execute(
            "UPDATE refresh_token SET status = ?, rotated_at = ? WHERE token_hash = ?",
            (RefreshTokenStatus.ROTATED.value, now.isoformat(), token_hash),
        )
        self.save_refresh(outcome.refresh_token)
        self.save_access(outcome.access_token)
        return outcome

    def revoke_family(self, family_id: str) -> None:
        self._conn.execute(
            "UPDATE refresh_token SET status = ? WHERE family_id = ? AND status != ?",
            (RefreshTokenStatus.REVOKED.value, family_id, RefreshTokenStatus.REVOKED.value),
        )
        self._conn.commit()

    def revoke_all_for_principal(self, principal_id: str, *, now: datetime) -> None:
        """Revoke every access/refresh token this principal holds, across all families
        and clients (docs/AUTH.md -- "Disconnect/revoke connector session ... iptal
        eder"). Unlike ``rotate``'s reuse-triggered revoke, this is a deliberate,
        principal-initiated action, not a theft signal.
        """
        self._conn.execute(
            "UPDATE access_token SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
            (now.isoformat(), principal_id),
        )
        self._conn.execute(
            "UPDATE refresh_token SET status = ? WHERE principal_id = ? AND status != ?",
            (RefreshTokenStatus.REVOKED.value, principal_id, RefreshTokenStatus.REVOKED.value),
        )
        self._conn.commit()


def _refresh_from_row(row: sqlite3.Row) -> RefreshToken:
    return RefreshToken(
        token="",
        family_id=row["family_id"],
        principal_id=row["principal_id"],
        client_id=row["client_id"],
        resource=row["resource"],
        scope=row["scope"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        status=RefreshTokenStatus(row["status"]),
    )
