"""Pure-logic tests for backend.src.auth.domain -- no sqlite, no network, no FastAPI."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.domain import (
    AuthError,
    AuthorizationCode,
    AuthorizationTransaction,
    ClientIdentity,
    RefreshToken,
    RefreshTokenStatus,
    TransactionStatus,
    complete_transaction,
    compute_code_challenge,
    consent_transaction,
    consume_authorization_code,
    issue_authorization_code,
    issue_token_pair,
    redirect_uri_allowed,
    rotate_refresh_token,
    validate_cimd_document,
    verify_pkce,
)

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
MCP_RESOURCE = "https://mcp.example.com/mcp"

CLAUDE_CODE_CLIENT_ID = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE_CODE_CLIENT = ClientIdentity(
    client_id=CLAUDE_CODE_CLIENT_ID,
    redirect_uris=("http://localhost/callback", "http://127.0.0.1/callback"),
    token_endpoint_auth_method="none",
)

WEB_CLIENT_ID = "https://claude.ai/oauth/hosted-client-metadata"
WEB_CLIENT = ClientIdentity(
    client_id=WEB_CLIENT_ID,
    redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
    token_endpoint_auth_method="none",
)


def _pkce_pair() -> tuple[str, str]:
    verifier = "a" * 43
    return verifier, compute_code_challenge(verifier)


class PkceTests(unittest.TestCase):
    def test_matching_verifier_passes(self) -> None:
        verifier, challenge = _pkce_pair()
        self.assertTrue(verify_pkce(verifier, challenge, "S256"))

    def test_wrong_verifier_fails(self) -> None:
        _, challenge = _pkce_pair()
        self.assertFalse(verify_pkce("wrong-verifier-xxxxxxxxxxxxxxxxxxxxxxxx", challenge, "S256"))

    def test_plain_method_rejected(self) -> None:
        """Only S256 is accepted (SECURITY.md, MCP authorization spec)."""
        verifier, _ = _pkce_pair()
        self.assertFalse(verify_pkce(verifier, verifier, "plain"))


class RedirectUriTests(unittest.TestCase):
    def test_exact_match(self) -> None:
        self.assertTrue(
            redirect_uri_allowed(
                "https://claude.ai/api/mcp/auth_callback", "https://claude.ai/api/mcp/auth_callback"
            )
        )

    def test_loopback_port_ignored(self) -> None:
        """Claude Code binds an ephemeral port at runtime (RFC 8252 s7.3)."""
        self.assertTrue(redirect_uri_allowed("http://127.0.0.1:54321/callback", "http://127.0.0.1/callback"))
        self.assertTrue(redirect_uri_allowed("http://localhost:9999/callback", "http://localhost/callback"))

    def test_loopback_path_mismatch_rejected(self) -> None:
        self.assertFalse(redirect_uri_allowed("http://127.0.0.1:54321/other", "http://127.0.0.1/callback"))

    def test_non_loopback_port_mismatch_rejected(self) -> None:
        """Only loopback hosts get the port exception -- no general port laxity."""
        self.assertFalse(redirect_uri_allowed("https://evil.example.com:9/callback", "https://evil.example.com/callback"))

    def test_cross_loopback_host_rejected(self) -> None:
        self.assertFalse(redirect_uri_allowed("http://localhost/callback", "http://127.0.0.1/callback"))


class CimdValidationTests(unittest.TestCase):
    def test_valid_hosted_document(self) -> None:
        identity = validate_cimd_document(
            WEB_CLIENT_ID,
            {
                "client_id": WEB_CLIENT_ID,
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        self.assertEqual(identity.client_id, WEB_CLIENT_ID)

    def test_valid_claude_code_document_allows_loopback(self) -> None:
        identity = validate_cimd_document(
            CLAUDE_CODE_CLIENT_ID,
            {
                "client_id": CLAUDE_CODE_CLIENT_ID,
                "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
                "token_endpoint_auth_method": "none",
            },
        )
        self.assertEqual(identity.redirect_uris, ("http://localhost/callback", "http://127.0.0.1/callback"))

    def test_non_self_referential_document_rejected(self) -> None:
        with self.assertRaises(AuthError) as ctx:
            validate_cimd_document(
                WEB_CLIENT_ID,
                {"client_id": "https://attacker.example.com/cimd", "redirect_uris": ["https://claude.ai/x"]},
            )
        self.assertEqual(ctx.exception.code, "invalid_client")

    def test_cross_origin_redirect_uri_rejected(self) -> None:
        """A non-loopback redirect_uri must be same-origin with the client_id URL."""
        with self.assertRaises(AuthError) as ctx:
            validate_cimd_document(
                WEB_CLIENT_ID,
                {
                    "client_id": WEB_CLIENT_ID,
                    "redirect_uris": ["https://attacker.example.com/callback"],
                    "token_endpoint_auth_method": "none",
                },
            )
        self.assertEqual(ctx.exception.code, "invalid_client")

    def test_confidential_auth_method_rejected(self) -> None:
        """CIMD clients must be public (none); Claude's CIMD client always is."""
        with self.assertRaises(AuthError):
            validate_cimd_document(
                WEB_CLIENT_ID,
                {
                    "client_id": WEB_CLIENT_ID,
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                    "token_endpoint_auth_method": "client_secret_basic",
                },
            )


