"""Fail-closed rules for the browser-facing approval login/session (docs/AUTH.md).

This is a *second*, much lighter authentication plane than the connector OAuth AS
in ``auth/domain.py``: it only proves "this browser belongs to principal X" so a
human can approve/reject a proposal outside Claude's tool-calling loop
(docs/ARCHITECTURE.md -- "human confirmation" must not be an MCP tool). It never
issues Google Ads scope, never touches the vault, and never creates a principal --
only a caller that already completed the real Google Ads connect flow may sign in
here (see ``backend.src.auth.approvals_routes``).

No sqlite, no FastAPI, no network I/O lives here (mirrors ``auth/domain.py`` and
``approval/domain.py``'s separation of pure rules from storage/transport).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .domain import AuthError, hash_token

LOGIN_STATE_TTL_SECONDS = 600
WEB_SESSION_TTL_SECONDS = 30 * 60


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuthError("invalid_request", "Zaman timezone bilgisi icermelidir.")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class WebLoginState:
    """A single-use, short-lived ``state`` value binding ``/login`` to its callback."""

    state: str
    expires_at: datetime


def issue_login_state(*, now: datetime, ttl_seconds: int = LOGIN_STATE_TTL_SECONDS) -> WebLoginState:
    """Issue a fresh, unpredictable login state (RFC 6749 ``state`` role)."""
    return WebLoginState(state=secrets.token_urlsafe(32), expires_at=_utc(now) + timedelta(seconds=ttl_seconds))


def redeem_login_state(*, already_consumed: bool, expires_at: datetime, now: datetime) -> None:
    """Validate a claimed login state. Raises ``AuthError`` if reused or expired.

    ``already_consumed`` must come from the store's atomic single-use claim (an
    ``UPDATE ... WHERE status = 'pending'``), mirroring
    ``auth.domain.consume_authorization_code`` -- this function is pure and only
    judges validity, it never mutates.
    """
    if already_consumed:
        raise AuthError("invalid_grant", "Giris islemi zaten kullanilmis.")
    if _utc(now) >= _utc(expires_at):
        raise AuthError("invalid_grant", "Giris isleminin suresi dolmus.")


@dataclass(frozen=True, slots=True)
class WebSession:
    """A newly issued browser session: the raw token plus its paired CSRF token.

    Both are random and independent -- knowing one must never reveal the other,
    since the CSRF token is embedded in rendered HTML (and so is a weaker secret)
    while the session token only ever travels as an HttpOnly cookie.
    """

    token: str
    csrf_token: str
    principal_id: str
    expires_at: datetime


def issue_web_session(
    principal_id: str, *, now: datetime, ttl_seconds: int = WEB_SESSION_TTL_SECONDS
) -> WebSession:
    """Issue a new approval-UI session for an already-resolved principal."""
    if not principal_id:
        raise AuthError("invalid_request", "principal_id zorunludur.")
    return WebSession(
        token=secrets.token_urlsafe(32),
        csrf_token=secrets.token_urlsafe(32),
        principal_id=principal_id,
        expires_at=_utc(now) + timedelta(seconds=ttl_seconds),
    )


@dataclass(frozen=True, slots=True)
class AuthenticatedWebSession:
    """The result of successfully verifying a browser session token."""

    principal_id: str
    csrf_token_hash: str


def verify_web_session(
    *,
    principal_id: str | None,
    csrf_token_hash: str | None,
    expires_at: datetime | None,
    revoked: bool,
    now: datetime,
) -> AuthenticatedWebSession:
    """Verify an already-looked-up session row. Fails closed on any missing/invalid field.

    Callers (``db.web_session_store.WebSessionRepository.get_principal``) pass
    ``None``/``revoked=True`` for an unknown/revoked token so this function has a
    single fail-closed path, matching ``auth.deps.verify_access_token``'s shape.
    """
    if principal_id is None or csrf_token_hash is None or expires_at is None:
        raise AuthError("invalid_token", "Oturum bilinmiyor veya iptal edilmis.")
    if revoked:
        raise AuthError("invalid_token", "Oturum iptal edilmis.")
    if _utc(now) >= _utc(expires_at):
        raise AuthError("invalid_token", "Oturumun suresi dolmus.")
    return AuthenticatedWebSession(principal_id=principal_id, csrf_token_hash=csrf_token_hash)


def verify_csrf_token(presented: str | None, expected_hash: str) -> None:
    """Constant-time CSRF field check for every state-changing ``/approvals`` POST."""
    if not presented or not secrets.compare_digest(hash_token(presented), expected_hash):
        raise AuthError("invalid_csrf", "CSRF token eslesmiyor veya eksik.")


__all__ = [
    "hash_token",
    "LOGIN_STATE_TTL_SECONDS",
    "WEB_SESSION_TTL_SECONDS",
    "WebLoginState",
    "issue_login_state",
    "redeem_login_state",
    "WebSession",
    "issue_web_session",
    "AuthenticatedWebSession",
    "verify_web_session",
    "verify_csrf_token",
]
