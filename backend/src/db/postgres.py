"""Production PostgreSQL engine and principal-scoped transaction helpers.

The sqlite3 repositories remain the fast local prototype path. This module is
the production SQLAlchemy entry point for the PostgreSQL schema managed by
Alembic: it validates that callers use a PostgreSQL URL and sets the
transaction-local principal context required by the RLS policies before any
repository work runs on the connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import ArgumentError

from ..auth.domain import AuthError
from .postgres_context import (
    bootstrap_authorization_code_principal,
    clear_transaction_principal,
    set_transaction_principal,
)

POSTGRES_DRIVER_PREFIX = "postgresql"
DEFAULT_POSTGRES_DRIVER = "postgresql+psycopg"


def create_postgres_engine(database_url: str) -> Engine:
    """Create the production SQLAlchemy engine for a PostgreSQL DSN.

    ``database_url`` may contain credentials, so callers must not include it in
    exception/log messages. Only PostgreSQL dialect URLs are accepted; using the
    sqlite prototype database here would silently bypass RLS.
    """
    if not database_url:
        raise ValueError("DATABASE_URL is required for the production PostgreSQL engine")

    try:
        url = make_url(database_url)
    except ArgumentError as exc:
        raise ValueError("DATABASE_URL is not a valid SQLAlchemy URL") from exc
    if url.get_backend_name() != POSTGRES_DRIVER_PREFIX:
        raise ValueError("DATABASE_URL must use the postgresql dialect for production RLS")
    if url.drivername == POSTGRES_DRIVER_PREFIX:
        url = url.set(drivername=DEFAULT_POSTGRES_DRIVER)

    return create_engine(url, pool_pre_ping=True)


@contextmanager
def principal_transaction(engine: Engine, principal_id: str) -> Iterator[Connection]:
    """Open one transaction with the RLS principal context set.

    The helper validates/sets the principal before yielding the connection and
    clears the transaction-local setting before commit. On exceptions, rollback
    clears PostgreSQL's transaction-local setting as part of aborting the
    transaction, so pooled connections cannot inherit another request's
    principal.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            set_transaction_principal(connection, principal_id)
            yield connection
            clear_transaction_principal(connection)
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise


@contextmanager
def authorization_code_transaction(engine: Engine, raw_code: str) -> Iterator[Connection]:
    """Open an RLS transaction scoped from one exact authorization code hash.

    Unknown codes fail closed. After bootstrap, normal principal RLS protects
    the atomic claim and every subsequent write in the transaction.
    """
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            principal_id = bootstrap_authorization_code_principal(connection, raw_code)
            if principal_id is None:
                raise AuthError("invalid_grant", "Yetkilendirme kodu bulunamadi.")
            yield connection
            clear_transaction_principal(connection)
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise
