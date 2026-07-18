"""Persistence for backend.src.auth.web_session objects (the browser approval login/session).

Business rules are NOT repeated here -- this module only stores/loads by hash and
performs the storage-layer atomicity single-use claims inherently need, mirroring
how ``backend/src/db/oauth_store.py`` relates to ``backend/src/auth/domain.py``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..auth.web_session import hash_token


def _now() -> str:
    return datetime.now(UTC).isoformat()


class WebLoginStateRepository:
    """Stores single-use ``/login`` -> ``/google/callback`` state values by hash."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(self, raw_state: str, expires_at: datetime) -> None:
        self._conn.execute(
            "INSERT INTO web_login_state (state_hash, status, expires_at, created_at) "
            "VALUES (?, 'pending', ?, ?)",
            (hash_token(raw_state), expires_at.isoformat(), _now()),
        )
        self._conn.commit()

    def claim(self, raw_state: str) -> tuple[bool, datetime] | None:
        """Atomically mark a state consumed. Returns ``(already_consumed, expires_at)``.

        The ``UPDATE ... WHERE status = 'pending'`` is the single-use enforcement,
        mirroring ``AuthorizationCodeRepository.claim``: a second, concurrent claim
        of the same state always loses this race and gets ``already_consumed=True``
        back. Returns ``None`` if the state was never issued (unknown to us).
        """
        state_hash = hash_token(raw_state)
        cursor = self._conn.execute(
            "UPDATE web_login_state SET status = 'consumed' "
            "WHERE state_hash = ? AND status = 'pending'",
            (state_hash,),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT expires_at FROM web_login_state WHERE state_hash = ?", (state_hash,)
        ).fetchone()
        if row is None:
            return None
        return cursor.rowcount == 0, datetime.fromisoformat(row["expires_at"])


@dataclass(frozen=True, slots=True)
class WebSessionIssued:
    """The raw token/csrf_token pair to hand back to the browser -- never re-derivable.

    Both carry ``repr=False`` (backend/tests/test_secret_redaction.py) -- same
    rationale as ``backend.src.auth.web_session.WebSession``.
    """

    token: str = field(repr=False)
    csrf_token: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WebSessionLookup:
    """A looked-up session row, or the fail-closed shape for unknown/revoked tokens."""

    principal_id: str | None
    csrf_token_hash: str | None
    expires_at: datetime | None
    revoked: bool


class WebSessionRepository:
    """Stores approval-UI browser sessions by hash."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def create(
        self, principal_id: str, raw_token: str, csrf_token: str, expires_at: datetime
    ) -> WebSessionIssued:
        self._conn.execute(
            "INSERT INTO web_session "
            "(token_hash, principal_id, csrf_token_hash, expires_at, revoked_at, created_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            (
                hash_token(raw_token),
                principal_id,
                hash_token(csrf_token),
                expires_at.isoformat(),
                _now(),
            ),
        )
        self._conn.commit()
        return WebSessionIssued(token=raw_token, csrf_token=csrf_token)

    def lookup(self, raw_token: str) -> WebSessionLookup:
        """Return the session row's fail-closed shape; never raises for an unknown token."""
        row = self._conn.execute(
            "SELECT * FROM web_session WHERE token_hash = ?", (hash_token(raw_token),)
        ).fetchone()
        if row is None:
            return WebSessionLookup(
                principal_id=None, csrf_token_hash=None, expires_at=None, revoked=False
            )
        return WebSessionLookup(
            principal_id=row["principal_id"],
            csrf_token_hash=row["csrf_token_hash"],
            expires_at=datetime.fromisoformat(row["expires_at"]),
            revoked=row["revoked_at"] is not None,
        )

    def revoke(self, raw_token: str) -> None:
        self._conn.execute(
            "UPDATE web_session SET revoked_at = ? WHERE token_hash = ?",
            (_now(), hash_token(raw_token)),
        )
        self._conn.commit()

    def revoke_all_for_principal(self, principal_id: str) -> None:
        """Revoke every browser session this principal holds, not just the calling one.

        Without this, disconnecting from one browser would leave any other
        concurrent ``web_session`` (a second device, an old/leaked cookie) fully
        valid -- still able to list and decide that principal's proposals -- even
        though ``disconnect_principal`` already tore down the Google credential and
        connector tokens (docs/AUTH.md "Disconnect").
        """
        self._conn.execute(
            "UPDATE web_session SET revoked_at = ? WHERE principal_id = ? AND revoked_at IS NULL",
            (_now(), principal_id),
        )
        self._conn.commit()


__all__ = [
    "WebLoginStateRepository",
    "WebSessionRepository",
    "WebSessionIssued",
    "WebSessionLookup",
]
