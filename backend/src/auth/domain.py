"""Fail-closed OAuth 2.1 connector authorization-server state machine.

Pure, framework-free rules for PKCE (RFC 7636), redirect URI matching (including the
RFC 8252 loopback exception Claude Code needs), Client ID Metadata Document (CIMD)
validation, and the authorization transaction -> code -> token lifecycle including
refresh rotation/reuse detection. No sqlite, no FastAPI, no network I/O lives here
(see ``backend.src.db.oauth_store`` for persistence, ``backend.src.auth.cimd`` for
the CIMD network fetch, ``backend.src.auth.server`` for HTTP wiring) -- this mirrors
``backend/src/approval/domain.py``'s separation of pure rules from storage/transport.

Every raised error carries an RFC 6749 ``code`` (``invalid_grant``, ``invalid_client``,
``invalid_request``, ``invalid_target``) so the HTTP layer can return a spec-compliant
JSON error body (docs/AUTH.md, Anthropic's connector-authentication docs) instead of
inventing its own error taxonomy.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Mapping
from urllib.parse import urlsplit


class AuthError(ValueError):
    """A safe, stable failure raised when an OAuth invariant is violated."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuthError("invalid_request", "Zaman timezone bilgisi icermelidir.")
    return value.astimezone(timezone.utc)


def hash_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest stored in place of a raw token/code value."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PKCE (RFC 7636) -- S256 only, per SECURITY.md and the MCP authorization spec.
# ---------------------------------------------------------------------------

