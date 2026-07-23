"""SQLAlchemy repositories for the PostgreSQL production schema.

These adapters mirror the current sqlite3 repositories for the first
production migration slice. They intentionally do not commit: callers must run
them inside ``postgres.principal_transaction`` so RLS context, commit and
rollback are handled in one place.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from sqlalchemy import Connection, RowMapping, and_, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..approval import (
    Approval,
    Decision,
    ExecutionClaim,
    ExecutionReservation,
    Proposal,
    ProposalStatus,
)
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
from .models import (
    AdsAccount,
    AuditEvent,
    CredentialRevocationJob,
    CredentialRevocationStatus,
    CredentialStatus,
    ExecutionStatus,
    OAuthCredential,
    Principal,
    PrincipalStatus,
)
from .proposals import MAX_PENDING_PROPOSAL_LIMIT, ProposalPage
from .sqlalchemy_schema import (
    access_token,
    ads_account,
    approval,
    audit_event,
    authorization_code,
    authorization_transaction,
    credential_revocation_job,
    execution,
    oauth_client_grant,
    oauth_credential,
    principal,
    proposal,
    refresh_token,
    web_login_state,
    web_session,
)
from .web_session_store import WebSessionIssued, WebSessionLookup


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise TypeError("timestamp column must be a datetime or ISO datetime string")
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _payload_for_bind(connection: Connection, payload: Any) -> Any:
    if connection.dialect.name == "postgresql":
        return dict(payload)
    return json.dumps(dict(payload), allow_nan=False)


class PostgresAuthorizationTransactionRepository:
    """Persist connector authorization transactions without committing."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def save(self, transaction: AuthorizationTransaction) -> None:
        """Insert a transaction or advance the status of its immutable request."""
        existing = self._connection.execute(
            select(authorization_transaction.c.id).where(
                authorization_transaction.c.id == transaction.transaction_id
            )
        ).first()
        if existing is not None:
            expected_status = {
                TransactionStatus.CONSENTED: TransactionStatus.PENDING,
                TransactionStatus.COMPLETED: TransactionStatus.CONSENTED,
            }.get(transaction.status)
            if expected_status is None:
                raise AuthError("invalid_request", "Yetkilendirme islemi tekrar olusturulamaz.")
            cursor = self._connection.execute(
                update(authorization_transaction)
                .where(
                    authorization_transaction.c.id == transaction.transaction_id,
                    authorization_transaction.c.status == expected_status.value,
                )
                .values(status=transaction.status.value)
            )
            if cursor.rowcount != 1:
                raise AuthError("invalid_request", "Yetkilendirme islemi daha once ilerletilmis.")
            return
        self._connection.execute(
            insert(authorization_transaction).values(
                id=transaction.transaction_id,
                client_id=transaction.client_id,
                redirect_uri=transaction.redirect_uri,
                code_challenge=transaction.code_challenge,
                code_challenge_method=transaction.code_challenge_method,
                resource=transaction.resource,
                scope=transaction.scope,
                client_state=transaction.client_state,
                consent_csrf_hash=transaction.consent_csrf_hash,
                status=transaction.status.value,
                expires_at=transaction.expires_at,
                created_at=_now(),
            )
        )

    def get(self, transaction_id: str) -> AuthorizationTransaction | None:
        """Load one authorization transaction by its opaque identifier."""
        row = (
            self._connection.execute(
                select(authorization_transaction).where(
                    authorization_transaction.c.id == transaction_id
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return AuthorizationTransaction(
            transaction_id=str(row["id"]),
            client_id=str(row["client_id"]),
            redirect_uri=str(row["redirect_uri"]),
            code_challenge=str(row["code_challenge"]),
            code_challenge_method=str(row["code_challenge_method"]),
            resource=str(row["resource"]),
            scope=str(row["scope"]),
            client_state=str(row["client_state"]),
            consent_csrf_hash=str(row["consent_csrf_hash"]),
            expires_at=_datetime(row["expires_at"]),
            status=TransactionStatus(str(row["status"])),
        )


class PostgresAuthorizationCodeRepository:
    """Store hashed authorization codes and atomically claim them once."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def save(self, code: AuthorizationCode) -> None:
        """Persist a code hash; the raw bearer value never reaches the database."""
        self._connection.execute(
            insert(authorization_code).values(
                code_hash=hash_token(code.code),
                transaction_id=code.transaction_id,
                principal_id=code.principal_id,
                client_id=code.client_id,
                redirect_uri=code.redirect_uri,
                code_challenge=code.code_challenge,
                code_challenge_method=code.code_challenge_method,
                resource=code.resource,
                scope=code.scope,
                expires_at=code.expires_at,
                consumed_at=None,
                created_at=_now(),
            )
        )

    def claim(self, raw_code: str) -> tuple[AuthorizationCode, bool]:
        """Atomically consume a code and report whether another claim won."""
        code_hash = hash_token(raw_code)
        cursor = self._connection.execute(
            update(authorization_code)
            .where(
                authorization_code.c.code_hash == code_hash,
                authorization_code.c.consumed_at.is_(None),
            )
            .values(consumed_at=_now())
        )
        row = (
            self._connection.execute(
                select(authorization_code).where(authorization_code.c.code_hash == code_hash)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AuthError("invalid_grant", "Yetkilendirme kodu bulunamadi.")
        return (
            AuthorizationCode(
                code="",  # nosec B106 - only the hash is persisted
                transaction_id=str(row["transaction_id"]),
                principal_id=str(row["principal_id"]),
                client_id=str(row["client_id"]),
                redirect_uri=str(row["redirect_uri"]),
                code_challenge=str(row["code_challenge"]),
                code_challenge_method=str(row["code_challenge_method"]),
                resource=str(row["resource"]),
                scope=str(row["scope"]),
                expires_at=_datetime(row["expires_at"]),
            ),
            cursor.rowcount == 0,
        )


class PostgresTokenRepository:
    """Persist hashed connector tokens and enforce refresh-token rotation."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def save_access(self, token: AccessToken) -> None:
        """Store only the access-token hash; callers own the transaction."""
        self._connection.execute(
            insert(access_token).values(
                token_hash=hash_token(token.token),
                principal_id=token.principal_id,
                client_id=token.client_id,
                resource=token.resource,
                scope=token.scope,
                expires_at=token.expires_at,
                revoked_at=None,
                created_at=_now(),
            )
        )

    def get_access(self, raw_token: str) -> AccessToken | None:
        """Return an unrevoked access-token record without reconstructing its secret."""
        row = (
            self._connection.execute(
                select(access_token).where(access_token.c.token_hash == hash_token(raw_token))
            )
            .mappings()
            .first()
        )
        if row is None or row["revoked_at"] is not None:
            return None
        return AccessToken(
            token="",  # nosec B106 - only the hash is persisted
            principal_id=str(row["principal_id"]),
            client_id=str(row["client_id"]),
            resource=str(row["resource"]),
            scope=str(row["scope"]),
            expires_at=_datetime(row["expires_at"]),
        )

    def save_refresh(self, token: RefreshToken) -> None:
        """Store only the refresh-token hash and its rotation-family metadata."""
        self._connection.execute(
            insert(refresh_token).values(
                token_hash=hash_token(token.token),
                family_id=token.family_id,
                principal_id=token.principal_id,
                client_id=token.client_id,
                resource=token.resource,
                scope=token.scope,
                status=token.status.value,
                expires_at=token.expires_at,
                created_at=_now(),
                rotated_at=None,
            )
        )

    def rotate(self, raw_refresh_token: str, *, now: datetime) -> RefreshOutcome:
        """Rotate once; reuse revokes the complete refresh-token family."""
        token_hash = hash_token(raw_refresh_token)
        row = (
            self._connection.execute(
                select(refresh_token).where(refresh_token.c.token_hash == token_hash)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AuthError("invalid_grant", "refresh_token bulunamadi.")
        stored = _refresh_token_from_row(row)
        if stored.status is not RefreshTokenStatus.ACTIVE:
            self.revoke_family(stored.family_id)
            raise AuthError(
                "invalid_grant", "refresh_token yeniden kullanilmis; oturum ailesi iptal edildi."
            )
        outcome = rotate_refresh_token(stored, now=now)
        cursor = self._connection.execute(
            update(refresh_token)
            .where(
                refresh_token.c.token_hash == token_hash,
                refresh_token.c.status == RefreshTokenStatus.ACTIVE.value,
            )
            .values(status=RefreshTokenStatus.ROTATED.value, rotated_at=now)
        )
        if cursor.rowcount != 1:
            self.revoke_family(stored.family_id)
            raise AuthError(
                "invalid_grant", "refresh_token yeniden kullanilmis; oturum ailesi iptal edildi."
            )
        self.save_refresh(outcome.refresh_token)
        self.save_access(outcome.access_token)
        return outcome

    def revoke_family(self, family_id: str) -> None:
        """Revoke every non-revoked refresh token in one rotation family."""
        self._connection.execute(
            update(refresh_token)
            .where(
                refresh_token.c.family_id == family_id,
                refresh_token.c.status != RefreshTokenStatus.REVOKED.value,
            )
            .values(status=RefreshTokenStatus.REVOKED.value)
        )

    def revoke_all_for_principal(self, principal_id: str, *, now: datetime) -> None:
        """Revoke all connector access and refresh tokens for a principal."""
        self._connection.execute(
            update(access_token)
            .where(
                access_token.c.principal_id == principal_id,
                access_token.c.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        self._connection.execute(
            update(refresh_token)
            .where(
                refresh_token.c.principal_id == principal_id,
                refresh_token.c.status != RefreshTokenStatus.REVOKED.value,
            )
            .values(status=RefreshTokenStatus.REVOKED.value)
        )


def _refresh_token_from_row(row: RowMapping) -> RefreshToken:
    return RefreshToken(
        token="",  # nosec B106 - only the hash is persisted
        family_id=str(row["family_id"]),
        principal_id=str(row["principal_id"]),
        client_id=str(row["client_id"]),
        resource=str(row["resource"]),
        scope=str(row["scope"]),
        expires_at=_datetime(row["expires_at"]),
        status=RefreshTokenStatus(str(row["status"])),
    )


class PostgresWebLoginStateRepository:
    """Persist hashed, single-use approval-UI Google login states."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def create(self, raw_state: str, expires_at: datetime) -> None:
        """Store a pending state by hash without committing the transaction."""
        self._connection.execute(
            insert(web_login_state).values(
                state_hash=hash_token(raw_state),
                status="pending",
                expires_at=expires_at,
                created_at=_now(),
            )
        )

    def claim(self, raw_state: str) -> tuple[bool, datetime] | None:
        """Atomically consume a state, reporting replay or an unknown value."""
        state_hash = hash_token(raw_state)
        cursor = self._connection.execute(
            update(web_login_state)
            .where(
                web_login_state.c.state_hash == state_hash,
                web_login_state.c.status == "pending",
            )
            .values(status="consumed")
        )
        row = (
            self._connection.execute(
                select(web_login_state.c.expires_at).where(
                    web_login_state.c.state_hash == state_hash
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        return cursor.rowcount == 0, _datetime(row["expires_at"])


class PostgresWebSessionRepository:
    """Persist hashed approval-UI browser sessions without committing."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def create(
        self, principal_id: str, raw_token: str, csrf_token: str, expires_at: datetime
    ) -> WebSessionIssued:
        """Create a session while storing only session and CSRF hashes."""
        self._connection.execute(
            insert(web_session).values(
                token_hash=hash_token(raw_token),
                principal_id=principal_id,
                csrf_token_hash=hash_token(csrf_token),
                expires_at=expires_at,
                revoked_at=None,
                created_at=_now(),
            )
        )
        return WebSessionIssued(token=raw_token, csrf_token=csrf_token)

    def lookup(self, raw_token: str) -> WebSessionLookup:
        """Return the fail-closed lookup shape for a raw session token."""
        row = (
            self._connection.execute(
                select(web_session).where(web_session.c.token_hash == hash_token(raw_token))
            )
            .mappings()
            .first()
        )
        if row is None:
            return WebSessionLookup(
                principal_id=None, csrf_token_hash=None, expires_at=None, revoked=False
            )
        return WebSessionLookup(
            principal_id=str(row["principal_id"]),
            csrf_token_hash=str(row["csrf_token_hash"]),
            expires_at=_datetime(row["expires_at"]),
            revoked=row["revoked_at"] is not None,
        )

    def revoke(self, raw_token: str) -> None:
        """Revoke one browser session by its raw token hash."""
        self._connection.execute(
            update(web_session)
            .where(web_session.c.token_hash == hash_token(raw_token))
            .values(revoked_at=_now())
        )

    def revoke_all_for_principal(self, principal_id: str) -> None:
        """Revoke every active browser session belonging to one principal."""
        self._connection.execute(
            update(web_session)
            .where(
                web_session.c.principal_id == principal_id,
                web_session.c.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )


class PostgresPrincipalRepository:
    """Identity root lookups backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def get(self, issuer: str, subject: str) -> Principal | None:
        """Look up a principal without creating one."""
        row = (
            self._connection.execute(
                select(principal).where(
                    principal.c.issuer == issuer,
                    principal.c.subject == subject,
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _principal_from_row(row)

    def get_or_create(self, issuer: str, subject: str) -> Principal:
        """Return the existing principal for ``(issuer, subject)`` or create one."""
        existing = self.get(issuer, subject)
        if existing is not None:
            return existing

        created = Principal(
            id=_new_id(),
            issuer=issuer,
            subject=subject,
            status=PrincipalStatus.ACTIVE,
            created_at=_now(),
        )
        self._connection.execute(
            insert(principal).values(
                id=created.id,
                issuer=created.issuer,
                subject=created.subject,
                status=created.status.value,
                created_at=created.created_at,
            )
        )
        return created


class PostgresClientGrantRepository:
    """Connector OAuth client consent metadata backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def record_consent(self, principal_id: str, client_id: str, scope: str) -> None:
        """Record or refresh active consent for one principal/client pair."""
        existing = self._connection.execute(
            select(oauth_client_grant.c.id).where(
                oauth_client_grant.c.principal_id == principal_id,
                oauth_client_grant.c.client_id == client_id,
            )
        ).first()

        if existing is None:
            self._connection.execute(
                insert(oauth_client_grant).values(
                    id=_new_id(),
                    principal_id=principal_id,
                    client_id=client_id,
                    scope=scope,
                    status="active",
                    created_at=_now(),
                )
            )
            return

        self._connection.execute(
            update(oauth_client_grant)
            .where(
                oauth_client_grant.c.principal_id == principal_id,
                oauth_client_grant.c.client_id == client_id,
            )
            .values(scope=scope, status="active")
        )

    def has_active_grant(self, principal_id: str, client_id: str) -> bool:
        """Return whether this principal currently granted this connector client."""
        row = self._connection.execute(
            select(oauth_client_grant.c.id).where(
                oauth_client_grant.c.principal_id == principal_id,
                oauth_client_grant.c.client_id == client_id,
                oauth_client_grant.c.status == "active",
            )
        ).first()
        return row is not None


def _principal_from_row(row: RowMapping) -> Principal:
    return Principal(
        id=str(row["id"]),
        issuer=str(row["issuer"]),
        subject=str(row["subject"]),
        status=PrincipalStatus(str(row["status"])),
        created_at=_datetime(row["created_at"]),
    )


class PostgresAdsAccountRepository:
    """Google Ads account links backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def link_account(
        self, principal_id: str, customer_id: str, login_customer_id: str | None
    ) -> AdsAccount:
        """Link a customer ID to a principal, reactivating an existing row when needed."""
        existing = self.get_account(principal_id, customer_id)
        if existing is not None:
            if existing.status != "active" or existing.login_customer_id != login_customer_id:
                self._connection.execute(
                    update(ads_account)
                    .where(
                        ads_account.c.id == existing.id,
                        ads_account.c.principal_id == principal_id,
                    )
                    .values(status="active", login_customer_id=login_customer_id)
                )
                refreshed = self.get_account(principal_id, customer_id)
                assert refreshed is not None  # nosec B101
                return refreshed
            return existing

        account = AdsAccount(
            id=_new_id(),
            principal_id=principal_id,
            customer_id=customer_id,
            login_customer_id=login_customer_id,
            status="active",
            created_at=_now(),
        )
        self._connection.execute(
            insert(ads_account).values(
                id=account.id,
                principal_id=account.principal_id,
                customer_id=account.customer_id,
                login_customer_id=account.login_customer_id,
                status=account.status,
                created_at=account.created_at,
            )
        )
        return account

    def get_account(self, principal_id: str, customer_id: str) -> AdsAccount | None:
        """Return an account row regardless of active/disconnected status."""
        row = (
            self._connection.execute(
                select(ads_account).where(
                    ads_account.c.principal_id == principal_id,
                    ads_account.c.customer_id == customer_id,
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _account_from_row(row)

    def get_active_account(self, principal_id: str, customer_id: str) -> AdsAccount | None:
        """Return an active account only when it belongs to ``principal_id``."""
        row = (
            self._connection.execute(
                select(ads_account).where(
                    ads_account.c.principal_id == principal_id,
                    ads_account.c.customer_id == customer_id,
                    ads_account.c.status == "active",
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _account_from_row(row)

    def list_accounts(self, principal_id: str) -> list[AdsAccount]:
        """Return all account rows for one principal, including disconnected rows."""
        rows = (
            self._connection.execute(
                select(ads_account)
                .where(ads_account.c.principal_id == principal_id)
                .order_by(ads_account.c.created_at)
            )
            .mappings()
            .all()
        )
        return [_account_from_row(row) for row in rows]

    def list_active_accounts(self, principal_id: str) -> list[AdsAccount]:
        """Return active account rows for one principal."""
        rows = (
            self._connection.execute(
                select(ads_account)
                .where(
                    ads_account.c.principal_id == principal_id,
                    ads_account.c.status == "active",
                )
                .order_by(ads_account.c.created_at)
            )
            .mappings()
            .all()
        )
        return [_account_from_row(row) for row in rows]

    def disconnect_all(self, principal_id: str) -> None:
        """Mark every account linked to this principal as disconnected."""
        self._connection.execute(
            update(ads_account)
            .where(ads_account.c.principal_id == principal_id)
            .values(status="disconnected")
        )

    def synchronize_accounts(
        self,
        principal_id: str,
        discovered: Iterable[tuple[str, str | None]],
    ) -> list[AdsAccount]:
        """Replace one principal's active account snapshot in the caller's transaction."""
        normalized = dict(discovered)
        self.disconnect_all(principal_id)
        for customer_id, login_customer_id in sorted(normalized.items()):
            self.link_account(principal_id, customer_id, login_customer_id)
        return self.list_active_accounts(principal_id)


def _account_from_row(row: RowMapping) -> AdsAccount:
    return AdsAccount(
        id=str(row["id"]),
        principal_id=str(row["principal_id"]),
        customer_id=str(row["customer_id"]),
        login_customer_id=None
        if row["login_customer_id"] is None
        else str(row["login_customer_id"]),
        status=str(row["status"]),
        created_at=_datetime(row["created_at"]),
    )


class PostgresOAuthCredentialRepository:
    """Google OAuth credential metadata backed by SQLAlchemy Core.

    Only the vault reference is persisted here; raw refresh/access tokens never
    belong in the production relational schema.
    """

    def __init__(self, connection: Connection):
        self._connection = connection

    def upsert(self, principal_id: str, vault_ref: str, key_version: int) -> OAuthCredential:
        """Revoke any prior active credential and store the new vault reference."""
        self._connection.execute(
            update(oauth_credential)
            .where(
                oauth_credential.c.principal_id == principal_id,
                oauth_credential.c.status == CredentialStatus.ACTIVE.value,
            )
            .values(status=CredentialStatus.REVOKED.value)
        )
        credential = OAuthCredential(
            id=_new_id(),
            principal_id=principal_id,
            vault_ref=vault_ref,
            status=CredentialStatus.ACTIVE,
            key_version=key_version,
            created_at=_now(),
        )
        self._connection.execute(
            insert(oauth_credential).values(
                id=credential.id,
                principal_id=credential.principal_id,
                vault_ref=credential.vault_ref,
                status=credential.status.value,
                key_version=credential.key_version,
                created_at=credential.created_at,
            )
        )
        return credential

    def get_active(self, principal_id: str) -> OAuthCredential | None:
        """Return this principal's newest active credential metadata, if any."""
        row = (
            self._connection.execute(
                select(oauth_credential)
                .where(
                    oauth_credential.c.principal_id == principal_id,
                    oauth_credential.c.status == CredentialStatus.ACTIVE.value,
                )
                .order_by(oauth_credential.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return None if row is None else _credential_from_row(row)

    def revoke(self, principal_id: str, credential_id: str) -> None:
        """Revoke one credential only when it belongs to ``principal_id``."""
        self._connection.execute(
            update(oauth_credential)
            .where(
                oauth_credential.c.id == credential_id,
                oauth_credential.c.principal_id == principal_id,
            )
            .values(status=CredentialStatus.REVOKED.value)
        )

    def revoke_active(self, principal_id: str) -> OAuthCredential | None:
        """Revoke the active credential and return the pre-revoke metadata."""
        credential = self.get_active(principal_id)
        if credential is None:
            return None
        self.revoke(principal_id, credential.id)
        return credential


def _credential_from_row(row: RowMapping) -> OAuthCredential:
    return OAuthCredential(
        id=str(row["id"]),
        principal_id=str(row["principal_id"]),
        vault_ref=str(row["vault_ref"]),
        status=CredentialStatus(str(row["status"])),
        key_version=int(row["key_version"]),
        created_at=_datetime(row["created_at"]),
    )


class PostgresCredentialRevocationRepository:
    """Transactional outbox for eventually revoking Google secrets from the vault."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def revoke_and_enqueue(
        self, principal_id: str, credential_id: str, *, now: datetime | None = None
    ) -> CredentialRevocationJob | None:
        """Revoke owned credential metadata and idempotently enqueue its vault snapshot."""
        timestamp = _now() if now is None else now
        credential_row = (
            self._connection.execute(
                select(oauth_credential).where(
                    oauth_credential.c.id == credential_id,
                    oauth_credential.c.principal_id == principal_id,
                )
            )
            .mappings()
            .first()
        )
        if credential_row is None:
            return None

        self._connection.execute(
            update(oauth_credential)
            .where(
                oauth_credential.c.id == credential_id,
                oauth_credential.c.principal_id == principal_id,
            )
            .values(status=CredentialStatus.REVOKED.value)
        )
        values = {
            "id": _new_id(),
            "principal_id": principal_id,
            "credential_id": credential_id,
            "vault_ref": str(credential_row["vault_ref"]),
            "status": CredentialRevocationStatus.PENDING.value,
            "attempts": 0,
            "next_attempt_at": timestamp,
            "last_error_code": None,
            "created_at": timestamp,
            "completed_at": None,
        }
        if self._connection.dialect.name == "postgresql":
            statement = postgresql_insert(credential_revocation_job).values(**values)
            statement = statement.on_conflict_do_nothing(
                constraint="uq_credential_revocation_job_credential"
            )
        else:
            statement = sqlite_insert(credential_revocation_job).values(**values)
            statement = statement.on_conflict_do_nothing(index_elements=["credential_id"])
        self._connection.execute(statement)
        return self.get(principal_id, credential_id)

    def get(self, principal_id: str, credential_id: str) -> CredentialRevocationJob | None:
        """Return one job only within its principal ownership boundary."""
        row = (
            self._connection.execute(
                select(credential_revocation_job).where(
                    credential_revocation_job.c.principal_id == principal_id,
                    credential_revocation_job.c.credential_id == credential_id,
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _revocation_job_from_row(row)

    def claim_due(
        self, principal_id: str, *, now: datetime, lease_until: datetime
    ) -> CredentialRevocationJob | None:
        """Claim one due job with a database lock and durable retry lease."""
        if lease_until <= now:
            raise ValueError("lease_until must be later than now")
        candidate = (
            select(credential_revocation_job.c.id)
            .where(
                credential_revocation_job.c.principal_id == principal_id,
                credential_revocation_job.c.status == CredentialRevocationStatus.PENDING.value,
                credential_revocation_job.c.next_attempt_at <= now,
            )
            .order_by(credential_revocation_job.c.next_attempt_at, credential_revocation_job.c.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job_id = self._connection.execute(candidate).scalar_one_or_none()
        if job_id is None:
            return None
        row = (
            self._connection.execute(
                update(credential_revocation_job)
                .where(
                    credential_revocation_job.c.id == job_id,
                    credential_revocation_job.c.status == CredentialRevocationStatus.PENDING.value,
                    credential_revocation_job.c.next_attempt_at <= now,
                )
                .values(
                    attempts=credential_revocation_job.c.attempts + 1,
                    next_attempt_at=lease_until,
                )
                .returning(credential_revocation_job)
            )
            .mappings()
            .first()
        )
        return None if row is None else _revocation_job_from_row(row)

    def retry(
        self,
        principal_id: str,
        job_id: str,
        *,
        claimed_attempt: int,
        error_code: str,
        next_attempt_at: datetime,
    ) -> bool:
        """Persist a sanitized failure code and release the job for a later attempt."""
        if not error_code or len(error_code) > 64 or not error_code.replace("_", "").isalnum():
            raise ValueError("error_code must be a short identifier")
        result = self._connection.execute(
            update(credential_revocation_job)
            .where(
                credential_revocation_job.c.id == job_id,
                credential_revocation_job.c.principal_id == principal_id,
                credential_revocation_job.c.status == CredentialRevocationStatus.PENDING.value,
                credential_revocation_job.c.attempts == claimed_attempt,
            )
            .values(last_error_code=error_code, next_attempt_at=next_attempt_at)
        )
        return result.rowcount == 1

    def complete(
        self,
        principal_id: str,
        job_id: str,
        *,
        claimed_attempt: int,
        completed_at: datetime,
    ) -> bool:
        """Idempotently mark a successfully revoked vault reference complete."""
        result = self._connection.execute(
            update(credential_revocation_job)
            .where(
                credential_revocation_job.c.id == job_id,
                credential_revocation_job.c.principal_id == principal_id,
                credential_revocation_job.c.status == CredentialRevocationStatus.PENDING.value,
                credential_revocation_job.c.attempts == claimed_attempt,
            )
            .values(
                status=CredentialRevocationStatus.COMPLETED.value,
                completed_at=completed_at,
                last_error_code=None,
            )
        )
        return result.rowcount == 1


def _revocation_job_from_row(row: RowMapping) -> CredentialRevocationJob:
    return CredentialRevocationJob(
        id=str(row["id"]),
        principal_id=str(row["principal_id"]),
        credential_id=str(row["credential_id"]),
        vault_ref=str(row["vault_ref"]),
        status=CredentialRevocationStatus(str(row["status"])),
        attempts=int(row["attempts"]),
        next_attempt_at=_datetime(row["next_attempt_at"]),
        last_error_code=None if row["last_error_code"] is None else str(row["last_error_code"]),
        created_at=_datetime(row["created_at"]),
        completed_at=None if row["completed_at"] is None else _datetime(row["completed_at"]),
    )


class PostgresProposalRepository:
    """Proposal snapshots backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def save(self, stored_proposal: Proposal) -> None:
        """Insert a proposal or advance status without changing immutable content."""
        row = self._connection.execute(
            select(proposal).where(proposal.c.id == stored_proposal.proposal_id)
        ).first()
        if row is None:
            self._connection.execute(
                insert(proposal).values(
                    id=stored_proposal.proposal_id,
                    principal_id=stored_proposal.principal_id,
                    customer_id=stored_proposal.customer_id,
                    payload=_payload_for_bind(self._connection, stored_proposal.payload),
                    proposal_hash=stored_proposal.proposal_hash,
                    status=stored_proposal.status.value,
                    expires_at=stored_proposal.expires_at,
                    created_at=_now(),
                )
            )
            return

        cursor = self._connection.execute(
            update(proposal)
            .where(
                proposal.c.id == stored_proposal.proposal_id,
                proposal.c.principal_id == stored_proposal.principal_id,
                proposal.c.customer_id == stored_proposal.customer_id,
                proposal.c.proposal_hash == stored_proposal.proposal_hash,
                proposal.c.expires_at == stored_proposal.expires_at,
            )
            .values(status=stored_proposal.status.value)
        )
        if cursor.rowcount != 1:
            raise ValueError(
                "proposal_id farkli bir principal/customer kapsaminda veya degisen "
                "payload/hash ile kullanilmis"
            )

    def get(self, principal_id: str, proposal_id: str) -> Proposal | None:
        """Return the proposal only when it belongs to ``principal_id``."""
        row = (
            self._connection.execute(
                select(proposal).where(
                    proposal.c.id == proposal_id,
                    proposal.c.principal_id == principal_id,
                )
            )
            .mappings()
            .first()
        )
        return None if row is None else _proposal_from_row(row)

    def list_pending(
        self,
        principal_id: str,
        *,
        customer_id: str | None = None,
        limit: int = 50,
        after_created_at: str | None = None,
        after_id: str | None = None,
        now: datetime | None = None,
    ) -> ProposalPage:
        """Return this principal's unexpired proposals awaiting a human decision."""
        if limit < 1:
            raise ValueError("limit pozitif olmalidir")
        if limit > MAX_PENDING_PROPOSAL_LIMIT:
            raise ValueError(f"limit en fazla {MAX_PENDING_PROPOSAL_LIMIT} olabilir")
        if (after_created_at is None) != (after_id is None):
            raise ValueError("after_created_at ve after_id birlikte verilmelidir")
        cutoff_time = now or datetime.now(UTC)
        if cutoff_time.tzinfo is None or cutoff_time.utcoffset() is None:
            raise ValueError("now timezone bilgisi icermelidir")

        filters = [
            proposal.c.principal_id == principal_id,
            proposal.c.status == ProposalStatus.PENDING_APPROVAL.value,
            proposal.c.expires_at > cutoff_time.astimezone(UTC),
        ]
        if customer_id is not None:
            filters.append(proposal.c.customer_id == customer_id)
        if after_created_at is not None and after_id is not None:
            after_created = _datetime(after_created_at)
            filters.append(
                or_(
                    proposal.c.created_at > after_created,
                    and_(proposal.c.created_at == after_created, proposal.c.id > after_id),
                )
            )

        rows = (
            self._connection.execute(
                select(proposal).where(*filters).order_by(proposal.c.created_at, proposal.c.id)
            )
            .mappings()
            .fetchmany(limit + 1)
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        last_row = page_rows[-1] if has_more and page_rows else None
        return ProposalPage(
            proposals=[_proposal_from_row(row) for row in page_rows],
            has_more=has_more,
            last_created_at=last_row["created_at"].isoformat() if last_row is not None else None,
            last_id=str(last_row["id"]) if last_row is not None else None,
        )


def _proposal_from_row(row: RowMapping) -> Proposal:
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return Proposal(
        proposal_id=str(row["id"]),
        principal_id=str(row["principal_id"]),
        customer_id=str(row["customer_id"]),
        payload=MappingProxyType(dict(payload)),
        proposal_hash=str(row["proposal_hash"]),
        expires_at=_datetime(row["expires_at"]),
        status=ProposalStatus(str(row["status"])),
    )


class PostgresApprovalRepository:
    """Human approval decisions backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def save(self, stored_approval: Approval) -> None:
        """Store a decision only when proposal and principal ownership match."""
        owner = self._connection.execute(
            select(proposal.c.id).where(
                proposal.c.id == stored_approval.proposal_id,
                proposal.c.principal_id == stored_approval.principal_id,
            )
        ).first()
        if owner is None:
            raise ValueError("approval proposal ve principal kapsami uyusmuyor")
        self._insert_approval(_new_id(), stored_approval)

    def save_decision_with_audit(
        self,
        decided_proposal: Proposal,
        stored_approval: Approval,
        event: AuditEvent,
    ) -> str:
        """Atomically store a human decision and its append-only audit event."""
        if (
            stored_approval.proposal_id != decided_proposal.proposal_id
            or stored_approval.principal_id != decided_proposal.principal_id
        ):
            raise ValueError("approval proposal ve principal kapsami uyusmuyor")
        if stored_approval.proposal_hash != decided_proposal.proposal_hash:
            raise ValueError("approval proposal hash'i guncel proposal ile uyusmuyor")
        if (
            event.proposal_id != decided_proposal.proposal_id
            or event.principal_id != decided_proposal.principal_id
        ):
            raise ValueError("audit event proposal ve principal kapsami uyusmuyor")
        if event.customer_id != decided_proposal.customer_id:
            raise ValueError("audit event customer kapsami uyusmuyor")
        if event.actor != stored_approval.approver_id:
            raise ValueError("audit event actor onaylayan ile uyusmuyor")
        if (
            event.event_type != "approval.decided"
            or event.outcome != stored_approval.decision.value
        ):
            raise ValueError("audit event approval karari ile uyusmuyor")

        cursor = self._connection.execute(
            update(proposal)
            .where(
                proposal.c.id == decided_proposal.proposal_id,
                proposal.c.principal_id == decided_proposal.principal_id,
                proposal.c.customer_id == decided_proposal.customer_id,
                proposal.c.proposal_hash == decided_proposal.proposal_hash,
                proposal.c.status == ProposalStatus.PENDING_APPROVAL.value,
            )
            .values(status=decided_proposal.status.value)
        )
        if cursor.rowcount != 1:
            raise ValueError("proposal decision principal/customer/hash kapsaminda bulunamadi")

        approval_id = _new_id()
        self._insert_approval(approval_id, stored_approval)
        _insert_audit_event(self._connection, replace(event, approval_id=approval_id))
        return approval_id

    def get_latest(self, principal_id: str, proposal_id: str) -> Approval | None:
        """Return the most recent decision only if it belongs to ``principal_id``."""
        row = (
            self._connection.execute(
                select(approval)
                .where(
                    approval.c.proposal_id == proposal_id,
                    approval.c.principal_id == principal_id,
                )
                .order_by(approval.c.decided_at.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return None if row is None else _approval_from_row(row)

    def _insert_approval(self, approval_id: str, stored_approval: Approval) -> None:
        self._connection.execute(
            insert(approval).values(
                id=approval_id,
                proposal_id=stored_approval.proposal_id,
                principal_id=stored_approval.principal_id,
                approver_id=stored_approval.approver_id,
                decision=stored_approval.decision.value,
                proposal_hash=stored_approval.proposal_hash,
                decided_at=stored_approval.decided_at,
            )
        )


def _approval_from_row(row: RowMapping) -> Approval:
    return Approval(
        proposal_id=str(row["proposal_id"]),
        principal_id=str(row["principal_id"]),
        approver_id=str(row["approver_id"]),
        decision=Decision(str(row["decision"])),
        proposal_hash=str(row["proposal_hash"]),
        decided_at=_datetime(row["decided_at"]),
    )


class PostgresExecutionRepository:
    """Execution reservations and provider outcomes backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def record(
        self,
        reservation: ExecutionReservation,
        before: str,
        after: str,
    ) -> ExecutionClaim:
        """Claim an idempotency key; reject reuse for a different scoped operation."""
        owner = (
            self._connection.execute(
                select(proposal.c.customer_id, proposal.c.proposal_hash).where(
                    proposal.c.id == reservation.proposal_id,
                    proposal.c.principal_id == reservation.principal_id,
                )
            )
            .mappings()
            .first()
        )
        if owner is None:
            raise ValueError("execution proposal ve principal kapsami uyusmuyor")
        if (
            owner["customer_id"] != reservation.customer_id
            or owner["proposal_hash"] != reservation.proposal_hash
        ):
            raise ValueError("execution reservation proposal snapshot'i ile uyusmuyor")

        execution_id = _new_id()
        values = {
            "id": execution_id,
            "proposal_id": reservation.proposal_id,
            "principal_id": reservation.principal_id,
            "idempotency_key": reservation.idempotency_key,
            "before": _json_for_bind(self._connection, before),
            "after": _json_for_bind(self._connection, after),
            "google_request_id": None,
            "status": ExecutionStatus.PENDING.value,
            "created_at": _now(),
        }
        cursor = self._connection.execute(_execution_insert_do_nothing(self._connection, values))
        if cursor.rowcount == 0:
            concurrent = (
                self._connection.execute(
                    select(execution).where(
                        execution.c.principal_id == reservation.principal_id,
                        execution.c.proposal_id == reservation.proposal_id,
                        execution.c.idempotency_key == reservation.idempotency_key,
                    )
                )
                .mappings()
                .one()
            )
            return _execution_claim_from_existing(concurrent, reservation, before, after)
        return ExecutionClaim(
            execution_id=execution_id,
            created=True,
            status=ExecutionStatus.PENDING,
            google_request_id=None,
        )

    def mark_result(
        self,
        principal_id: str,
        execution_id: str,
        status: ExecutionStatus,
        google_request_id: str | None,
    ) -> None:
        """Record the provider outcome only for the owning principal."""
        cursor = self._connection.execute(
            update(execution)
            .where(
                execution.c.id == execution_id,
                execution.c.principal_id == principal_id,
            )
            .values(status=status.value, google_request_id=google_request_id)
        )
        if cursor.rowcount != 1:
            raise ValueError("execution sonucu principal kapsaminda bulunamadi")


def _execution_insert_do_nothing(connection: Connection, values: dict[str, Any]):
    """Insert an execution claim without surfacing a concurrent idempotency conflict."""
    if connection.dialect.name == "postgresql":
        return (
            postgresql_insert(execution)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_execution_principal_proposal_idempotency")
        )
    if connection.dialect.name == "sqlite":
        return (
            sqlite_insert(execution)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=("principal_id", "proposal_id", "idempotency_key")
            )
        )
    return insert(execution).values(**values)


def _json_for_bind(connection: Connection, value: str) -> Any:
    parsed = json.loads(value)
    if connection.dialect.name == "postgresql":
        return parsed
    return json.dumps(parsed, allow_nan=False)


def _json_from_row_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(json.loads(value), allow_nan=False, sort_keys=True)
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _canonical_json(value: str) -> str:
    return json.dumps(json.loads(value), allow_nan=False, sort_keys=True)


def _execution_claim_from_existing(
    row: RowMapping,
    reservation: ExecutionReservation,
    before: str,
    after: str,
) -> ExecutionClaim:
    expected = (
        reservation.proposal_id,
        reservation.principal_id,
        _canonical_json(before),
        _canonical_json(after),
    )
    actual = (
        str(row["proposal_id"]),
        str(row["principal_id"]),
        _json_from_row_value(row["before"]),
        _json_from_row_value(row["after"]),
    )
    if actual != expected:
        raise ValueError("idempotency_key farkli bir execution icin kullanilmis")
    return ExecutionClaim(
        execution_id=str(row["id"]),
        created=False,
        status=ExecutionStatus(str(row["status"])),
        google_request_id=None
        if row["google_request_id"] is None
        else str(row["google_request_id"]),
    )


class PostgresAuditRepository:
    """Append-only audit event store backed by SQLAlchemy Core."""

    def __init__(self, connection: Connection):
        self._connection = connection

    def insert(self, event: AuditEvent) -> None:
        """Append one immutable audit event."""
        _insert_audit_event(self._connection, event)

    def list_for_principal(self, principal_id: str) -> list[AuditEvent]:
        """Return audit events scoped to one principal."""
        rows = (
            self._connection.execute(
                select(audit_event)
                .where(audit_event.c.principal_id == principal_id)
                .order_by(audit_event.c.occurred_at)
            )
            .mappings()
            .all()
        )
        return [_audit_from_row(row) for row in rows]


def _insert_audit_event(connection: Connection, event: AuditEvent) -> None:
    connection.execute(
        insert(audit_event).values(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            actor=event.actor,
            principal_id=event.principal_id,
            customer_id=event.customer_id,
            event_type=event.event_type,
            proposal_id=event.proposal_id,
            approval_id=event.approval_id,
            execution_id=event.execution_id,
            outcome=event.outcome,
            reason_code=event.reason_code,
            correlation_id=event.correlation_id,
            google_request_id=event.google_request_id,
        )
    )


def _audit_from_row(row: RowMapping) -> AuditEvent:
    return AuditEvent(
        event_id=str(row["event_id"]),
        occurred_at=_datetime(row["occurred_at"]),
        actor=str(row["actor"]),
        principal_id=None if row["principal_id"] is None else str(row["principal_id"]),
        customer_id=None if row["customer_id"] is None else str(row["customer_id"]),
        event_type=str(row["event_type"]),
        proposal_id=None if row["proposal_id"] is None else str(row["proposal_id"]),
        approval_id=None if row["approval_id"] is None else str(row["approval_id"]),
        execution_id=None if row["execution_id"] is None else str(row["execution_id"]),
        outcome=str(row["outcome"]),
        reason_code=None if row["reason_code"] is None else str(row["reason_code"]),
        correlation_id=str(row["correlation_id"]),
        google_request_id=None
        if row["google_request_id"] is None
        else str(row["google_request_id"]),
    )
