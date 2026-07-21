"""Connector access/refresh token lifecycle tests (todo.md 3.4): TTL enforcement over a
real HTTP request, concurrent refresh rotation, disconnect revoking every client's
tokens for a principal in one call, and scope narrowing (a later, narrower
authorization never inherits a wider historical ``oauth_client_grant`` record).
Sequential rotation/reuse/TTL behaviour at the pure ``TokenRepository``/``auth.domain``
level is already covered by ``test_oauth_store.py`` and ``test_auth_domain.py``; this
file adds the boundary-crossing and true-concurrency cases those unit-level suites
cannot exercise.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import httpx
from backend.src.app import create_app
from backend.src.auth.domain import AccessToken as DomainAccessToken
from backend.src.auth.domain import (
    AuthError,
    AuthorizationTransaction,
    ClientIdentity,
    compute_code_challenge,
    consent_transaction,
    consume_authorization_code,
    issue_authorization_code,
    issue_token_pair,
)
from backend.src.auth.google_oauth import FakeGoogleOAuthClient
from backend.src.config import Settings
from backend.src.db.connection import connect
from backend.src.db.oauth_store import (
    AuthorizationCodeRepository,
    AuthorizationTransactionRepository,
    ClientGrantRepository,
    TokenRepository,
)
from backend.src.db.repository import PrincipalRepository
from cryptography.fernet import Fernet

PUBLIC_BASE_URL = "https://connector.example.com"
NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
MCP_RESOURCE = f"{PUBLIC_BASE_URL}/mcp"
CLIENT = ClientIdentity(
    client_id="https://claude.ai/oauth/hosted-client-metadata",
    redirect_uris=("https://claude.ai/api/mcp/auth_callback",),
    token_endpoint_auth_method="none",
)


def _settings() -> Settings:
    return Settings(
        sqlite_db_path=":memory:",
        environment="test",
        public_base_url=PUBLIC_BASE_URL,
        mcp_resource_path="/mcp",
        local_vault_key=Fernet.generate_key().decode(),
        google_client_id="client-id",
        google_client_secret="client-secret",
        google_ads_developer_token="dev-token",
        allowed_hosts=("connector.example.com",),
        cors_allowed_origins=(),
    )


def _make_transaction(transaction_id: str) -> tuple[str, AuthorizationTransaction]:
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
        consent_csrf_hash="test-consent-csrf-hash",
        now=NOW,
    )
    return verifier, txn


def _issue_pair(conn, *, principal_sub: str, transaction_id: str):
    principal = PrincipalRepository(conn).get_or_create(
        "https://accounts.google.com", principal_sub
    )
    verifier, txn = _make_transaction(transaction_id)
    AuthorizationTransactionRepository(conn).save(txn)
    consented = consent_transaction(txn, now=NOW)
    code = issue_authorization_code(consented, principal_id=principal.id, now=NOW)
    codes = AuthorizationCodeRepository(conn)
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
    tokens = TokenRepository(conn)
    tokens.save_access(access)
    tokens.save_refresh(refresh)
    return principal, access, refresh


class AccessTokenExpiryOverHttpTests(unittest.IsolatedAsyncioTestCase):
    """The 600s access-token TTL (auth/domain.py::ACCESS_TOKEN_TTL_SECONDS) is asserted at
    the pure-function level already; nothing previously drove an *expired* token through a
    live request to confirm ``auth.deps.verify_access_token`` actually rejects it at the
    boundary every protected route depends on."""

    async def test_expired_access_token_is_rejected_with_401(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            conn = app.state.auth_context.conn
            principal = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "expiry-sub"
            )
            TokenRepository(conn).save_access(
                DomainAccessToken(
                    token="already-expired-token",
                    principal_id=principal.id,
                    client_id=CLIENT.client_id,
                    resource=MCP_RESOURCE,
                    scope="adwords",
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )

            response = await client.get(
                "/api/v1/accounts",
                headers={"Authorization": "Bearer already-expired-token"},
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_token")

    async def test_not_yet_expired_access_token_is_accepted(self) -> None:
        app = create_app(_settings(), google_client=FakeGoogleOAuthClient())

        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url=PUBLIC_BASE_URL
            ) as client,
        ):
            conn = app.state.auth_context.conn
            principal = PrincipalRepository(conn).get_or_create(
                "https://accounts.google.com", "fresh-sub"
            )
            TokenRepository(conn).save_access(
                DomainAccessToken(
                    token="still-valid-token",
                    principal_id=principal.id,
                    client_id=CLIENT.client_id,
                    resource=MCP_RESOURCE,
                    scope="adwords",
                    expires_at=datetime.now(UTC) + timedelta(seconds=1),
                )
            )

            response = await client.get(
                "/api/v1/accounts",
                headers={"Authorization": "Bearer still-valid-token"},
            )

        self.assertEqual(response.status_code, 200)


class DisconnectRevokesAllClientsTests(unittest.TestCase):
    """docs/AUTH.md's disconnect guarantee ("gelecek erisimi durdurabilir") must hold across
    every client_id a principal has ever authorized, not just the most recent one."""

    def test_disconnect_revokes_token_families_from_every_client(self) -> None:
        from backend.src.auth.disconnect import disconnect_principal
        from backend.src.auth.vault import LocalEncryptedVault
        from backend.src.db.proposals import AuditRepository
        from backend.src.db.repository import AdsAccountRepository, OAuthCredentialRepository

        conn = connect(":memory:")
        principal = PrincipalRepository(conn).get_or_create(
            "https://accounts.google.com", "multi-client-sub"
        )
        verifier_a = "a" * 43
        txn_a = AuthorizationTransaction.create(
            transaction_id="txn-a",
            client=CLIENT,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge=compute_code_challenge(verifier_a),
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",
            client_state="state-a",
            consent_csrf_hash="csrf-a",
            now=NOW,
        )
        other_client = ClientIdentity(
            client_id="https://other-client.example.com/metadata.json",
            redirect_uris=("https://other-client.example.com/callback",),
            token_endpoint_auth_method="none",
        )
        verifier_b = "b" * 43
        txn_b = AuthorizationTransaction.create(
            transaction_id="txn-b",
            client=other_client,
            redirect_uri="https://other-client.example.com/callback",
            code_challenge=compute_code_challenge(verifier_b),
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",
            client_state="state-b",
            consent_csrf_hash="csrf-b",
            now=NOW,
        )
        transactions = AuthorizationTransactionRepository(conn)
        codes = AuthorizationCodeRepository(conn)
        tokens = TokenRepository(conn)
        refreshes = []
        pairs = ((txn_a, CLIENT, verifier_a), (txn_b, other_client, verifier_b))
        for txn, client, verifier in pairs:
            transactions.save(txn)
            consented = consent_transaction(txn, now=NOW)
            code = issue_authorization_code(consented, principal_id=principal.id, now=NOW)
            codes.save(code)
            stored, already_consumed = codes.claim(code.code)
            grant = consume_authorization_code(
                stored,
                client_id=client.client_id,
                redirect_uri=code.redirect_uri,
                resource=MCP_RESOURCE,
                code_verifier=verifier,
                already_consumed=already_consumed,
                now=NOW,
            )
            access, refresh = issue_token_pair(grant, now=NOW)
            tokens.save_access(access)
            tokens.save_refresh(refresh)
            refreshes.append(refresh)

        vault = LocalEncryptedVault(conn, Fernet.generate_key())
        disconnect_principal(
            principal.id,
            tokens=tokens,
            credentials=OAuthCredentialRepository(conn),
            accounts=AdsAccountRepository(conn),
            vault=vault,
            audit=AuditRepository(conn),
            now=NOW,
        )

        for refresh in refreshes:
            with self.assertRaises(AuthError):
                tokens.rotate(refresh.token, now=NOW + timedelta(seconds=1))


class ConcurrentRefreshRotationTests(unittest.TestCase):
    """Zorunlu vaka (todo.md 3.4): iki eşzamanlı çağrı aynı hâlâ-aktif refresh_token'ı
    rotate etmeye çalışırsa yalnız biri başarılı olmalı, diğeri reuse olarak reddedilip
    TÜM aileyi iptal etmelidir -- ``ConcurrentAuthorizationCodeClaimTests``'in
    (test_oauth_store.py, todo.md 3.3) authorization_code için kanıtladığı atomiklik
    garantisinin refresh_token eşdeğeri."""

    def test_concurrent_rotation_of_the_same_refresh_token_only_succeeds_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "refresh-race.db"
            setup_conn = connect(str(db_path))
            _, _, refresh = _issue_pair(
                setup_conn, principal_sub="race-sub", transaction_id="txn-refresh-race"
            )
            setup_conn.close()

            outcomes: list[str] = []
            outcomes_lock = threading.Lock()
            start_barrier = threading.Barrier(2)

            def _attempt_rotate() -> None:
                conn = connect(str(db_path))
                conn.execute("PRAGMA busy_timeout = 5000")
                try:
                    start_barrier.wait()
                    try:
                        TokenRepository(conn).rotate(refresh.token, now=NOW + timedelta(seconds=5))
                        result = "success"
                    except AuthError:
                        result = "rejected"
                finally:
                    conn.close()
                with outcomes_lock:
                    outcomes.append(result)

            threads = [threading.Thread(target=_attempt_rotate) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(sorted(outcomes), ["rejected", "success"])

            # Reuse detection must fail the whole family closed -- including whichever
            # sibling token the winning caller was just issued.
            verify_conn = connect(str(db_path))
            verify_conn.execute("PRAGMA busy_timeout = 5000")
            still_active = verify_conn.execute(
                "SELECT COUNT(*) AS n FROM refresh_token WHERE family_id = ? AND status = 'active'",
                (refresh.family_id,),
            ).fetchone()["n"]
            verify_conn.close()
            self.assertEqual(still_active, 0)


class ScopeNarrowingTests(unittest.TestCase):
    """``ClientGrantRepository`` (db/oauth_store.py) only ever records that a principal
    consented to a client_id+scope at some point -- ``has_active_grant`` is not consulted
    anywhere in the token-issuance path (verified by reading auth/server.py: the only
    call site is ``record_consent`` after a successful Google exchange). This means a
    later, narrower authorization can never silently inherit a wider historical scope;
    every issued token's scope comes exclusively from the *current* transaction's
    explicit consent. This test proves that structurally, not just by absence of a
    call site."""

    def test_a_narrower_re_authorization_does_not_inherit_a_wider_prior_grant(self) -> None:
        conn = connect(":memory:")
        principal = PrincipalRepository(conn).get_or_create(
            "https://accounts.google.com", "narrowing-sub"
        )
        grants = ClientGrantRepository(conn)
        # A prior, wider consent is already on record for this principal+client.
        grants.record_consent(principal.id, CLIENT.client_id, "adwords openid profile")
        self.assertTrue(grants.has_active_grant(principal.id, CLIENT.client_id))

        verifier = "n" * 43
        txn = AuthorizationTransaction.create(
            transaction_id="txn-narrow",
            client=CLIENT,
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            code_challenge=compute_code_challenge(verifier),
            code_challenge_method="S256",
            resource=MCP_RESOURCE,
            expected_resource=MCP_RESOURCE,
            scope="adwords",  # narrower than the prior recorded grant
            client_state="state-narrow",
            consent_csrf_hash="csrf-narrow",
            now=NOW,
        )
        transactions = AuthorizationTransactionRepository(conn)
        transactions.save(txn)
        consented = consent_transaction(txn, now=NOW)
        code = issue_authorization_code(consented, principal_id=principal.id, now=NOW)
        codes = AuthorizationCodeRepository(conn)
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

        self.assertEqual(access.scope, "adwords")
        self.assertEqual(refresh.scope, "adwords")


if __name__ == "__main__":
    unittest.main()
