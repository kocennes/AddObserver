"""Request-scoped PostgreSQL unit of work for production composition.

Every repository in one request shares one SQLAlchemy connection and one DB
transaction. Principal RLS context may be bound after an upstream identity is
verified, or bootstrapped from an exact authorization-code hash for ``/token``.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType

from sqlalchemy import Engine
from sqlalchemy.engine import Connection

from .postgres_context import (
    bootstrap_access_token_principal,
    bootstrap_authorization_code_principal,
    bootstrap_refresh_token_principal,
    bootstrap_web_session_principal,
    clear_transaction_principal,
    set_transaction_principal,
)
from .postgres_repository import (
    PostgresAdsAccountRepository,
    PostgresApprovalRepository,
    PostgresAuditRepository,
    PostgresAuthorizationCodeRepository,
    PostgresAuthorizationTransactionRepository,
    PostgresClientGrantRepository,
    PostgresCredentialRevocationRepository,
    PostgresExecutionRepository,
    PostgresOAuthCredentialRepository,
    PostgresPrincipalRepository,
    PostgresProposalRepository,
    PostgresTokenRepository,
    PostgresWebLoginStateRepository,
    PostgresWebSessionRepository,
)


@dataclass(frozen=True, slots=True)
class PostgresRepositories:
    """Repository adapters sharing one request transaction connection."""

    principals: PostgresPrincipalRepository
    client_grants: PostgresClientGrantRepository
    accounts: PostgresAdsAccountRepository
    credentials: PostgresOAuthCredentialRepository
    credential_revocations: PostgresCredentialRevocationRepository
    authorization_transactions: PostgresAuthorizationTransactionRepository
    authorization_codes: PostgresAuthorizationCodeRepository
    tokens: PostgresTokenRepository
    web_login_states: PostgresWebLoginStateRepository
    web_sessions: PostgresWebSessionRepository
    proposals: PostgresProposalRepository
    approvals: PostgresApprovalRepository
    executions: PostgresExecutionRepository
    audit: PostgresAuditRepository

    @classmethod
    def for_connection(cls, connection: Connection) -> PostgresRepositories:
        """Build every adapter against the same SQLAlchemy connection."""
        return cls(
            principals=PostgresPrincipalRepository(connection),
            client_grants=PostgresClientGrantRepository(connection),
            accounts=PostgresAdsAccountRepository(connection),
            credentials=PostgresOAuthCredentialRepository(connection),
            credential_revocations=PostgresCredentialRevocationRepository(connection),
            authorization_transactions=PostgresAuthorizationTransactionRepository(connection),
            authorization_codes=PostgresAuthorizationCodeRepository(connection),
            tokens=PostgresTokenRepository(connection),
            web_login_states=PostgresWebLoginStateRepository(connection),
            web_sessions=PostgresWebSessionRepository(connection),
            proposals=PostgresProposalRepository(connection),
            approvals=PostgresApprovalRepository(connection),
            executions=PostgresExecutionRepository(connection),
            audit=PostgresAuditRepository(connection),
        )


class PostgresRequestUnitOfWork(AbstractContextManager["PostgresRequestUnitOfWork"]):
    """Own one connection, transaction, RLS context and repository set."""

    def __init__(self, engine: Engine):
        self._engine = engine
        self._connection_context = None
        self._connection: Connection | None = None
        self._transaction = None
        self._principal_bound = False
        self.repositories: PostgresRepositories | None = None

    def __enter__(self) -> PostgresRequestUnitOfWork:
        self._connection_context = self._engine.connect()
        self._connection = self._connection_context.__enter__()
        self._transaction = self._connection.begin()
        self.repositories = PostgresRepositories.for_connection(self._connection)
        return self

    def bind_principal(self, principal_id: str) -> None:
        """Install the verified principal as transaction-local RLS context."""
        connection = self._require_connection()
        set_transaction_principal(connection, principal_id)
        self._principal_bound = True

    def bootstrap_authorization_code(self, raw_code: str) -> str | None:
        """Resolve an exact code hash and bind its owning principal under RLS."""
        principal_id = bootstrap_authorization_code_principal(self._require_connection(), raw_code)
        self._principal_bound = principal_id is not None
        return principal_id

    def bootstrap_access_token(self, raw_token: str) -> str | None:
        """Resolve an exact access-token hash and bind its owning principal."""
        principal_id = bootstrap_access_token_principal(self._require_connection(), raw_token)
        self._principal_bound = principal_id is not None
        return principal_id

    def bootstrap_refresh_token(self, raw_token: str) -> str | None:
        """Resolve an exact refresh-token hash and bind its owning principal."""
        principal_id = bootstrap_refresh_token_principal(self._require_connection(), raw_token)
        self._principal_bound = principal_id is not None
        return principal_id

    def bootstrap_web_session(self, raw_token: str) -> str | None:
        """Resolve an exact browser-session hash and bind its owning principal."""
        principal_id = bootstrap_web_session_principal(self._require_connection(), raw_token)
        self._principal_bound = principal_id is not None
        return principal_id

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._transaction is None or self._connection_context is None:
            raise RuntimeError("PostgresRequestUnitOfWork exited without being entered")
        try:
            if exc_type is None:
                if self._principal_bound:
                    clear_transaction_principal(self._require_connection())
                self._transaction.commit()
            else:
                self._transaction.rollback()
        finally:
            self.repositories = None
            self._connection = None
            self._transaction = None
            self._principal_bound = False
            self._connection_context.__exit__(exc_type, exc_value, traceback)
            self._connection_context = None
        return None

    def _require_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("PostgresRequestUnitOfWork must be entered before use")
        return self._connection


@dataclass(frozen=True, slots=True)
class PostgresUnitOfWorkFactory:
    """Create a fresh request-scoped unit of work from the shared engine."""

    engine: Engine

    def request(self) -> PostgresRequestUnitOfWork:
        """Return a new, not-yet-entered request unit of work."""
        return PostgresRequestUnitOfWork(self.engine)
