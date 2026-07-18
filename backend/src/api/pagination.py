"""Opaque, signed pagination cursors for principal-scoped list endpoints.

See ``docs/API_DESIGN.md`` "Pagination sozlesmesi" for the accepted contract (todo.md 1.5):
a cursor is never a raw offset. It encodes a keyset position (``after_created_at``,
``after_id``) plus the exact request context (``principal_id``, ``customer_id``, ``status``)
it was minted for, HMAC-signed so a client cannot forge a fresh expiry or splice a position
from one context into another. Every failure (bad signature, expired, wrong context) raises
the same ``InvalidCursorError`` -- callers must not report which check failed, so a forged
cursor can never be used to probe whether another principal's data exists.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: Bounded like every other short-lived token in this codebase (docs/AUTH.md); a cursor is a
#: continuation hint for one pagination session, not a durable resource identifier.
CURSOR_TTL = timedelta(minutes=15)
_SIGNATURE_LENGTH = hashlib.sha256().digest_size
#: HKDF-style "info" label: derives a cursor-signing subkey from the vault key without ever
#: reusing that key's own Fernet material for a different purpose (key separation).
_KEY_INFO = b"addobserver-api-pagination-cursor-v1"


class InvalidCursorError(ValueError):
    """Raised for a malformed, tampered, expired or context-mismatched cursor."""


@dataclass(frozen=True)
class CursorPosition:
    """Keyset position a list query resumes from."""

    after_created_at: str
    after_id: str


def _derive_signing_key(vault_key: str) -> bytes:
    return hmac.new(vault_key.encode("utf-8"), _KEY_INFO, hashlib.sha256).digest()


def encode_cursor(
    vault_key: str,
    *,
    principal_id: str,
    customer_id: str | None,
    status: str,
    position: CursorPosition,
    now: datetime,
) -> str:
    """Mint an opaque cursor bound to this exact request context, valid for ``CURSOR_TTL``."""
    payload = {
        "principal_id": principal_id,
        "customer_id": customer_id,
        "status": status,
        "after_created_at": position.after_created_at,
        "after_id": position.after_id,
        "issued_at": now.astimezone(UTC).isoformat(),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_derive_signing_key(vault_key), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode("ascii").rstrip("=")


def decode_cursor(
    vault_key: str,
    cursor: str,
    *,
    principal_id: str,
    customer_id: str | None,
    status: str,
    now: datetime,
) -> CursorPosition:
    """Return the keyset position encoded in ``cursor``, or raise ``InvalidCursorError``."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, binascii.Error) as error:
        raise InvalidCursorError("cursor gecersiz") from error
    if len(raw) <= _SIGNATURE_LENGTH:
        raise InvalidCursorError("cursor gecersiz")
    body, signature = raw[:-_SIGNATURE_LENGTH], raw[-_SIGNATURE_LENGTH:]
    expected_signature = hmac.new(_derive_signing_key(vault_key), body, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidCursorError("cursor gecersiz")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidCursorError("cursor gecersiz") from error
    if not isinstance(payload, dict):
        raise InvalidCursorError("cursor gecersiz")
    if (
        payload.get("principal_id") != principal_id
        or payload.get("customer_id") != customer_id
        or payload.get("status") != status
    ):
        raise InvalidCursorError("cursor gecersiz")
    issued_at_raw = payload.get("issued_at")
    if not isinstance(issued_at_raw, str):
        raise InvalidCursorError("cursor gecersiz")
    try:
        issued_at = datetime.fromisoformat(issued_at_raw)
    except ValueError as error:
        raise InvalidCursorError("cursor gecersiz") from error
    if issued_at.tzinfo is None:
        raise InvalidCursorError("cursor gecersiz")
    if now.astimezone(UTC) - issued_at.astimezone(UTC) > CURSOR_TTL:
        raise InvalidCursorError("cursor suresi doldu")
    after_created_at = payload.get("after_created_at")
    after_id = payload.get("after_id")
    if not isinstance(after_created_at, str) or not isinstance(after_id, str):
        raise InvalidCursorError("cursor gecersiz")
    return CursorPosition(after_created_at=after_created_at, after_id=after_id)
