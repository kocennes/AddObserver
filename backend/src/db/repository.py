"""Principal-scoped repositories for identity, account linking and credentials.

Her metod açık ``principal_id`` alır; DATABASE.md kararı gereği bu, gelecekteki Postgres
RLS'in yerine geçen bir savunma katmanı değil onun ÖN KOŞULUDUR — repository filtresi RLS
uygulanana kadar TEK izolasyon sınırıdır.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from .models import AdsAccount, CredentialStatus, OAuthCredential, Principal, PrincipalStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class PrincipalRepository:
    """Identity root lookups. A principal is created lazily on first sign-in."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def get(self, issuer: str, subject: str) -> Principal | None:
        """Look up a principal without creating one (never used by the real Ads-connect flow)."""
        row = self._conn.execute(
            "SELECT * FROM principal WHERE issuer = ? AND subject = ?", (issuer, subject)
        ).fetchone()
        return None if row is None else _principal_from_row(row)

    def get_or_create(self, issuer: str, subject: str) -> Principal:
        """Return the existing principal for ``(issuer, subject)`` or create one."""
        row = self._conn.execute(
            "SELECT * FROM principal WHERE issuer = ? AND subject = ?", (issuer, subject)
        ).fetchone()
        if row is not None:
            return _principal_from_row(row)
        principal = Principal(
            id=_new_id(),
            issuer=issuer,
            subject=subject,
            status=PrincipalStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
        )
        self._conn.execute(
            "INSERT INTO principal (id, issuer, subject, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (principal.id, principal.issuer, principal.subject, principal.status.value, _now()),
        )
        self._conn.commit()
        return principal


def _principal_from_row(row: sqlite3.Row) -> Principal:
    return Principal(
        id=row["id"],
        issuer=row["issuer"],
        subject=row["subject"],
        status=PrincipalStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class AdsAccountRepository:
    """Links a principal to the Google Ads customer_ids it may access."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def link_account(
        self, principal_id: str, customer_id: str, login_customer_id: str | None
    ) -> AdsAccount:
        """Link a Google Ads customer_id to a principal. Idempotent on (principal_id, customer_id).

        If a previously disconnected account is linked again after a fresh Google
        authorization, reactivate the existing row instead of creating a second
        historical identity for the same customer.
        """
        existing = self.get_account(principal_id, customer_id)
        if existing is not None:
            if existing.status != "active" or existing.login_customer_id != login_customer_id:
                self._conn.execute(
                    "UPDATE ads_account SET status = ?, login_customer_id = ? WHERE id = ? AND principal_id = ?",
                    ("active", login_customer_id, existing.id, principal_id),
                )
                self._conn.commit()
                refreshed = self.get_account(principal_id, customer_id)
                assert refreshed is not None
                return refreshed
            return existing
        account = AdsAccount(
            id=_new_id(),
            principal_id=principal_id,
            customer_id=customer_id,
            login_customer_id=login_customer_id,
            status="active",
            created_at=datetime.now(timezone.utc),
        )
        self._conn.execute(
            "INSERT INTO ads_account (id, principal_id, customer_id, login_customer_id, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                account.id, account.principal_id, account.customer_id,
                account.login_customer_id, account.status, _now(),
            ),
        )
        self._conn.commit()
        return account

    def get_account(self, principal_id: str, customer_id: str) -> AdsAccount | None:
        """Return the account row for history/admin use, regardless of active/disconnected status."""
        row = self._conn.execute(
            "SELECT * FROM ads_account WHERE principal_id = ? AND customer_id = ?",
            (principal_id, customer_id),
        ).fetchone()
        return None if row is None else _account_from_row(row)

    def get_active_account(self, principal_id: str, customer_id: str) -> AdsAccount | None:
        """Return an account only if it belongs to ``principal_id`` and is still active."""
        row = self._conn.execute(
            "SELECT * FROM ads_account WHERE principal_id = ? AND customer_id = ? AND status = ?",
            (principal_id, customer_id, "active"),
        ).fetchone()
        return None if row is None else _account_from_row(row)

    def list_accounts(self, principal_id: str) -> list[AdsAccount]:
        """Return all account rows for history/admin use, including disconnected rows."""
        rows = self._conn.execute(
            "SELECT * FROM ads_account WHERE principal_id = ? ORDER BY created_at", (principal_id,)
        ).fetchall()
        return [_account_from_row(row) for row in rows]

    def list_active_accounts(self, principal_id: str) -> list[AdsAccount]:
        """Return only accounts that can be used for future reads/proposals."""
        rows = self._conn.execute(
            "SELECT * FROM ads_account WHERE principal_id = ? AND status = ? ORDER BY created_at",
            (principal_id, "active"),
        ).fetchall()
        return [_account_from_row(row) for row in rows]

    def disconnect_all(self, principal_id: str) -> None:
        """Mark every account linked to this principal as disconnected.

        Rows are kept, not deleted, so ``proposal``/``approval``/``audit_event`` history
        referencing ``customer_id`` stays intact (docs/AUTH.md disconnect decision).
        """
        self._conn.execute(
            "UPDATE ads_account SET status = ? WHERE principal_id = ?",
            ("disconnected", principal_id),
        )
        self._conn.commit()


def _account_from_row(row: sqlite3.Row) -> AdsAccount:
    return AdsAccount(
        id=row["id"],
        principal_id=row["principal_id"],
        customer_id=row["customer_id"],
        login_customer_id=row["login_customer_id"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class OAuthCredentialRepository:
    """Stores only a secrets-manager reference for each principal's Google credential."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert(self, principal_id: str, vault_ref: str, key_version: int) -> OAuthCredential:
        """Revoke any prior active credential and store the new reference (never a secret value)."""
        self._conn.execute(
            "UPDATE oauth_credential SET status = ? WHERE principal_id = ? AND status = ?",
            (CredentialStatus.REVOKED.value, principal_id, CredentialStatus.ACTIVE.value),
        )
        credential = OAuthCredential(
            id=_new_id(),
            principal_id=principal_id,
            vault_ref=vault_ref,
            status=CredentialStatus.ACTIVE,
            key_version=key_version,
            created_at=datetime.now(timezone.utc),
        )
        self._conn.execute(
            "INSERT INTO oauth_credential (id, principal_id, vault_ref, status, key_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                credential.id, credential.principal_id, credential.vault_ref,
                credential.status.value, credential.key_version, _now(),
            ),
        )
        self._conn.commit()
        return credential

    def get_active(self, principal_id: str) -> OAuthCredential | None:
        row = self._conn.execute(
            "SELECT * FROM oauth_credential WHERE principal_id = ? AND status = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (principal_id, CredentialStatus.ACTIVE.value),
        ).fetchone()
        return None if row is None else _credential_from_row(row)

    def revoke(self, principal_id: str, credential_id: str) -> None:
        self._conn.execute(
            "UPDATE oauth_credential SET status = ? WHERE id = ? AND principal_id = ?",
            (CredentialStatus.REVOKED.value, credential_id, principal_id),
        )
        self._conn.commit()

    def revoke_active(self, principal_id: str) -> OAuthCredential | None:
        """Revoke this principal's active credential (if any) and return the pre-revoke
        record, so the caller can also destroy the vault secret it points to."""
        credential = self.get_active(principal_id)
        if credential is None:
            return None
        self.revoke(principal_id, credential.id)
        return credential


def _credential_from_row(row: sqlite3.Row) -> OAuthCredential:
    return OAuthCredential(
        id=row["id"],
        principal_id=row["principal_id"],
        vault_ref=row["vault_ref"],
        status=CredentialStatus(row["status"]),
        key_version=row["key_version"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