def compute_code_challenge(code_verifier: str) -> str:
    """Return the S256 code_challenge for a verifier (also used by tests/tools)."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify_pkce(code_verifier: str, code_challenge: str, code_challenge_method: str) -> bool:
    """Verify a presented code_verifier against a stored S256 challenge."""
    if code_challenge_method != "S256":
        return False
    if not code_verifier or not code_challenge:
        return False
    expected = compute_code_challenge(code_verifier)
    return secrets.compare_digest(expected, code_challenge)


# ---------------------------------------------------------------------------
# Redirect URI matching -- exact match, plus the RFC 8252 loopback exception.
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_loopback_uri(uri: str) -> bool:
    parsed = urlsplit(uri)
    return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS


def redirect_uri_allowed(candidate: str, registered: str) -> bool:
    """Return True if ``candidate`` may be used given a ``registered`` redirect_uri.

    Exact match always passes. Native loopback clients (Claude Code) bind an
    ephemeral port at runtime, so RFC 8252 s7.3 requires the port to be ignored for
    ``127.0.0.1``/``::1``; Anthropic's connector docs extend the same tolerance to
    ``localhost`` for compatibility. Scheme, host, path and query must still match.
    """
    if candidate == registered:
        return True
    cand = urlsplit(candidate)
    reg = urlsplit(registered)
    if cand.scheme != "http" or reg.scheme != "http":
        return False
    if cand.hostname not in _LOOPBACK_HOSTS or cand.hostname != reg.hostname:
        return False
    return (cand.path, cand.query) == (reg.path, reg.query)


# ---------------------------------------------------------------------------
# Client ID Metadata Document (CIMD) validation.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """A resolved OAuth client. In this increment, always sourced from a CIMD document."""

    client_id: str
    redirect_uris: tuple[str, ...]
    token_endpoint_auth_method: str


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return (parsed.scheme, parsed.hostname or "", parsed.port)


def validate_cimd_document(claimed_client_id_url: str, document: Mapping[str, object]) -> ClientIdentity:
    """Validate an already-fetched CIMD document (draft-ietf-oauth-client-id-metadata-document).

    The document must be self-referential (its own ``client_id`` equals the URL it was
    served from) and every non-loopback ``redirect_uris`` entry must be same-origin
    with that URL -- both required by Anthropic's connector-authentication guidance.
    Loopback redirect URIs (Claude Code) are exempt from the same-origin rule, as they
    are for any native OAuth client. The network fetch and SSRF guarding happen in
    ``backend.src.auth.cimd``; this function only judges document content.
    """
    if document.get("client_id") != claimed_client_id_url:
        raise AuthError("invalid_client", "CIMD dokumani kendine referansli degil.")
    redirect_uris = document.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise AuthError("invalid_client", "CIMD dokumaninda redirect_uris eksik.")
    claimed_origin = _origin(claimed_client_id_url)
    validated: list[str] = []
    for uri in redirect_uris:
        if not isinstance(uri, str) or not uri:
            raise AuthError("invalid_client", "redirect_uris yalniz dolu string icerebilir.")
        if not _is_loopback_uri(uri) and _origin(uri) != claimed_origin:
            raise AuthError("invalid_client", "redirect_uri, client_id ile ayni origin'de olmalidir.")
        validated.append(uri)
    auth_method = document.get("token_endpoint_auth_method", "none")
    if auth_method != "none":
        raise AuthError("invalid_client", "CIMD istemcisi yalniz public client (none) olabilir.")
    return ClientIdentity(
        client_id=claimed_client_id_url,
        redirect_uris=tuple(validated),
        token_endpoint_auth_method=str(auth_method),
    )


# ---------------------------------------------------------------------------
# Authorization transaction (the /authorize request, pre-consent).
# ---------------------------------------------------------------------------

class TransactionStatus(StrEnum):
    PENDING = "pending"
    CONSENTED = "consented"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class AuthorizationTransaction:
    """An in-flight /authorize request, bound to one CIMD-resolved client and PKCE challenge."""

    transaction_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    resource: str
    scope: str
    client_state: str
    expires_at: datetime
    status: TransactionStatus = TransactionStatus.PENDING

    @classmethod
    def create(
        cls,
        *,
        transaction_id: str,
        client: ClientIdentity,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        resource: str,
        expected_resource: str,
        scope: str,
        client_state: str,
        now: datetime,
        ttl_seconds: int = 600,
    ) -> "AuthorizationTransaction":
        """Validate and open a new transaction. Fails closed on any RFC 8707/PKCE mismatch."""
        if code_challenge_method != "S256":
            raise AuthError("invalid_request", "Yalniz S256 code_challenge_method desteklenir.")
        if not code_challenge:
            raise AuthError("invalid_request", "code_challenge zorunludur.")
        if not any(redirect_uri_allowed(redirect_uri, reg) for reg in client.redirect_uris):
            raise AuthError("invalid_request", "redirect_uri istemci kaydiyla eslesmiyor.")
        if resource != expected_resource:
            raise AuthError("invalid_target", "resource bu MCP sunucusuyla eslesmiyor.")
        if not transaction_id or not client_state:
            raise AuthError("invalid_request", "transaction_id ve state zorunludur.")
        return cls(
            transaction_id=transaction_id,
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            scope=scope,
            client_state=client_state,
            expires_at=_utc(now) + timedelta(seconds=ttl_seconds),
        )


def consent_transaction(transaction: AuthorizationTransaction, *, now: datetime) -> AuthorizationTransaction:
    """Move a pending transaction into consented, ready for the Google upstream leg."""
    if transaction.status is not TransactionStatus.PENDING:
        raise AuthError("invalid_request", "Islem onay bekleyen durumda degil.")
    if _utc(now) >= transaction.expires_at:
        raise AuthError("invalid_request", "Islemin suresi dolmus.")
    return replace(transaction, status=TransactionStatus.CONSENTED)


def complete_transaction(transaction: AuthorizationTransaction, *, now: datetime) -> AuthorizationTransaction:
    """Close out a transaction once our own authorization code has been issued."""
    if transaction.status is not TransactionStatus.CONSENTED:
        raise AuthError("invalid_request", "Islem onaylanmadan tamamlanamaz.")
    if _utc(now) >= transaction.expires_at:
        raise AuthError("invalid_request", "Islemin suresi dolmus.")
    return replace(transaction, status=TransactionStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Authorization code -- single-use, bound to the transaction's exact PKCE/resource.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AuthorizationCode:
    """A single-use grant tying a resolved principal to the original /authorize request."""

    code: str
    transaction_id: str
    principal_id: str
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str
    resource: str
    scope: str
    expires_at: datetime


def issue_authorization_code(
    transaction: AuthorizationTransaction,
    *,
    principal_id: str,
    now: datetime,
    ttl_seconds: int = 60,
) -> AuthorizationCode:
    """Issue our own authorization code once the Google upstream leg resolved a principal."""
    if transaction.status is not TransactionStatus.CONSENTED:
        raise AuthError("invalid_request", "Onay tamamlanmadan kod uretilemez.")
    if _utc(now) >= transaction.expires_at:
        raise AuthError("invalid_request", "Islemin suresi dolmus.")
    if not principal_id:
        raise AuthError("invalid_request", "principal_id zorunludur.")
    return AuthorizationCode(
        code=secrets.token_urlsafe(32),
        transaction_id=transaction.transaction_id,
        principal_id=principal_id,
        client_id=transaction.client_id,
        redirect_uri=transaction.redirect_uri,
        code_challenge=transaction.code_challenge,
        code_challenge_method=transaction.code_challenge_method,
        resource=transaction.resource,
        scope=transaction.scope,
        expires_at=_utc(now) + timedelta(seconds=ttl_seconds),
    )


@dataclass(frozen=True, slots=True)
class CodeGrant:
    """Proof that an authorization_code grant satisfied every RFC 6749 + PKCE check."""

    principal_id: str
    client_id: str
    resource: str
    scope: str


def consume_authorization_code(
    stored: AuthorizationCode,
    *,
    client_id: str,
    redirect_uri: str,
    resource: str,
    code_verifier: str,
    already_consumed: bool,
    now: datetime,
) -> CodeGrant:
    """Validate a presented authorization_code against its stored record.

    ``already_consumed`` must come from the store's atomic single-use claim (an
    ``UPDATE ... WHERE consumed_at IS NULL``) so a race can never redeem the same
    code twice; this function is pure and only judges validity, it never mutates.
    """
    if already_consumed:
        raise AuthError("invalid_grant", "Yetkilendirme kodu zaten kullanilmis.")
    if _utc(now) >= stored.expires_at:
        raise AuthError("invalid_grant", "Yetkilendirme kodunun suresi dolmus.")
    if stored.client_id != client_id:
        raise AuthError("invalid_client", "Kod bu istemciye ait degil.")
    if stored.redirect_uri != redirect_uri:
        raise AuthError("invalid_grant", "redirect_uri kod ile eslesmiyor.")
    if stored.resource != resource:
        raise AuthError("invalid_target", "resource kod ile eslesmiyor.")
    if not verify_pkce(code_verifier, stored.code_challenge, stored.code_challenge_method):
        raise AuthError("invalid_grant", "PKCE dogrulamasi basarisiz.")
    return CodeGrant(
        principal_id=stored.principal_id,
        client_id=stored.client_id,
        resource=stored.resource,
        scope=stored.scope,
    )


# ---------------------------------------------------------------------------
# Access / refresh tokens -- rotation with reuse detection (OAuth 2.1 token theft).
# ---------------------------------------------------------------------------

class RefreshTokenStatus(StrEnum):
    ACTIVE = "active"
    ROTATED = "rotated"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class AccessToken:
    token: str
    principal_id: str
    client_id: str
    resource: str
    scope: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RefreshToken:
    token: str
    family_id: str
    principal_id: str
    client_id: str
    resource: str
    scope: str
    expires_at: datetime
    status: RefreshTokenStatus = RefreshTokenStatus.ACTIVE


ACCESS_TOKEN_TTL_SECONDS = 600
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30


def issue_token_pair(
    grant: CodeGrant,
    *,
    now: datetime,
    access_ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
    refresh_ttl_seconds: int = REFRESH_TOKEN_TTL_SECONDS,
) -> tuple[AccessToken, RefreshToken]:
    """Issue the first access/refresh pair for a freshly redeemed authorization code."""
    current_time = _utc(now)
    access = AccessToken(
        token=secrets.token_urlsafe(32),
        principal_id=grant.principal_id,
        client_id=grant.client_id,
        resource=grant.resource,
        scope=grant.scope,
        expires_at=current_time + timedelta(seconds=access_ttl_seconds),
    )
    refresh = RefreshToken(
        token=secrets.token_urlsafe(32),
        family_id=secrets.token_urlsafe(16),
        principal_id=grant.principal_id,
        client_id=grant.client_id,
        resource=grant.resource,
        scope=grant.scope,
        expires_at=current_time + timedelta(seconds=refresh_ttl_seconds),
    )
    return access, refresh


@dataclass(frozen=True, slots=True)
class RefreshOutcome:
    access_token: AccessToken
    refresh_token: RefreshToken


def rotate_refresh_token(
    stored: RefreshToken,
    *,
    now: datetime,
    access_ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
) -> RefreshOutcome:
    """Rotate one *already-confirmed-active* refresh token into a new access/refresh pair.

    Reuse detection is a storage-layer concern (it must revoke every row sharing
    ``family_id``, not just judge one record) and therefore lives in
    ``backend.src.db.oauth_store.TokenRepository.rotate``, which calls this function
    only after confirming ``stored.status`` is ``ACTIVE``. This function still
    re-checks status defensively and fails closed if it is not.
    """
    if stored.status is not RefreshTokenStatus.ACTIVE:
        raise AuthError("invalid_grant", "refresh_token gecerli degil.")
    current_time = _utc(now)
    if current_time >= stored.expires_at:
        raise AuthError("invalid_grant", "refresh_token suresi dolmus.")
    access = AccessToken(
        token=secrets.token_urlsafe(32),
        principal_id=stored.principal_id,
        client_id=stored.client_id,
        resource=stored.resource,
        scope=stored.scope,
        expires_at=current_time + timedelta(seconds=access_ttl_seconds),
    )
    new_refresh = RefreshToken(
        token=secrets.token_urlsafe(32),
        family_id=stored.family_id,
        principal_id=stored.principal_id,
        client_id=stored.client_id,
        resource=stored.resource,
        scope=stored.scope,
        expires_at=stored.expires_at,
    )
    return RefreshOutcome(access_token=access, refresh_token=new_refresh)