class TransactionLifecycleTests(unittest.TestCase):
    def _create(self, **overrides):
        verifier, challenge = _pkce_pair()
        params = dict(
            transaction_id="txn-1",
            client=WEB_CLIENT,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge=challenge,
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",
            client_state="client-opaque-state",
            now=NOW,
        )
        params.update(overrides)
        return params["transaction_id"], verifier, AuthorizationTransaction.create(**params)

    def test_happy_path_creates_pending_transaction(self) -> None:
        _, _, txn = self._create()
        self.assertEqual(txn.status, TransactionStatus.PENDING)

    def test_resource_mismatch_rejected(self) -> None:
        with self.assertRaises(AuthError) as ctx:
            self._create(resource="https://other.example.com/mcp")
        self.assertEqual(ctx.exception.code, "invalid_target")

    def test_unregistered_redirect_uri_rejected(self) -> None:
        with self.assertRaises(AuthError):
            self._create(redirect_uri="https://not-registered.example.com/callback")

    def test_plain_challenge_method_rejected(self) -> None:
        with self.assertRaises(AuthError):
            self._create(code_challenge_method="plain")

    def test_full_authorization_code_round_trip(self) -> None:
        _, verifier, txn = self._create()
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id="principal-1", now=NOW)
        complete_transaction(consented, now=NOW)  # does not raise

        grant = consume_authorization_code(
            code,
            client_id=WEB_CLIENT_ID,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            resource=MCP_RESOURCE,
            code_verifier=verifier,
            already_consumed=False,
            now=NOW,
        )
        self.assertEqual(grant.principal_id, "principal-1")
        self.assertEqual(grant.client_id, WEB_CLIENT_ID)

    def test_code_cannot_be_issued_before_consent(self) -> None:
        _, _, txn = self._create()
        with self.assertRaises(AuthError):
            issue_authorization_code(txn, principal_id="principal-1", now=NOW)

    def test_expired_transaction_rejects_consent(self) -> None:
        _, _, txn = self._create()
        later = NOW + timedelta(seconds=10_000)
        with self.assertRaises(AuthError):
            consent_transaction(txn, now=later)


