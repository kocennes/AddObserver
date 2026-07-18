"""Tests for backend.src.auth.disconnect (docs/AUTH.md disconnect/revoke decision)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.disconnect import disconnect_principal
from backend.src.auth.domain import (
    AuthorizationTransaction,
    ClientIdentity,
    compute_code_challenge,
    consent_transaction,
    consume_authorization_code,
    issue_authorization_code,
    issue_token_pair,
)
from backend.src.auth.vault import LocalEncryptedVault, VaultError
from backend.src.db.connection import connect
from backend.src.db.oauth_store import (
    AuthorizationCodeRepository,
    AuthorizationTransactionRepository,
    TokenRepository,
)
from backend.src.db.proposals import AuditRepository
from backend.src.db.repository import (
    AdsAccountRepository,
    OAuthCredentialRepository,
    PrincipalRepository,
)
from backend.src.db.web_session_store import WebSessionRepository
from cryptography.fernet import Fernet

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
MCP_RESOURCE = "https://mcp.example.com/mcp"
CLIENT = ClientIdentity(
    client_id="https://claude.ai/oauth/hosted-client-metadata",
    redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
    token_endpoint_auth_method="none",
)


class DisconnectPrincipalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.accounts = AdsAccountRepository(self.conn)
        self.credentials = OAuthCredentialRepository(self.conn)
        self.tokens = TokenRepository(self.conn)
        self.audit = AuditRepository(self.conn)
        self.web_sessions = WebSessionRepository(self.conn)
        self.vault = LocalEncryptedVault(self.conn, Fernet.generate_key())
        self.principal = self.principals.get_or_create("https://accounts.google.com", "sub-1")

    def _issue_token_pair(self):
        verifier = "a" * 43
        txn = AuthorizationTransaction.create(
            transaction_id="txn-1",
            client=CLIENT,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge=compute_code_challenge(verifier),
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",
            client_state="client-state",
            consent_csrf_hash="test-consent-csrf-hash",
            now=NOW,
        )
        transactions = AuthorizationTransactionRepository(self.conn)
        transactions.save(txn)
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id=self.principal.id, now=NOW)
        codes = AuthorizationCodeRepository(self.conn)
        codes.save(code)
        stored, already_consumed = codes.claim(code.code)
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
        return access, refresh

    def _link_and_store_credential(self) -> str:
        self.accounts.link_account(self.principal.id, "1234567890", None)
        vault_ref = self.vault.store("real-google-refresh-token")
        self.credentials.upsert(self.principal.id, vault_ref, key_version=1)
        return vault_ref

    def test_disconnect_revokes_tokens_credential_and_accounts(self) -> None:
        access, refresh = self._issue_token_pair()
        vault_ref = self._link_and_store_credential()

        result = disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
        )

        self.assertTrue(result.credential_revoked)
        self.assertEqual(result.accounts_disconnected, 1)
        # Connector access token no longer resolves.
        self.assertIsNone(self.tokens.get_access(access.token))
        # Refresh token can no longer be rotated (revoked, not merely rotated).
        with self.assertRaises(Exception):
            self.tokens.rotate(refresh.token, now=NOW + timedelta(seconds=1))
        # Google credential reference is gone and the vault secret is destroyed.
        self.assertIsNone(self.credentials.get_active(self.principal.id))
        with self.assertRaises(VaultError):
            self.vault.read(vault_ref)
        # The linked account is marked disconnected, not deleted.
        account = self.accounts.get_account(self.principal.id, "1234567890")
        assert account is not None
        self.assertEqual(account.status, "disconnected")

    def test_disconnect_writes_one_audit_event(self) -> None:
        self._link_and_store_credential()
        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
        )
        events = self.audit.list_for_principal(self.principal.id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "principal.disconnected")
        self.assertEqual(events[0].outcome, "revoked")

    def test_disconnect_uses_supplied_correlation_id(self) -> None:
        self._link_and_store_credential()
        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
            correlation_id="support-corr-1",
        )
        events = self.audit.list_for_principal(self.principal.id)
        self.assertEqual(events[0].correlation_id, "support-corr-1")

    def test_disconnect_replaces_unsafe_correlation_id(self) -> None:
        self._link_and_store_credential()
        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
            correlation_id="bad value with spaces",
        )
        events = self.audit.list_for_principal(self.principal.id)
        self.assertRegex(events[0].correlation_id, r"^[A-Za-z0-9._-]{1,128}$")
        self.assertNotEqual(events[0].correlation_id, "bad value with spaces")

    def test_disconnect_is_idempotent(self) -> None:
        """A second disconnect (e.g. a double-submitted form) must not error."""
        self._link_and_store_credential()
        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
        )
        second = disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW + timedelta(seconds=1),
        )
        self.assertFalse(second.credential_revoked)
        self.assertEqual(len(self.audit.list_for_principal(self.principal.id)), 2)

    def test_disconnect_does_not_touch_other_principals(self) -> None:
        other = self.principals.get_or_create("https://accounts.google.com", "sub-2")
        self.accounts.link_account(other.id, "9999999999", None)
        other_vault_ref = self.vault.store("other-principals-refresh-token")
        self.credentials.upsert(other.id, other_vault_ref, key_version=1)
        self._link_and_store_credential()

        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
        )

        other_credential = self.credentials.get_active(other.id)
        assert other_credential is not None
        self.assertEqual(self.vault.read(other_vault_ref), "other-principals-refresh-token")
        other_account = self.accounts.get_account(other.id, "9999999999")
        assert other_account is not None
        self.assertEqual(other_account.status, "active")

    def test_disconnect_revokes_every_concurrent_web_session_for_principal(self) -> None:
        """A principal may be logged into /approvals from two browsers/devices at once.

        Disconnect is destructive and irreversible (docs/AUTH.md "Disconnect") -- it must
        not leave a second, already-issued browser session usable afterward.
        """
        self._link_and_store_credential()
        first = self.web_sessions.create(self.principal.id, "session-a", "csrf-a", NOW)
        second = self.web_sessions.create(self.principal.id, "session-b", "csrf-b", NOW)
        other = self.principals.get_or_create("https://accounts.google.com", "sub-2")
        other_session = self.web_sessions.create(other.id, "session-other", "csrf-other", NOW)

        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
            web_sessions=self.web_sessions,
        )

        self.assertTrue(self.web_sessions.lookup(first.token).revoked)
        self.assertTrue(self.web_sessions.lookup(second.token).revoked)
        # A different principal's concurrent session is untouched.
        self.assertFalse(self.web_sessions.lookup(other_session.token).revoked)

    def test_disconnect_without_web_sessions_argument_leaves_sessions_untouched(self) -> None:
        """``web_sessions`` is optional so non-browser callers aren't forced to supply one;
        when omitted, any existing web session for the principal is simply left alone."""
        self._link_and_store_credential()
        session = self.web_sessions.create(self.principal.id, "session-a", "csrf-a", NOW)

        disconnect_principal(
            self.principal.id,
            tokens=self.tokens,
            credentials=self.credentials,
            accounts=self.accounts,
            vault=self.vault,
            audit=self.audit,
            now=NOW,
        )

        self.assertFalse(self.web_sessions.lookup(session.token).revoked)


if __name__ == "__main__":
    unittest.main()
