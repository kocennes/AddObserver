"""Tests for backend.src.db.oauth_store (connector OAuth 2.1 AS persistence, ADR-0002)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.domain import (
    AuthError,
    AuthorizationTransaction,
    ClientIdentity,
    RefreshToken,
    RefreshTokenStatus,
    compute_code_challenge,
    consent_transaction,
    issue_authorization_code,
    issue_token_pair,
)
from backend.src.db.connection import connect
from backend.src.db.oauth_store import (
    AuthorizationCodeRepository,
    AuthorizationTransactionRepository,
    ClientGrantRepository,
    TokenRepository,
)
from backend.src.db.repository import PrincipalRepository

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
MCP_RESOURCE = "https://mcp.example.com/mcp"
CLIENT = ClientIdentity(
    client_id="https://claude.ai/oauth/hosted-client-metadata",
    redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
    token_endpoint_auth_method="none",
)


def _make_transaction(transaction_id: str = "txn-1") -> tuple[str, AuthorizationTransaction]:
    verifier = "a" * 43
    txn = AuthorizationTransaction.create(
        transaction_id=transaction_id,
        client=CLIENT,
        redirect_uri="https://claude.ai/api/mcp/auth_callback",
        code_challenge=compute_code_challenge(verifier),
        code_challenge_method="S256",
        resource=MCP_RESOURCE,
        expected_resource=MCP_RESOURCE,
        scope="adwords",
        client_state="client-state",
        now=NOW,
    )
    return verifier, txn


class ClientGrantRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.grants = ClientGrantRepository(self.conn)

    def test_record_and_check_consent(self) -> None:
        principal = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        self.assertFalse(self.grants.has_active_grant(principal.id, CLIENT.client_id))
        self.grants.record_consent(principal.id, CLIENT.client_id, "adwords")
        self.assertTrue(self.grants.has_active_grant(principal.id, CLIENT.client_id))

    def test_re_consent_is_idempotent(self) -> None:
        principal = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        self.grants.record_consent(principal.id, CLIENT.client_id, "adwords")
        self.grants.record_consent(principal.id, CLIENT.client_id, "adwords openid")
        self.assertTrue(self.grants.has_active_grant(principal.id, CLIENT.client_id))


class TransactionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.transactions = AuthorizationTransactionRepository(self.conn)

    def test_round_trip(self) -> None:
        _, txn = _make_transaction()
        self.transactions.save(txn)
        loaded = self.transactions.get(txn.transaction_id)
        assert loaded is not None
        self.assertEqual(loaded.client_id, txn.client_id)
        self.assertEqual(loaded.status, txn.status)

    def test_status_update_persists(self) -> None:
        _, txn = _make_transaction()
        self.transactions.save(txn)
        consented = consent_transaction(txn, now=NOW)
        self.transactions.save(consented)
        loaded = self.transactions.get(txn.transaction_id)
        assert loaded is not None
        self.assertEqual(loaded.status.value, "consented")

    def test_unknown_transaction_returns_none(self) -> None:
        self.assertIsNone(self.transactions.get("does-not-exist"))


class AuthorizationCodeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.transactions = AuthorizationTransactionRepository(self.conn)
        self.codes = AuthorizationCodeRepository(self.conn)

    def _issue(self):
        principal = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        _, txn = _make_transaction()
        self.transactions.save(txn)
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id=principal.id, now=NOW)
        self.codes.save(code)
        return principal, code

    def test_claim_succeeds_once(self) -> None:
        principal, code = self._issue()
        record, already_consumed = self.codes.claim(code.code)
        self.assertFalse(already_consumed)
        self.assertEqual(record.principal_id, principal.id)
        self.assertEqual(record.client_id, code.client_id)

    def test_duplicate_claim_is_reported_as_already_consumed(self) -> None:
        """Zorunlu vaka: kod tek kullanımlıktır -- ikinci redeem denemesi fail-closed olmalı."""
        _, code = self._issue()
        self.codes.claim(code.code)
        _, already_consumed = self.codes.claim(code.code)
        self.assertTrue(already_consumed)

    def test_unknown_code_raises(self) -> None:
        with self.assertRaises(AuthError):
            self.codes.claim("never-issued-code")


class TokenRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.transactions = AuthorizationTransactionRepository(self.conn)
        self.codes = AuthorizationCodeRepository(self.conn)
        self.tokens = TokenRepository(self.conn)

    def _issue_pair(self):
        from backend.src.auth.domain import consume_authorization_code

        principal = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        verifier, txn = _make_transaction()
        self.transactions.save(txn)
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id=principal.id, now=NOW)
        self.codes.save(code)
        stored, already_consumed = self.codes.claim(code.code)
        self.assertFalse(already_consumed)
        grant = consume_authorization_code(
            stored,
            client_id=CLIENT.client_id,
            redirect_uri=code.redirect_uri,
            resource=MCP_RESOURCE,
            code_verifier=verifier,
            already_consumed=already_consumed,
            now=NOW,
        )
        access, refresh = issue_token_pair(grant, now=NOW)
        self.tokens.save_access(access)
        self.tokens.save_refresh(refresh)
        return principal, access, refresh

    def test_get_access_round_trip(self) -> None:
        principal, access, _ = self._issue_pair()
        loaded = self.tokens.get_access(access.token)
        assert loaded is not None
        self.assertEqual(loaded.principal_id, principal.id)

    def test_wrong_access_token_returns_none(self) -> None:
        self._issue_pair()
        self.assertIsNone(self.tokens.get_access("not-a-real-token"))

    def test_rotate_succeeds_and_old_token_then_fails(self) -> None:
        _, _, refresh = self._issue_pair()
        outcome = self.tokens.rotate(refresh.token, now=NOW + timedelta(seconds=5))
        self.assertNotEqual(outcome.refresh_token.token, refresh.token)
        # The old (now-rotated) refresh token must not be usable again.
        with self.assertRaises(AuthError):
            self.tokens.rotate(refresh.token, now=NOW + timedelta(seconds=10))

    def test_reuse_after_rotation_revokes_whole_family(self) -> None:
        """Zorunlu vaka: rotated bir refresh_token tekrar kullanılırsa TÜM aile iptal edilir."""
        _, _, refresh = self._issue_pair()
        first = self.tokens.rotate(refresh.token, now=NOW + timedelta(seconds=5))
        with self.assertRaises(AuthError):
            self.tokens.rotate(refresh.token, now=NOW + timedelta(seconds=10))
        # The legitimately-rotated successor must also be dead now (family-wide revoke).
        with self.assertRaises(AuthError):
            self.tokens.rotate(first.refresh_token.token, now=NOW + timedelta(seconds=15))

    def test_unknown_refresh_token_raises(self) -> None:
        with self.assertRaises(AuthError):
            self.tokens.rotate("never-issued", now=NOW)


if __name__ == "__main__":
    unittest.main()
