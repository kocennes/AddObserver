"""Secret storage for Google refresh tokens.

``docs/SECURITY.md`` requires refresh tokens to live in "a secrets manager/KMS-backed
vault, separate from application data; the DB holds only a vault reference" -- and
explicitly lists the production secrets manager choice as an open question (``TBD``).

``LocalEncryptedVault`` is a **local/dev-only** stand-in that satisfies the
``VaultClient`` shape so the rest of the OAuth flow (which only ever handles a
``vault_ref``, never a raw secret) can be built and tested now. It is NOT the
production secrets manager and must not be deployed as one.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken


class VaultError(RuntimeError):
    """Raised when a vault reference cannot be stored, read or revoked."""


class VaultClient(Protocol):
    """Storage boundary for Google refresh tokens. Only a ``vault_ref`` ever leaves it."""

    def store(self, secret: str) -> str:
        """Persist ``secret`` and return an opaque reference (never the secret itself)."""
        ...

    def read(self, vault_ref: str) -> str:
        """Return the secret for a reference, or raise ``VaultError`` if revoked/unknown."""
        ...

    def revoke(self, vault_ref: str) -> None:
        """Permanently destroy the secret behind a reference."""
        ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LocalEncryptedVault:
    """Fernet-encrypted, sqlite-backed vault for local development and tests only.

    ``key`` must be a urlsafe-base64 32-byte Fernet key (``Fernet.generate_key()``).
    Ciphertext lives in the ``vault_secret`` table (see ``backend/src/db/schema.py``);
    the plaintext secret never touches any other table, log line or exception message.
    """

    def __init__(self, conn: sqlite3.Connection, key: bytes | str):
        self._conn = conn
        self._fernet = Fernet(key)

    def store(self, secret: str) -> str:
        vault_ref = str(uuid.uuid4())
        ciphertext = self._fernet.encrypt(secret.encode("utf-8"))
        self._conn.execute(
            "INSERT INTO vault_secret (vault_ref, ciphertext, created_at, revoked_at) "
            "VALUES (?, ?, ?, NULL)",
            (vault_ref, ciphertext, _now()),
        )
        self._conn.commit()
        return vault_ref

    def read(self, vault_ref: str) -> str:
        row = self._conn.execute(
            "SELECT ciphertext, revoked_at FROM vault_secret WHERE vault_ref = ?", (vault_ref,)
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            raise VaultError("vault_ref bulunamadi veya iptal edilmis.")
        try:
            return self._fernet.decrypt(row["ciphertext"]).decode("utf-8")
        except InvalidToken as error:
            raise VaultError("vault_ref cozulemedi.") from error

    def revoke(self, vault_ref: str) -> None:
        self._conn.execute(
            "UPDATE vault_secret SET revoked_at = ? WHERE vault_ref = ?", (_now(), vault_ref)
        )
        self._conn.commit()
