"""Contract tests for the first SQLAlchemy/PostgreSQL repository slice."""

from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import (  # noqa: E402
    Decision,
    Proposal,
    ProposalStatus,
    approve_proposal,
    reserve_execution,
    submit_proposal,
)
from backend.src.auth.domain import (  # noqa: E402
    AccessToken,
    AuthError,
    AuthorizationTransaction,
    ClientIdentity,
    RefreshToken,
    compute_code_challenge,
    consent_transaction,
    issue_authorization_code,
)
from backend.src.db.models import AuditEvent, ExecutionStatus  # noqa: E402
from backend.src.db.postgres_repository import (  # noqa: E402
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
from backend.src.db.sqlalchemy_schema import metadata  # noqa: E402


def _stable_uuid(label: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, label))


class PostgresOAuthRepositoryContractTests(unittest.TestCase):
    """Production OAuth persistence preserves opaque IDs and single-use claims."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        tables = metadata.tables
        with self.engine.begin() as connection:
            tables["principal"].create(connection)
            tables["authorization_transaction"].create(connection)
            tables["authorization_code"].create(connection)
            tables["access_token"].create(connection)
            tables["refresh_token"].create(connection)
            tables["web_login_state"].create(connection)
            tables["web_session"].create(connection)
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.principals = PostgresPrincipalRepository(self.connection)
        self.transactions = PostgresAuthorizationTransactionRepository(self.connection)
        self.codes = PostgresAuthorizationCodeRepository(self.connection)
        self.tokens = PostgresTokenRepository(self.connection)
        self.login_states = PostgresWebLoginStateRepository(self.connection)
        self.web_sessions = PostgresWebSessionRepository(self.connection)
        self.now = datetime(2026, 7, 19, 12, tzinfo=UTC)

    def tearDown(self) -> None:
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()

    def _transaction(self) -> AuthorizationTransaction:
        verifier = "v" * 43
        client = ClientIdentity(
            client_id="https://client.example/metadata.json",
            redirect_uris=("https://client.example/callback",),
            token_endpoint_auth_method="none",
        )
        return AuthorizationTransaction.create(
            transaction_id="opaque.transaction-id_123",
            client=client,
            redirect_uri=client.redirect_uris[0],
            code_challenge=compute_code_challenge(verifier),
            code_challenge_method="S256",
            resource="https://connector.example.com/mcp",
            expected_resource="https://connector.example.com/mcp",
            scope="google_ads.read",
            client_state="state-1",
            consent_csrf_hash="csrf-hash",
            now=self.now,
        )

    def test_transaction_round_trip_and_status_advance(self) -> None:
        transaction = self._transaction()
        self.transactions.save(transaction)
        consented = consent_transaction(transaction, now=self.now)
        self.transactions.save(consented)

        loaded = self.transactions.get(transaction.transaction_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.transaction_id, "opaque.transaction-id_123")
        self.assertEqual(loaded.status, consented.status)

    def test_transaction_status_advance_is_compare_and_set(self) -> None:
        transaction = self._transaction()
        self.transactions.save(transaction)
        consented = consent_transaction(transaction, now=self.now)
        self.transactions.save(consented)

        with self.assertRaisesRegex(AuthError, "daha once ilerletilmis"):
            self.transactions.save(consented)

        loaded = self.transactions.get(transaction.transaction_id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, consented.status)

    def test_authorization_code_claim_is_single_use_and_hash_only(self) -> None:
        owner = self.principals.get_or_create("issuer", "oauth-owner")
        transaction = consent_transaction(self._transaction(), now=self.now)
        self.transactions.save(transaction)
        code = issue_authorization_code(transaction, principal_id=owner.id, now=self.now)
        self.codes.save(code)

        first, already_consumed = self.codes.claim(code.code)
        second, second_already_consumed = self.codes.claim(code.code)
        self.assertFalse(already_consumed)
        self.assertTrue(second_already_consumed)
        self.assertEqual(first.principal_id, owner.id)
        self.assertEqual(second.transaction_id, transaction.transaction_id)
        stored_hash = self.connection.exec_driver_sql(
            "SELECT code_hash FROM authorization_code"
        ).scalar_one()
        self.assertNotEqual(stored_hash, code.code)

    def test_unknown_authorization_code_fails_closed(self) -> None:
        with self.assertRaises(AuthError):
            self.codes.claim("unknown-code")

    def _token_pair(self, principal_id: str, *, family_id: str = "family_opaque-1"):
        access = AccessToken(
            token="access-secret",
            principal_id=principal_id,
            client_id="client-1",
            resource="https://connector.example.com/mcp",
            scope="google_ads.read",
            expires_at=self.now + timedelta(minutes=10),
        )
        refresh = RefreshToken(
            token="refresh-secret",
            family_id=family_id,
            principal_id=principal_id,
            client_id="client-1",
            resource="https://connector.example.com/mcp",
            scope="google_ads.read",
            expires_at=self.now + timedelta(days=30),
        )
        return access, refresh

    def test_token_hash_round_trip_rotation_and_family_reuse_revocation(self) -> None:
        owner = self.principals.get_or_create("issuer", "token-owner")
        access, refresh = self._token_pair(owner.id)
        self.tokens.save_access(access)
        self.tokens.save_refresh(refresh)

        loaded = self.tokens.get_access(access.token)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.principal_id, owner.id)
        outcome = self.tokens.rotate(refresh.token, now=self.now + timedelta(seconds=1))
        with self.assertRaises(AuthError):
            self.tokens.rotate(refresh.token, now=self.now + timedelta(seconds=2))
        with self.assertRaises(AuthError):
            self.tokens.rotate(outcome.refresh_token.token, now=self.now + timedelta(seconds=3))
        raw_values = self.connection.exec_driver_sql(
            "SELECT token_hash FROM access_token UNION ALL SELECT token_hash FROM refresh_token"
        ).scalars()
        self.assertNotIn(access.token, list(raw_values))

    def test_disconnect_revokes_only_the_target_principals_tokens(self) -> None:
        owner = self.principals.get_or_create("issuer", "disconnect-owner")
        other = self.principals.get_or_create("issuer", "other-owner")
        owner_access, owner_refresh = self._token_pair(owner.id, family_id="owner-family")
        other_access, other_refresh = self._token_pair(other.id, family_id="other-family")
        other_access = dataclasses.replace(other_access, token="other-access")
        other_refresh = dataclasses.replace(other_refresh, token="other-refresh")
        for access, refresh in (
            (owner_access, owner_refresh),
            (other_access, other_refresh),
        ):
            self.tokens.save_access(access)
            self.tokens.save_refresh(refresh)

        self.tokens.revoke_all_for_principal(owner.id, now=self.now)

        self.assertIsNone(self.tokens.get_access(owner_access.token))
        self.assertIsNotNone(self.tokens.get_access(other_access.token))
        with self.assertRaises(AuthError):
            self.tokens.rotate(owner_refresh.token, now=self.now + timedelta(seconds=1))
        self.tokens.rotate(other_refresh.token, now=self.now + timedelta(seconds=1))

    def test_web_login_state_is_hash_only_single_use_and_unknown_safe(self) -> None:
        expires_at = self.now + timedelta(minutes=10)
        self.login_states.create("raw-login-state", expires_at)

        first = self.login_states.claim("raw-login-state")
        replay = self.login_states.claim("raw-login-state")

        self.assertEqual(first, (False, expires_at))
        self.assertEqual(replay, (True, expires_at))
        self.assertIsNone(self.login_states.claim("unknown-state"))
        stored_hash = self.connection.exec_driver_sql(
            "SELECT state_hash FROM web_login_state"
        ).scalar_one()
        self.assertNotEqual(stored_hash, "raw-login-state")

    def test_web_sessions_are_hash_only_and_principal_scoped_on_revoke_all(self) -> None:
        owner = self.principals.get_or_create("issuer", "web-owner")
        other = self.principals.get_or_create("issuer", "web-other")
        first = self.web_sessions.create(
            owner.id, "owner-session-1", "owner-csrf-1", self.now + timedelta(minutes=30)
        )
        second = self.web_sessions.create(
            owner.id, "owner-session-2", "owner-csrf-2", self.now + timedelta(minutes=30)
        )
        other_session = self.web_sessions.create(
            other.id, "other-session", "other-csrf", self.now + timedelta(minutes=30)
        )

        self.assertEqual(first.token, "owner-session-1")
        self.assertEqual(self.web_sessions.lookup(first.token).principal_id, owner.id)
        self.web_sessions.revoke(second.token)
        self.assertTrue(self.web_sessions.lookup(second.token).revoked)
        self.web_sessions.revoke_all_for_principal(owner.id)

        self.assertTrue(self.web_sessions.lookup(first.token).revoked)
        self.assertFalse(self.web_sessions.lookup(other_session.token).revoked)
        unknown = self.web_sessions.lookup("unknown-session")
        self.assertIsNone(unknown.principal_id)
        stored = self.connection.exec_driver_sql(
            "SELECT token_hash, csrf_token_hash FROM web_session WHERE principal_id = ?",
            (owner.id,),
        ).all()
        self.assertNotIn("owner-session-1", {row[0] for row in stored})
        self.assertNotIn("owner-csrf-1", {row[1] for row in stored})


class PostgresRepositoryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "repository.db"
        self.engine = create_engine(f"sqlite:///{db_path}")
        with self.engine.begin() as connection:
            _create_minimal_tables(connection)
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.principals = PostgresPrincipalRepository(self.connection)
        self.grants = PostgresClientGrantRepository(self.connection)
        self.accounts = PostgresAdsAccountRepository(self.connection)
        self.credentials = PostgresOAuthCredentialRepository(self.connection)
        self.revocations = PostgresCredentialRevocationRepository(self.connection)
        self.proposals = PostgresProposalRepository(self.connection)
        self.approvals = PostgresApprovalRepository(self.connection)
        self.executions = PostgresExecutionRepository(self.connection)
        self.audit = PostgresAuditRepository(self.connection)
        self.now = datetime(2026, 7, 18, 12, tzinfo=UTC)
        self.payload = {"type": "campaign_budget_update", "after": {"amount_micros": 5_000_000}}

    def tearDown(self) -> None:
        self.transaction.rollback()
        self.connection.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_get_or_create_is_idempotent(self) -> None:
        first = self.principals.get_or_create("https://issuer.example", "user-1")
        second = self.principals.get_or_create("https://issuer.example", "user-1")

        self.assertEqual(first.id, second.id)

    def test_credential_revocation_outbox_is_owned_idempotent_and_retryable(self) -> None:
        owner = self.principals.get_or_create("iss", "revocation-owner")
        attacker = self.principals.get_or_create("iss", "revocation-attacker")
        credential = self.credentials.upsert(owner.id, "vault://credential-1", 1)

        self.assertIsNone(
            self.revocations.revoke_and_enqueue(attacker.id, credential.id, now=self.now)
        )
        first = self.revocations.revoke_and_enqueue(owner.id, credential.id, now=self.now)
        second = self.revocations.revoke_and_enqueue(owner.id, credential.id, now=self.now)
        self.assertIsNotNone(first)
        assert first is not None and second is not None
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.vault_ref, "vault://credential-1")
        self.assertIsNone(self.credentials.get_active(owner.id))

        claimed = self.revocations.claim_due(
            owner.id, now=self.now, lease_until=self.now + timedelta(minutes=1)
        )
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.attempts, 1)
        self.assertIsNone(
            self.revocations.claim_due(
                owner.id, now=self.now, lease_until=self.now + timedelta(minutes=1)
            )
        )
        self.assertTrue(
            self.revocations.retry(
                owner.id,
                claimed.id,
                claimed_attempt=claimed.attempts,
                error_code="VAULT_UNAVAILABLE",
                next_attempt_at=self.now + timedelta(minutes=2),
            )
        )
        reclaimed = self.revocations.claim_due(
            owner.id,
            now=self.now + timedelta(minutes=2),
            lease_until=self.now + timedelta(minutes=3),
        )
        self.assertIsNotNone(reclaimed)
        assert reclaimed is not None
        self.assertEqual(reclaimed.attempts, 2)
        self.assertTrue(
            self.revocations.complete(
                owner.id,
                reclaimed.id,
                claimed_attempt=reclaimed.attempts,
                completed_at=self.now + timedelta(minutes=2),
            )
        )
        self.assertFalse(
            self.revocations.complete(
                owner.id,
                reclaimed.id,
                claimed_attempt=reclaimed.attempts,
                completed_at=self.now + timedelta(minutes=2),
            )
        )

    def test_revocation_retry_rejects_raw_provider_error_text(self) -> None:
        with self.assertRaises(ValueError):
            self.revocations.retry(
                _stable_uuid("missing-owner"),
                _stable_uuid("missing-job"),
                claimed_attempt=1,
                error_code="provider said token=secret",
                next_attempt_at=self.now,
            )

    def test_stale_revocation_worker_cannot_overwrite_a_newer_claim(self) -> None:
        owner = self.principals.get_or_create("iss", "stale-worker-owner")
        credential = self.credentials.upsert(owner.id, "vault://stale-worker", 1)
        job = self.revocations.revoke_and_enqueue(owner.id, credential.id, now=self.now)
        self.assertIsNotNone(job)

        first = self.revocations.claim_due(
            owner.id, now=self.now, lease_until=self.now + timedelta(minutes=1)
        )
        second = self.revocations.claim_due(
            owner.id,
            now=self.now + timedelta(minutes=1),
            lease_until=self.now + timedelta(minutes=2),
        )
        assert first is not None and second is not None
        self.assertEqual((first.attempts, second.attempts), (1, 2))

        self.assertFalse(
            self.revocations.retry(
                owner.id,
                first.id,
                claimed_attempt=first.attempts,
                error_code="VAULT_UNAVAILABLE",
                next_attempt_at=self.now + timedelta(minutes=1),
            )
        )
        self.assertFalse(
            self.revocations.complete(
                owner.id,
                first.id,
                claimed_attempt=first.attempts,
                completed_at=self.now + timedelta(minutes=1),
            )
        )
        self.assertTrue(
            self.revocations.complete(
                owner.id,
                second.id,
                claimed_attempt=second.attempts,
                completed_at=self.now + timedelta(minutes=1),
            )
        )

    def test_different_subjects_get_different_principals(self) -> None:
        a = self.principals.get_or_create("https://issuer.example", "user-1")
        b = self.principals.get_or_create("https://issuer.example", "user-2")

        self.assertNotEqual(a.id, b.id)

    def test_client_grant_record_and_check_consent(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        client_id = "https://claude.ai/oauth/hosted-client-metadata"

        self.assertFalse(self.grants.has_active_grant(owner.id, client_id))
        self.grants.record_consent(owner.id, client_id, "adwords")

        self.assertTrue(self.grants.has_active_grant(owner.id, client_id))

    def test_client_grant_re_consent_is_idempotent_and_updates_scope(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        client_id = "https://claude.ai/oauth/hosted-client-metadata"

        self.grants.record_consent(owner.id, client_id, "adwords")
        self.grants.record_consent(owner.id, client_id, "adwords openid")

        self.assertTrue(self.grants.has_active_grant(owner.id, client_id))
        stored = self.connection.exec_driver_sql(
            "SELECT scope FROM oauth_client_grant"
        ).scalar_one()
        self.assertEqual(stored, "adwords openid")

    def test_client_grant_lookup_is_principal_scoped(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        client_id = "https://claude.ai/oauth/hosted-client-metadata"

        self.grants.record_consent(owner.id, client_id, "adwords")

        self.assertFalse(self.grants.has_active_grant(attacker.id, client_id))

    def test_link_account_is_idempotent_and_principal_scoped(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")

        first = self.accounts.link_account(owner.id, "1234567890", None)
        second = self.accounts.link_account(owner.id, "1234567890", None)

        self.assertEqual(first.id, second.id)
        self.assertIsNone(self.accounts.get_account(attacker.id, "1234567890"))

    def test_list_accounts_only_returns_own_accounts(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        other = self.principals.get_or_create("iss", "other")

        self.accounts.link_account(owner.id, "1111111111", None)
        self.accounts.link_account(other.id, "2222222222", None)

        self.assertEqual(
            [account.customer_id for account in self.accounts.list_accounts(owner.id)],
            ["1111111111"],
        )

    def test_active_accessors_hide_disconnected_rows_but_history_accessors_keep_them(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        self.accounts.link_account(owner.id, "1111111111", None)

        self.accounts.disconnect_all(owner.id)

        historical = self.accounts.get_account(owner.id, "1111111111")
        assert historical is not None
        self.assertEqual(historical.status, "disconnected")
        self.assertIsNone(self.accounts.get_active_account(owner.id, "1111111111"))
        self.assertEqual(self.accounts.list_active_accounts(owner.id), [])

    def test_relinking_disconnected_account_reactivates_existing_row(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        first = self.accounts.link_account(owner.id, "1111111111", None)
        self.accounts.disconnect_all(owner.id)

        relinked = self.accounts.link_account(owner.id, "1111111111", "9999999999")

        self.assertEqual(relinked.id, first.id)
        self.assertEqual(relinked.status, "active")
        self.assertEqual(relinked.login_customer_id, "9999999999")

    def test_repository_does_not_commit_its_own_transaction(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        self.accounts.link_account(owner.id, "1111111111", None)

        observer = self.engine.connect()
        try:
            rows = observer.exec_driver_sql("SELECT * FROM ads_account").fetchall()
            self.assertEqual(rows, [])
        finally:
            observer.close()

    def test_credential_upsert_revokes_previous_active_metadata(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")

        first = self.credentials.upsert(owner.id, "vault://ref-1", key_version=1)
        second = self.credentials.upsert(owner.id, "vault://ref-2", key_version=2)

        active = self.credentials.get_active(owner.id)
        assert active is not None
        self.assertEqual(active.id, second.id)
        self.assertEqual(active.vault_ref, "vault://ref-2")
        self.assertEqual(active.key_version, 2)
        self.assertNotEqual(first.id, second.id)

    def test_credential_lookup_is_principal_scoped(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")

        self.credentials.upsert(owner.id, "vault://owner-ref", key_version=1)

        self.assertIsNone(self.credentials.get_active(attacker.id))

    def test_credential_revoke_requires_owner_principal(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        credential = self.credentials.upsert(owner.id, "vault://owner-ref", key_version=1)

        self.credentials.revoke(attacker.id, credential.id)
        self.assertIsNotNone(self.credentials.get_active(owner.id))

        revoked = self.credentials.revoke_active(owner.id)
        assert revoked is not None
        self.assertEqual(revoked.id, credential.id)
        self.assertIsNone(self.credentials.get_active(owner.id))

    def test_proposal_round_trip_and_cross_principal_lookup(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))

        self.proposals.save(pending)

        loaded = self.proposals.get(owner.id, _stable_uuid("proposal-1"))
        assert loaded is not None
        self.assertEqual(loaded.proposal_hash, pending.proposal_hash)
        self.assertEqual(dict(loaded.payload), dict(pending.payload))
        self.assertIsNone(self.proposals.get(attacker.id, _stable_uuid("proposal-1")))

    def test_proposal_save_cannot_replace_payload_or_owner_scope(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))
        self.proposals.save(pending)

        foreign = self._pending_proposal(attacker.id, _stable_uuid("proposal-1"))
        with self.assertRaisesRegex(ValueError, "farkli bir principal"):
            self.proposals.save(foreign)

        changed = submit_proposal(
            Proposal.create(
                proposal_id=_stable_uuid("proposal-1"),
                principal_id=owner.id,
                customer_id="1234567890",
                payload={"type": "campaign_budget_update", "after": {"amount_micros": 1}},
                expires_at=self.now + timedelta(minutes=30),
            ),
            now=self.now,
        )
        with self.assertRaisesRegex(ValueError, "payload/hash"):
            self.proposals.save(changed)

    def test_proposal_list_pending_filters_and_paginates_by_owner(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        other = self.principals.get_or_create("iss", "other")
        self.proposals.save(
            self._pending_proposal(owner.id, _stable_uuid("proposal-1"), "1234567890")
        )
        self.proposals.save(
            self._pending_proposal(owner.id, _stable_uuid("proposal-2"), "2222222222")
        )
        self.proposals.save(
            self._pending_proposal(other.id, _stable_uuid("proposal-3"), "1234567890")
        )

        first_page = self.proposals.list_pending(owner.id, limit=1, now=self.now)
        self.assertEqual(
            [item.proposal_id for item in first_page.proposals], [_stable_uuid("proposal-1")]
        )
        self.assertTrue(first_page.has_more)

        second_page = self.proposals.list_pending(
            owner.id,
            limit=2,
            after_created_at=first_page.last_created_at,
            after_id=first_page.last_id,
            now=self.now,
        )
        self.assertEqual(
            [item.proposal_id for item in second_page.proposals], [_stable_uuid("proposal-2")]
        )
        self.assertFalse(second_page.has_more)

        filtered = self.proposals.list_pending(owner.id, customer_id="2222222222", now=self.now)
        self.assertEqual(
            [item.proposal_id for item in filtered.proposals], [_stable_uuid("proposal-2")]
        )

    def test_approval_save_and_lookup_are_principal_scoped(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending,
            principal_id=owner.id,
            approver_id=owner.id,
            decision=Decision.APPROVE,
            now=self.now,
        )

        self.proposals.save(approved)
        self.approvals.save(approval)

        latest = self.approvals.get_latest(owner.id, _stable_uuid("proposal-1"))
        assert latest is not None
        self.assertEqual(latest.decision, Decision.APPROVE)
        self.assertIsNone(self.approvals.get_latest(attacker.id, _stable_uuid("proposal-1")))

    def test_approval_cannot_reference_another_principals_proposal(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))
        self.proposals.save(pending)
        _, approval = approve_proposal(
            pending,
            principal_id=owner.id,
            approver_id=owner.id,
            decision=Decision.APPROVE,
            now=self.now,
        )
        foreign = type(approval)(
            proposal_id=approval.proposal_id,
            principal_id=attacker.id,
            approver_id=attacker.id,
            decision=approval.decision,
            proposal_hash=approval.proposal_hash,
            decided_at=approval.decided_at,
        )

        with self.assertRaisesRegex(ValueError, "principal kapsami"):
            self.approvals.save(foreign)

    def test_save_decision_with_audit_stores_decision_and_event_together(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending,
            principal_id=owner.id,
            approver_id=owner.id,
            decision=Decision.APPROVE,
            now=self.now,
        )

        approval_id = self.approvals.save_decision_with_audit(
            approved,
            approval,
            self._audit_event(owner.id, "evt-approval", approved.proposal_id),
        )

        self.assertEqual(
            self.proposals.get(owner.id, _stable_uuid("proposal-1")).status,
            ProposalStatus.APPROVED,
        )
        self.assertEqual(
            self.approvals.get_latest(owner.id, _stable_uuid("proposal-1")).decision,
            Decision.APPROVE,
        )
        events = self.audit.list_for_principal(owner.id)
        self.assertEqual([event.event_id for event in events], [_stable_uuid("evt-approval")])
        self.assertEqual(events[0].approval_id, approval_id)

    def test_second_decision_cannot_transition_an_already_decided_proposal(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending,
            principal_id=owner.id,
            approver_id=owner.id,
            decision=Decision.APPROVE,
            now=self.now,
        )
        event = self._audit_event(owner.id, "evt-first", approved.proposal_id)
        self.approvals.save_decision_with_audit(approved, approval, event)

        with self.assertRaisesRegex(ValueError, "proposal decision"):
            self.approvals.save_decision_with_audit(
                approved,
                approval,
                self._audit_event(owner.id, "evt-second", approved.proposal_id),
            )

        self.assertEqual(len(self.audit.list_for_principal(owner.id)), 1)

    def test_save_decision_with_audit_rejects_mismatched_event(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        pending = self._pending_proposal(owner.id, _stable_uuid("proposal-1"))
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending,
            principal_id=owner.id,
            approver_id=owner.id,
            decision=Decision.APPROVE,
            now=self.now,
        )
        bad_event = self._audit_event(owner.id, "evt-approval", approved.proposal_id)
        bad_event = type(bad_event)(**{**bad_event.__dict__, "outcome": Decision.REJECT.value})

        with self.assertRaisesRegex(ValueError, "approval karari"):
            self.approvals.save_decision_with_audit(approved, approval, bad_event)

        self.assertEqual(
            self.proposals.get(owner.id, _stable_uuid("proposal-1")).status,
            ProposalStatus.PENDING_APPROVAL,
        )
        self.assertIsNone(self.approvals.get_latest(owner.id, _stable_uuid("proposal-1")))

    def test_duplicate_idempotency_key_reuses_same_execution_row(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        approved, approval = self._approved_proposal(owner.id, _stable_uuid("proposal-1"))
        self.approvals.save(approval)
        _, reservation = reserve_execution(
            approved,
            approval,
            principal_id=owner.id,
            current_payload=self.payload,
            idempotency_key="request-1",
            now=self.now,
        )

        first = self.executions.record(reservation, before="{}", after="{}")
        second = self.executions.record(reservation, before="{}", after="{}")

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.execution_id, second.execution_id)

    def test_idempotency_key_cannot_be_reused_for_different_execution_payload(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        approved, approval = self._approved_proposal(owner.id, _stable_uuid("proposal-1"))
        _, reservation = reserve_execution(
            approved,
            approval,
            principal_id=owner.id,
            current_payload=self.payload,
            idempotency_key="request-1",
            now=self.now,
        )
        self.executions.record(reservation, before="{}", after="{}")

        with self.assertRaisesRegex(ValueError, "idempotency_key"):
            self.executions.record(reservation, before='{"old": true}', after="{}")

    def test_execution_cannot_reference_another_principals_proposal(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        approved, approval = self._approved_proposal(owner.id, _stable_uuid("proposal-1"))
        _, reservation = reserve_execution(
            approved,
            approval,
            principal_id=owner.id,
            current_payload=self.payload,
            idempotency_key="request-2",
            now=self.now,
        )
        foreign = type(reservation)(
            proposal_id=reservation.proposal_id,
            principal_id=attacker.id,
            customer_id=reservation.customer_id,
            proposal_hash=reservation.proposal_hash,
            idempotency_key=reservation.idempotency_key,
            reserved_at=reservation.reserved_at,
        )

        with self.assertRaisesRegex(ValueError, "proposal ve principal"):
            self.executions.record(foreign, before="{}", after="{}")

    def test_execution_reservation_must_match_proposal_snapshot(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        approved, approval = self._approved_proposal(owner.id, _stable_uuid("proposal-1"))
        _, reservation = reserve_execution(
            approved,
            approval,
            principal_id=owner.id,
            current_payload=self.payload,
            idempotency_key="request-3",
            now=self.now,
        )
        stale = type(reservation)(
            proposal_id=reservation.proposal_id,
            principal_id=reservation.principal_id,
            customer_id="9999999999",
            proposal_hash=reservation.proposal_hash,
            idempotency_key=reservation.idempotency_key,
            reserved_at=reservation.reserved_at,
        )

        with self.assertRaisesRegex(ValueError, "proposal snapshot"):
            self.executions.record(stale, before="{}", after="{}")

    def test_mark_result_updates_status_and_request_id_for_owner_only(self) -> None:
        owner = self.principals.get_or_create("iss", "owner")
        attacker = self.principals.get_or_create("iss", "attacker")
        approved, approval = self._approved_proposal(owner.id, _stable_uuid("proposal-1"))
        _, reservation = reserve_execution(
            approved,
            approval,
            principal_id=owner.id,
            current_payload=self.payload,
            idempotency_key="request-4",
            now=self.now,
        )
        claim = self.executions.record(reservation, before="{}", after="{}")

        with self.assertRaisesRegex(ValueError, "principal kapsaminda"):
            self.executions.mark_result(
                attacker.id, claim.execution_id, ExecutionStatus.APPLIED, "foreign-request"
            )

        self.executions.mark_result(
            owner.id, claim.execution_id, ExecutionStatus.APPLIED, "gads-request-1"
        )
        row = (
            self.connection.exec_driver_sql(
                "SELECT status, google_request_id FROM execution WHERE google_request_id = ?",
                ("gads-request-1",),
            )
            .mappings()
            .one()
        )
        self.assertEqual(row["status"], ExecutionStatus.APPLIED.value)
        self.assertEqual(row["google_request_id"], "gads-request-1")

    def test_audit_repository_is_append_only_and_principal_scoped(self) -> None:
        public_methods = {name for name in dir(self.audit) if not name.startswith("_")}
        self.assertEqual(public_methods, {"insert", "list_for_principal"})
        owner = self.principals.get_or_create("iss", "owner")
        other = self.principals.get_or_create("iss", "other")

        self.audit.insert(self._audit_event(owner.id, "evt-owner", None))
        self.audit.insert(self._audit_event(other.id, "evt-other", None))

        self.assertEqual(
            [event.event_id for event in self.audit.list_for_principal(owner.id)],
            [_stable_uuid("evt-owner")],
        )

    def _pending_proposal(
        self,
        principal_id: str,
        proposal_id: str,
        customer_id: str = "1234567890",
    ) -> Proposal:
        draft = Proposal.create(
            proposal_id=proposal_id,
            principal_id=principal_id,
            customer_id=customer_id,
            payload=self.payload,
            expires_at=self.now + timedelta(minutes=30),
        )
        return submit_proposal(draft, now=self.now)

    def _approved_proposal(self, principal_id: str, proposal_id: str):
        pending = self._pending_proposal(principal_id, proposal_id)
        self.proposals.save(pending)
        approved, approval = approve_proposal(
            pending,
            principal_id=principal_id,
            approver_id=principal_id,
            decision=Decision.APPROVE,
            now=self.now,
        )
        self.proposals.save(approved)
        return approved, approval

    def _audit_event(
        self,
        principal_id: str,
        event_id: str,
        proposal_id: str | None,
    ) -> AuditEvent:
        return AuditEvent(
            event_id=_stable_uuid(event_id),
            occurred_at=self.now,
            actor=principal_id,
            principal_id=principal_id,
            customer_id="1234567890",
            event_type="approval.decided",
            proposal_id=proposal_id,
            approval_id=None,
            execution_id=None,
            outcome=Decision.APPROVE.value,
            reason_code=None,
            correlation_id=f"corr-{event_id}",
            google_request_id=None,
        )


def _create_minimal_tables(connection: Connection) -> None:
    connection.exec_driver_sql(
        """
        CREATE TABLE principal (
            id TEXT PRIMARY KEY,
            issuer TEXT NOT NULL,
            subject TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (issuer, subject)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE ads_account (
            id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            login_customer_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (principal_id, customer_id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE oauth_client_grant (
            id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            client_id TEXT NOT NULL,
            scope TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (principal_id, client_id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE oauth_credential (
            id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            vault_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            key_version INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE credential_revocation_job (
            id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            credential_id TEXT NOT NULL UNIQUE,
            vault_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL,
            next_attempt_at TEXT NOT NULL,
            last_error_code TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE proposal (
            id TEXT PRIMARY KEY,
            principal_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            proposal_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (id, principal_id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE approval (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            approver_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            proposal_hash TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            UNIQUE (proposal_id, principal_id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE audit_event (
            event_id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            principal_id TEXT,
            customer_id TEXT,
            event_type TEXT NOT NULL,
            proposal_id TEXT,
            approval_id TEXT,
            execution_id TEXT,
            outcome TEXT NOT NULL,
            reason_code TEXT,
            correlation_id TEXT NOT NULL,
            google_request_id TEXT
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE execution (
            id TEXT PRIMARY KEY,
            proposal_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            before TEXT NOT NULL,
            after TEXT NOT NULL,
            google_request_id TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (principal_id, proposal_id, idempotency_key)
        )
        """
    )


if __name__ == "__main__":
    unittest.main()
