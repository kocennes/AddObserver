"""Optional live PostgreSQL tests for principal-scoped RLS isolation.

These tests are skipped unless ``ADDOBSERVER_POSTGRES_TEST_DSN`` points to a
disposable PostgreSQL database. The suite creates and drops its own schema, but
the database itself must be safe for destructive test setup.
"""

from __future__ import annotations

import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from sqlalchemy import create_engine, delete, func, insert, select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import (  # noqa: E402
    Decision,
    ExecutionReservation,
    Proposal,
    approve_proposal,
    submit_proposal,
)
from backend.src.auth.domain import (  # noqa: E402
    AuthError,
    RefreshToken,
    consent_transaction,
    hash_token,
)
from backend.src.db.models import AuditEvent  # noqa: E402
from backend.src.db.postgres_context import (  # noqa: E402
    PRINCIPAL_CONTEXT_SETTING,
    bootstrap_access_token_principal,
    bootstrap_authorization_code_principal,
    bootstrap_refresh_token_principal,
    bootstrap_web_session_principal,
    set_transaction_principal,
)
from backend.src.db.postgres_repository import (  # noqa: E402
    PostgresApprovalRepository,
    PostgresAuthorizationCodeRepository,
    PostgresAuthorizationTransactionRepository,
    PostgresCredentialRevocationRepository,
    PostgresExecutionRepository,
    PostgresProposalRepository,
    PostgresTokenRepository,
)
from backend.src.db.sqlalchemy_schema import RLS_TABLES, metadata  # noqa: E402

POSTGRES_TEST_DSN_ENV = "ADDOBSERVER_POSTGRES_TEST_DSN"
PRINCIPAL_POLICY = (
    "principal_id = nullif(current_setting('app.current_principal_id', true), '')::uuid"
)


def _quote_identifier(identifier: str) -> str:
    """Quote a generated PostgreSQL identifier."""
    return '"' + identifier.replace('"', '""') + '"'