class AuthorizationCodeConsumptionTests(unittest.TestCase):
    def _issue_code(self) -> tuple[str, AuthorizationCode]:
        verifier, challenge = _pkce_pair()
        txn = AuthorizationTransaction.create(
            transaction_id="txn-2",
            client=WEB_CLIENT,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge=challenge,
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",
            client_state="state",
            now=NOW,
        )
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id="principal-1", now=NOW)
        return verifier, code

    def test_already_consumed_is_rejected(self) -> None:
        verifier, code = self._issue_code()
        with self.assertRaises(AuthError) as ctx:
            consume_authorization_code(
                code,
                client_id=WEB_CLIENT_ID,
                redirect_uri=code.redirect_uri,
                resource=MCP_RESOURCE,
                code_verifier=verifier,
                already_consumed=True,
                now=NOW,
            )
        self.assertEqual(ctx.exception.code, "invalid_grant")

    def test_expired_code_rejected(self) -> None:
        verifier, code = self._issue_code()
        with self.assertRaises(AuthError):
            consume_authorization_code(
                code,
                client_id=WEB_CLIENT_ID,
                redirect_uri=code.redirect_uri,
                resource=MCP_RESOURCE,
                code_verifier=verifier,
                already_consumed=False,
                now=NOW + timedelta(seconds=120),
            )

    def test_wrong_pkce_verifier_rejected(self) -> None:
        _, code = self._issue_code()
        with self.assertRaises(AuthError):
            consume_authorization_code(
                code,
                client_id=WEB_CLIENT_ID,
                redirect_uri=code.redirect_uri,
                resource=MCP_RESOURCE,
                code_verifier="wrong" * 10,
                already_consumed=False,
                now=NOW,
            )

    def test_wrong_client_rejected(self) -> None:
        """A code minted for one client cannot be redeemed by another (cross-client theft)."""
        verifier, code = self._issue_code()
        with self.assertRaises(AuthError) as ctx:
            consume_authorization_code(
                code,
                client_id="https://someone-else.example.com/cimd",
                redirect_uri=code.redirect_uri,
                resource=MCP_RESOURCE,
                code_verifier=verifier,
                already_consumed=False,
                now=NOW,
            )
        self.assertEqual(ctx.exception.code, "invalid_client")

    def test_wrong_redirect_uri_rejected(self) -> None:
        verifier, code = self._issue_code()
        with self.assertRaises(AuthError):
            consume_authorization_code(
                code,
                client_id=WEB_CLIENT_ID,
                redirect_uri="https://claude.ai/somewhere-else",
                resource=MCP_RESOURCE,
                code_verifier=verifier,
                already_consumed=False,
                now=NOW,
            )


class RefreshRotationTests(unittest.TestCase):
    def _issue_pair(self):
        verifier, challenge = _pkce_pair()
        txn = AuthorizationTransaction.create(
            transaction_id="txn-3",
            client=WEB_CLIENT,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge=challenge,
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",
            client_state="state",
            now=NOW,
        )
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id="principal-1", now=NOW)
        grant = consume_authorization_code(
            code,
            client_id=WEB_CLIENT_ID,
            redirect_uri=code.redirect_uri,
            resource=MCP_RESOURCE,
            code_verifier=verifier,
            already_consumed=False,
            now=NOW,
        )
        return issue_token_pair(grant, now=NOW)

    def test_active_refresh_token_rotates(self) -> None:
        _, refresh = self._issue_pair()
        outcome = rotate_refresh_token(refresh, now=NOW + timedelta(seconds=5))
        self.assertNotEqual(outcome.refresh_token.token, refresh.token)
        self.assertEqual(outcome.refresh_token.family_id, refresh.family_id)

    def test_expired_refresh_token_rejected(self) -> None:
        _, refresh = self._issue_pair()
        far_future = NOW + timedelta(days=365)
        with self.assertRaises(AuthError):
            rotate_refresh_token(refresh, now=far_future)

    def test_rotated_status_refuses_reuse(self) -> None:
        """A refresh token already marked ROTATED (replay) must fail closed."""
        _, refresh = self._issue_pair()
        reused = RefreshToken(
            token=refresh.token,
            family_id=refresh.family_id,
            principal_id=refresh.principal_id,
            client_id=refresh.client_id,
            resource=refresh.resource,
            scope=refresh.scope,
            expires_at=refresh.expires_at,
            status=RefreshTokenStatus.ROTATED,
        )
        with self.assertRaises(AuthError) as ctx:
            rotate_refresh_token(reused, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_grant")

    def test_revoked_status_refuses_use(self) -> None:
        _, refresh = self._issue_pair()
        revoked = RefreshToken(
            token=refresh.token,
            family_id=refresh.family_id,
            principal_id=refresh.principal_id,
            client_id=refresh.client_id,
            resource=refresh.resource,
            scope=refresh.scope,
            expires_at=refresh.expires_at,
            status=RefreshTokenStatus.REVOKED,
        )
        with self.assertRaises(AuthError):
            rotate_refresh_token(revoked, now=NOW)


if __name__ == "__main__":
    unittest.main()
