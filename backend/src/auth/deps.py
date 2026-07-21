"""Connector access-token verification.

Built now so the still-nonexistent ``backend/src/mcp/`` increment can depend on it
directly instead of re-deriving audience/expiry/revocation checks. Nothing in this
module is wired to an ``/mcp`` route yet -- there is no such route in this increment.

Per ``docs/SECURITY.md`` ("Token audience, issuer, signature, expiry, scope ve
subject her MCP isteğinde doğrulanır") and RFC 8707, a token is only valid for the
one MCP ``resource`` URI it was issued for -- this is checked on every call, not
just at issuance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from fastapi import HTTPException, Request

from .domain import AccessToken


class AccessTokenStore(Protocol):
    """Minimal repository contract required for bearer verification."""

    def get_access(self, raw_token: str) -> AccessToken | None:
        """Return the active token metadata matching ``raw_token``, if any."""
        ...


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """The result of a successfully verified connector access token."""

    principal_id: str
    client_id: str
    scope: str


class BearerTokenError(Exception):
    """A verification failure, carrying an RFC 6750 ``error`` for ``WWW-Authenticate``."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


def extract_bearer_token(authorization_header: str | None) -> str | None:
    """Return the raw token from an ``Authorization: Bearer <token>`` header, or None."""
    if not authorization_header:
        return None
    scheme, _, value = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def verify_access_token(
    raw_token: str,
    tokens: AccessTokenStore,
    *,
    expected_resource: str,
    now: datetime,
) -> AuthenticatedPrincipal:
    """Verify a raw bearer token: known, unexpired, unrevoked, and audience-bound.

    ``tokens.get_access`` already excludes revoked rows (see ``db/oauth_store.py``);
    this function still re-checks resource/expiry explicitly so the audience/expiry
    rule is visible at the call site rather than only implicit in the repository.
    """
    token = tokens.get_access(raw_token)
    if token is None:
        raise BearerTokenError("invalid_token", "Access token bilinmiyor veya iptal edilmis.")
    if token.resource != expected_resource:
        raise BearerTokenError(
            "invalid_token", "Access token bu kaynak (resource) icin gecerli degil."
        )
    if now.astimezone(UTC) >= token.expires_at:
        raise BearerTokenError("invalid_token", "Access token suresi dolmus.")
    return AuthenticatedPrincipal(
        principal_id=token.principal_id, client_id=token.client_id, scope=token.scope
    )


def www_authenticate_header(
    *, protected_resource_metadata_url: str, error: str | None = None, scope: str | None = None
) -> str:
    """Build the canonical ``WWW-Authenticate`` value Anthropic's connector docs expect."""
    parts = ["Bearer"]
    if error:
        parts.append(f'error="{error}"')
    parts.append(f'resource_metadata="{protected_resource_metadata_url}"')
    if scope:
        parts.append(f'scope="{scope}"')
    return ", ".join([parts[0], *parts[1:]])


def require_principal(
    tokens: AccessTokenStore, *, expected_resource: str, protected_resource_metadata_url: str
):
    """FastAPI dependency factory: verifies the request's bearer token or raises 401.

    Returns a real transport-level ``401`` (not a ``200`` wrapping an error) per
    Anthropic's "Return 401, not a tool error" guidance, so a future protected MCP
    route can depend on this directly.
    """

    def _dependency(request: Request) -> AuthenticatedPrincipal:
        raw_token = extract_bearer_token(request.headers.get("Authorization"))
        header = www_authenticate_header(
            protected_resource_metadata_url=protected_resource_metadata_url,
            error="invalid_token" if raw_token else None,
        )
        if raw_token is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required.",
                headers={"WWW-Authenticate": header},
            )
        try:
            return verify_access_token(
                raw_token,
                tokens,
                expected_resource=expected_resource,
                now=datetime.now(UTC),
            )
        except BearerTokenError as error:
            raise HTTPException(
                status_code=401, detail=error.description, headers={"WWW-Authenticate": header}
            ) from error

    return _dependency