class PostgreSQLRLSIntegrationTests(unittest.TestCase):
    """Run live RLS isolation tests against an explicit disposable PostgreSQL DSN."""

    engine: Engine
    schema_name: str

    @classmethod
    def setUpClass(cls) -> None:
        dsn = os.environ.get(POSTGRES_TEST_DSN_ENV)
        if not dsn:
            raise unittest.SkipTest(f"{POSTGRES_TEST_DSN_ENV} is not set")

        cls.schema_name = f"addobserver_rls_test_{uuid4().hex}"
        cls.engine = create_engine(dsn, pool_size=4, max_overflow=0)

        with cls.engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE SCHEMA {_quote_identifier(cls.schema_name)}")
            connection.exec_driver_sql(
                f"SET search_path TO {_quote_identifier(cls.schema_name)}, public"
            )
            metadata.create_all(connection)
            for table_name in RLS_TABLES:
                policy_name = f"{table_name}_principal_isolation"
                connection.exec_driver_sql(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
                connection.exec_driver_sql(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
                connection.exec_driver_sql(
                    f"CREATE POLICY {policy_name} ON {table_name} "
                    f"USING ({PRINCIPAL_POLICY}) WITH CHECK ({PRINCIPAL_POLICY})"
                )
            connection.exec_driver_sql(
                "CREATE POLICY authorization_code_exact_hash_bootstrap "
                "ON authorization_code FOR SELECT USING ("
                "code_hash = nullif(current_setting("
                "'app.current_authorization_code_hash', true), ''))"
            )
            for table_name in ("access_token", "refresh_token"):
                connection.exec_driver_sql(
                    f"CREATE POLICY {table_name}_exact_hash_bootstrap ON {table_name} "
                    "FOR SELECT USING (token_hash = nullif(current_setting("
                    "'app.current_token_hash', true), ''))"
                )
            connection.exec_driver_sql(
                "CREATE POLICY web_session_exact_hash_bootstrap ON web_session "
                "FOR SELECT USING (token_hash = nullif(current_setting("
                "'app.current_web_session_hash', true), ''))"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        engine = getattr(cls, "engine", None)
        schema_name = getattr(cls, "schema_name", None)
        if engine is None or schema_name is None:
            return

        with engine.begin() as connection:
            connection.exec_driver_sql(
                f"DROP SCHEMA IF EXISTS {_quote_identifier(schema_name)} CASCADE"
            )
        engine.dispose()

    def test_test_role_is_not_privileged_to_bypass_rls(self) -> None:
        with self.engine.connect() as connection:
            role = (
                connection.execute(
                    text("SELECT usesuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
                .mappings()
                .one()
            )

        self.assertFalse(role["usesuper"])
        self.assertFalse(role["rolbypassrls"])

    def test_cross_principal_crud_isolation(self) -> None:
        principal = metadata.tables["principal"]
        ads_account = metadata.tables["ads_account"]
        p1 = str(uuid4())
        p2 = str(uuid4())
        p1_account = str(uuid4())
        p2_account = str(uuid4())
        now = datetime.now(UTC)

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(principal),
                    [
                        {
                            "id": p1,
                            "issuer": "test",
                            "subject": f"{p1}:subject",
                            "status": "active",
                            "created_at": now,
                        },
                        {
                            "id": p2,
                            "issuer": "test",
                            "subject": f"{p2}:subject",
                            "status": "active",
                            "created_at": now,
                        },
                    ],
                )
                set_transaction_principal(connection, p1)
                connection.execute(
                    insert(ads_account).values(
                        id=p1_account,
                        principal_id=p1,
                        customer_id="1111111111",
                        login_customer_id=None,
                        status="active",
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, p2)
                connection.execute(
                    insert(ads_account).values(
                        id=p2_account,
                        principal_id=p2,
                        customer_id="2222222222",
                        login_customer_id=None,
                        status="active",
                        created_at=now,
                    )
                )

            with connection.begin():
                set_transaction_principal(connection, p1)
                visible_customers = list(
                    connection.execute(select(ads_account.c.customer_id)).scalars()
                )
                hidden_update = connection.execute(
                    update(ads_account)
                    .where(ads_account.c.id == p2_account)
                    .values(status="disconnected")
                )
                hidden_delete = connection.execute(
                    delete(ads_account).where(ads_account.c.id == p2_account)
                )

            self.assertEqual(visible_customers, ["1111111111"])
            self.assertEqual(hidden_update.rowcount, 0)
            self.assertEqual(hidden_delete.rowcount, 0)

            with self.assertRaises(DBAPIError), connection.begin():
                set_transaction_principal(connection, p1)
                connection.execute(
                    insert(ads_account).values(
                        id=str(uuid4()),
                        principal_id=p2,
                        customer_id="3333333333",
                        login_customer_id=None,
                        status="active",
                        created_at=now,
                    )
                )

            with self.assertRaises(DBAPIError), connection.begin():
                set_transaction_principal(connection, p1)
                connection.execute(
                    update(ads_account)
                    .where(ads_account.c.id == p1_account)
                    .values(principal_id=p2)
                )

            with connection.begin():
                set_transaction_principal(connection, p1)
                own_delete = connection.execute(
                    delete(ads_account).where(ads_account.c.id == p1_account)
                )

            self.assertEqual(own_delete.rowcount, 1)

    def test_transaction_local_principal_does_not_survive_pool_reuse(self) -> None:
        p1 = str(uuid4())

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                set_transaction_principal(connection, p1)
                self.assertEqual(self._current_principal(connection), p1)

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            self.assertNotEqual(self._current_principal(connection), p1)

    def test_authorization_code_bootstrap_reveals_only_exact_hash_then_sets_principal(self) -> None:
        from backend.src.auth.domain import hash_token

        principal = metadata.tables["principal"]
        transaction = metadata.tables["authorization_transaction"]
        code = metadata.tables["authorization_code"]
        owner = str(uuid4())
        other = str(uuid4())
        now = datetime.now(UTC)

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(principal),
                    [
                        {
                            "id": owner,
                            "issuer": "test",
                            "subject": owner,
                            "status": "active",
                            "created_at": now,
                        },
                        {
                            "id": other,
                            "issuer": "test",
                            "subject": other,
                            "status": "active",
                            "created_at": now,
                        },
                    ],
                )
                connection.execute(
                    insert(transaction).values(
                        id="bootstrap-transaction",
                        client_id="client",
                        redirect_uri="https://client.example/callback",
                        code_challenge="a" * 43,
                        code_challenge_method="S256",
                        resource="https://connector.example/mcp",
                        scope="read",
                        client_state="state",
                        consent_csrf_hash="csrf",
                        status="completed",
                        expires_at=now,
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                connection.execute(
                    insert(code).values(
                        code_hash=hash_token("owner-code"),
                        transaction_id="bootstrap-transaction",
                        principal_id=owner,
                        client_id="client",
                        redirect_uri="https://client.example/callback",
                        code_challenge="a" * 43,
                        code_challenge_method="S256",
                        resource="https://connector.example/mcp",
                        scope="read",
                        expires_at=now,
                        consumed_at=None,
                        created_at=now,
                    )
                )

            with connection.begin():
                resolved = bootstrap_authorization_code_principal(connection, "owner-code")
                visible = list(connection.execute(select(code.c.principal_id)).scalars())

            self.assertEqual(resolved, owner)
            self.assertEqual(visible, [owner])

            with connection.begin():
                self.assertIsNone(
                    bootstrap_authorization_code_principal(connection, "unknown-code")
                )

    def test_access_and_refresh_token_bootstrap_resolve_only_exact_hash(self) -> None:
        from backend.src.auth.domain import hash_token

        principal = metadata.tables["principal"]
        access = metadata.tables["access_token"]
        refresh = metadata.tables["refresh_token"]
        owner = str(uuid4())
        now = datetime.now(UTC)

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(principal).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                connection.execute(
                    insert(access).values(
                        token_hash=hash_token("access-raw"),
                        principal_id=owner,
                        client_id="client",
                        resource="https://connector.example/mcp",
                        scope="read",
                        expires_at=now,
                        revoked_at=None,
                        created_at=now,
                    )
                )
                connection.execute(
                    insert(refresh).values(
                        token_hash=hash_token("refresh-raw"),
                        family_id="family",
                        principal_id=owner,
                        client_id="client",
                        resource="https://connector.example/mcp",
                        scope="read",
                        status="active",
                        expires_at=now,
                        created_at=now,
                        rotated_at=None,
                    )
                )

            for raw_token, bootstrap in (
                ("access-raw", bootstrap_access_token_principal),
                ("refresh-raw", bootstrap_refresh_token_principal),
            ):
                with connection.begin():
                    self.assertEqual(bootstrap(connection, raw_token), owner)

            with connection.begin():
                self.assertIsNone(bootstrap_access_token_principal(connection, "unknown-access"))

    def test_web_session_bootstrap_resolves_only_exact_hash(self) -> None:
        from backend.src.auth.domain import hash_token

        principal = metadata.tables["principal"]
        session = metadata.tables["web_session"]
        owner = str(uuid4())
        now = datetime.now(UTC)

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(principal).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                connection.execute(
                    insert(session).values(
                        token_hash=hash_token("session-raw"),
                        principal_id=owner,
                        csrf_token_hash=hash_token("csrf-raw"),
                        expires_at=now,
                        revoked_at=None,
                        created_at=now,
                    )
                )

            with connection.begin():
                self.assertEqual(bootstrap_web_session_principal(connection, "session-raw"), owner)
                visible = list(connection.execute(select(session.c.principal_id)).scalars())
                self.assertEqual(visible, [owner])

            with connection.begin():
                self.assertIsNone(bootstrap_web_session_principal(connection, "unknown-session"))

    def test_concurrent_execution_reservation_reuses_exactly_one_row(self) -> None:
        owner = str(uuid4())
        proposal = Proposal.create(
            proposal_id=str(uuid4()),
            principal_id=owner,
            customer_id="1234567890",
            payload={"type": "campaign_pause", "campaign_id": "1"},
            expires_at=datetime.now(UTC),
        )
        reservation = ExecutionReservation(
            proposal_id=proposal.proposal_id,
            principal_id=owner,
            customer_id=proposal.customer_id,
            proposal_hash=proposal.proposal_hash,
            idempotency_key="concurrent-execution",
            reserved_at=datetime.now(UTC),
        )
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(metadata.tables["principal"]).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=datetime.now(UTC),
                    )
                )
                set_transaction_principal(connection, owner)
                PostgresProposalRepository(connection).save(proposal)

        barrier = Barrier(2)

        def reserve(_index: int):  # noqa: ANN202
            with self.engine.connect() as connection:
                self._set_search_path(connection)
                with connection.begin():
                    set_transaction_principal(connection, owner)
                    barrier.wait(timeout=10)
                    return PostgresExecutionRepository(connection).record(
                        reservation, before="{}", after="{}"
                    )

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(reserve, range(2)))

        self.assertEqual(sum(claim.created for claim in claims), 1)
        self.assertEqual(len({claim.execution_id for claim in claims}), 1)
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                set_transaction_principal(connection, owner)
                count = connection.execute(
                    select(func.count()).select_from(metadata.tables["execution"])
                ).scalar_one()
        self.assertEqual(count, 1)

    def test_concurrent_revocation_claim_has_exactly_one_winner(self) -> None:
        owner = str(uuid4())
        credential_id = str(uuid4())
        job_id = str(uuid4())
        now = datetime.now(UTC)
        lease_until = now + timedelta(minutes=2)
        principal = metadata.tables["principal"]
        credential = metadata.tables["oauth_credential"]
        revocation_job = metadata.tables["credential_revocation_job"]

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(principal).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                connection.execute(
                    insert(credential).values(
                        id=credential_id,
                        principal_id=owner,
                        vault_ref="vault-ref",
                        status="revoked",
                        key_version=1,
                        created_at=now,
                    )
                )
                connection.execute(
                    insert(revocation_job).values(
                        id=job_id,
                        principal_id=owner,
                        credential_id=credential_id,
                        vault_ref="vault-ref",
                        status="pending",
                        attempts=0,
                        next_attempt_at=now,
                        last_error_code=None,
                        created_at=now,
                        completed_at=None,
                    )
                )

        barrier = Barrier(2)

        def claim(_index: int):  # noqa: ANN202
            with self.engine.connect() as connection:
                self._set_search_path(connection)
                with connection.begin():
                    set_transaction_principal(connection, owner)
                    barrier.wait(timeout=10)
                    return PostgresCredentialRevocationRepository(connection).claim_due(
                        owner, now=now, lease_until=lease_until
                    )

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, range(2)))

        winners = [claimed for claimed in claims if claimed is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].id, job_id)
        self.assertEqual(winners[0].attempts, 1)
        self.assertEqual(winners[0].next_attempt_at, lease_until)

        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                set_transaction_principal(connection, owner)
                stored = connection.execute(
                    select(
                        revocation_job.c.attempts,
                        revocation_job.c.next_attempt_at,
                    ).where(revocation_job.c.id == job_id)
                ).one()
        self.assertEqual(stored.attempts, 1)
        self.assertEqual(stored.next_attempt_at, lease_until)

    def test_concurrent_authorization_code_claim_has_exactly_one_winner(self) -> None:
        owner = str(uuid4())
        raw_code = f"concurrent-code-{uuid4()}"
        transaction_id = f"concurrent-transaction-{uuid4()}"
        now = datetime.now(UTC)
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(metadata.tables["principal"]).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=now,
                    )
                )
                connection.execute(
                    insert(metadata.tables["authorization_transaction"]).values(
                        id=transaction_id,
                        client_id="client",
                        redirect_uri="https://client.example/callback",
                        code_challenge="a" * 43,
                        code_challenge_method="S256",
                        resource="https://connector.example/mcp",
                        scope="read",
                        client_state="state",
                        consent_csrf_hash="csrf",
                        status="completed",
                        expires_at=now,
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                connection.execute(
                    insert(metadata.tables["authorization_code"]).values(
                        code_hash=hash_token(raw_code),
                        transaction_id=transaction_id,
                        principal_id=owner,
                        client_id="client",
                        redirect_uri="https://client.example/callback",
                        code_challenge="a" * 43,
                        code_challenge_method="S256",
                        resource="https://connector.example/mcp",
                        scope="read",
                        expires_at=now,
                        consumed_at=None,
                        created_at=now,
                    )
                )

        barrier = Barrier(2)

        def claim(_index: int) -> bool:
            with self.engine.connect() as connection:
                self._set_search_path(connection)
                with connection.begin():
                    self.assertEqual(
                        bootstrap_authorization_code_principal(connection, raw_code), owner
                    )
                    barrier.wait(timeout=10)
                    _stored, already_consumed = PostgresAuthorizationCodeRepository(
                        connection
                    ).claim(raw_code)
                    return already_consumed

        with ThreadPoolExecutor(max_workers=2) as pool:
            replay_flags = list(pool.map(claim, range(2)))

        self.assertCountEqual(replay_flags, [False, True])
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                set_transaction_principal(connection, owner)
                consumed_at = connection.execute(
                    select(metadata.tables["authorization_code"].c.consumed_at).where(
                        metadata.tables["authorization_code"].c.code_hash == hash_token(raw_code)
                    )
                ).scalar_one()
        self.assertIsNotNone(consumed_at)

    def test_concurrent_authorization_consent_has_exactly_one_winner(self) -> None:
        transaction_id = f"concurrent-consent-{uuid4()}"
        now = datetime.now(UTC)
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(metadata.tables["authorization_transaction"]).values(
                        id=transaction_id,
                        client_id="client",
                        redirect_uri="https://client.example/callback",
                        code_challenge="a" * 43,
                        code_challenge_method="S256",
                        resource="https://connector.example/mcp",
                        scope="read",
                        client_state="state",
                        consent_csrf_hash="csrf",
                        status="pending",
                        expires_at=now + timedelta(minutes=10),
                        created_at=now,
                    )
                )

        barrier = Barrier(2)

        def consent(_index: int) -> bool:
            try:
                with self.engine.connect() as connection:
                    self._set_search_path(connection)
                    with connection.begin():
                        repository = PostgresAuthorizationTransactionRepository(connection)
                        transaction = repository.get(transaction_id)
                        self.assertIsNotNone(transaction)
                        assert transaction is not None
                        consented = consent_transaction(transaction, now=now)
                        barrier.wait(timeout=10)
                        repository.save(consented)
                return True
            except AuthError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = list(pool.map(consent, range(2)))

        self.assertCountEqual(winners, [True, False])
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            status = connection.execute(
                select(metadata.tables["authorization_transaction"].c.status).where(
                    metadata.tables["authorization_transaction"].c.id == transaction_id
                )
            ).scalar_one()
        self.assertEqual(status, "consented")

    def test_concurrent_refresh_rotation_revokes_replayed_family(self) -> None:
        owner = str(uuid4())
        raw_token = f"concurrent-refresh-{uuid4()}"
        family_id = f"family-{uuid4()}"
        now = datetime.now(UTC)
        stored = RefreshToken(
            token=raw_token,
            family_id=family_id,
            principal_id=owner,
            client_id="client",
            resource="https://connector.example/mcp",
            scope="read",
            expires_at=now + timedelta(days=30),
        )
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(metadata.tables["principal"]).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                PostgresTokenRepository(connection).save_refresh(stored)

        barrier = Barrier(2)

        def rotate(_index: int) -> str:
            result = "rotated"
            with self.engine.connect() as connection:
                self._set_search_path(connection)
                with connection.begin():
                    self.assertEqual(
                        bootstrap_refresh_token_principal(connection, raw_token), owner
                    )
                    barrier.wait(timeout=10)
                    try:
                        PostgresTokenRepository(connection).rotate(raw_token, now=now)
                    except AuthError:
                        # The replay revocation must commit, matching the HTTP route boundary.
                        result = "replay"
            return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(rotate, range(2)))

        self.assertCountEqual(outcomes, ["rotated", "replay"])
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                set_transaction_principal(connection, owner)
                statuses = list(
                    connection.execute(
                        select(metadata.tables["refresh_token"].c.status).where(
                            metadata.tables["refresh_token"].c.family_id == family_id
                        )
                    ).scalars()
                )
        self.assertEqual(statuses, ["revoked", "revoked"])

    def test_concurrent_approval_decision_has_exactly_one_audit_event(self) -> None:
        owner = str(uuid4())
        now = datetime.now(UTC)
        pending = submit_proposal(
            Proposal.create(
                proposal_id=str(uuid4()),
                principal_id=owner,
                customer_id="1234567890",
                payload={"type": "campaign_pause", "campaign_id": "1"},
                expires_at=now + timedelta(hours=1),
            ),
            now=now,
        )
        approved, approval = approve_proposal(
            pending,
            principal_id=owner,
            approver_id=owner,
            decision=Decision.APPROVE,
            now=now,
        )
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                connection.execute(
                    insert(metadata.tables["principal"]).values(
                        id=owner,
                        issuer="test",
                        subject=owner,
                        status="active",
                        created_at=now,
                    )
                )
                set_transaction_principal(connection, owner)
                PostgresProposalRepository(connection).save(pending)

        barrier = Barrier(2)

        def decide(index: int) -> str:
            result = "won"
            with self.engine.connect() as connection:
                self._set_search_path(connection)
                try:
                    with connection.begin():
                        set_transaction_principal(connection, owner)
                        barrier.wait(timeout=10)
                        PostgresApprovalRepository(connection).save_decision_with_audit(
                            approved,
                            approval,
                            AuditEvent(
                                event_id=str(uuid4()),
                                occurred_at=now,
                                actor=owner,
                                principal_id=owner,
                                customer_id=pending.customer_id,
                                event_type="approval.decided",
                                outcome=Decision.APPROVE.value,
                                proposal_id=pending.proposal_id,
                                approval_id=None,
                                execution_id=None,
                                reason_code=None,
                                correlation_id=f"concurrent-decision-{index}",
                                google_request_id=None,
                            ),
                        )
                except ValueError:
                    result = "lost"
            return result

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(decide, range(2)))

        self.assertCountEqual(outcomes, ["won", "lost"])
        with self.engine.connect() as connection:
            self._set_search_path(connection)
            with connection.begin():
                set_transaction_principal(connection, owner)
                approval_count = connection.execute(
                    select(func.count()).select_from(metadata.tables["approval"])
                ).scalar_one()
                audit_count = connection.execute(
                    select(func.count()).select_from(metadata.tables["audit_event"])
                ).scalar_one()
        self.assertEqual((approval_count, audit_count), (1, 1))

    def _set_search_path(self, connection) -> None:  # noqa: ANN001
        connection.exec_driver_sql(
            f"SET search_path TO {_quote_identifier(self.schema_name)}, public"
        )
        connection.commit()

    def _current_principal(self, connection) -> str | None:  # noqa: ANN001
        return connection.execute(
            text("SELECT current_setting(:setting_name, true)"),
            {"setting_name": PRINCIPAL_CONTEXT_SETTING},
        ).scalar_one()


if __name__ == "__main__":
    unittest.main()
