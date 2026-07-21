"""Credential-resolution tests (docs/SECURITY.md -- account ownership + vault gates)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.errors import AdsApiError, ErrorClass
from backend.src.auth.vault import LocalEncryptedVault
from backend.src.config import Settings
from backend.src.db.connection import connect
from backend.src.db.repository import (
    AdsAccountRepository,
    OAuthCredentialRepository,
    PrincipalRepository,
)
from backend.src.mcp.credentials import (
    deactivate_credential_on_auth_failure,
    resolve_google_ads_credentials,
    resolve_principal_google_ads_credentials,
)
from cryptography.fernet import Fernet


class ResolveGoogleAdsCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.vault = LocalEncryptedVault(self.conn, Fernet.generate_key())
        self.accounts = AdsAccountRepository(self.conn)
        self.oauth_credentials = OAuthCredentialRepository(self.conn)
        self.settings = Settings(
            sqlite_db_path=":memory:",
            environment="test",
            public_base_url="https://connector.example.com",
            mcp_resource_path="/mcp",
            local_vault_key=None,
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_ads_developer_token="dev-token",
            allowed_hosts=("connector.example.com",),
            cors_allowed_origins=(),
        )
        self.principal = PrincipalRepository(self.conn).get_or_create(
            "https://accounts.google.com", "sub-1"
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _resolve(self, *, principal_id: str, customer_id: str):
        return resolve_google_ads_credentials(
            principal_id=principal_id,
            customer_id=customer_id,
            settings=self.settings,
            accounts=self.accounts,
            oauth_credentials=self.oauth_credentials,
            vault=self.vault,
        )

    def test_resolves_full_credentials_for_linked_account(self) -> None:
        self.accounts.link_account(self.principal.id, "1234567890", "1112223333")
        vault_ref = self.vault.store("google-refresh-token")
        self.oauth_credentials.upsert(self.principal.id, vault_ref, key_version=1)

        credentials = self._resolve(principal_id=self.principal.id, customer_id="1234567890")

        self.assertEqual(credentials.refresh_token, "google-refresh-token")
        self.assertEqual(credentials.login_customer_id, "1112223333")
        self.assertEqual(credentials.developer_token, "dev-token")
        self.assertEqual(credentials.client_id, "client-id")
        self.assertEqual(credentials.client_secret, "client-secret")

    def test_unlinked_customer_id_is_rejected(self) -> None:
        with self.assertRaises(AdsApiError) as ctx:
            self._resolve(principal_id=self.principal.id, customer_id="9999999999")
        self.assertEqual(ctx.exception.code, "account_not_linked")
        self.assertEqual(ctx.exception.error_class, ErrorClass.VALIDATION)

    def test_customer_id_linked_to_another_principal_is_rejected(self) -> None:
        other = PrincipalRepository(self.conn).get_or_create("https://accounts.google.com", "sub-2")
        self.accounts.link_account(other.id, "1234567890", None)

        with self.assertRaises(AdsApiError) as ctx:
            self._resolve(principal_id=self.principal.id, customer_id="1234567890")
        self.assertEqual(ctx.exception.code, "account_not_linked")

    def test_disconnected_customer_id_is_rejected_even_if_history_row_exists(self) -> None:
        self.accounts.link_account(self.principal.id, "1234567890", None)
        self.accounts.disconnect_all(self.principal.id)

        with self.assertRaises(AdsApiError) as ctx:
            self._resolve(principal_id=self.principal.id, customer_id="1234567890")
        self.assertEqual(ctx.exception.code, "account_not_linked")

    def test_no_active_google_credential_is_rejected(self) -> None:
        self.accounts.link_account(self.principal.id, "1234567890", None)

        with self.assertRaises(AdsApiError) as ctx:
            self._resolve(principal_id=self.principal.id, customer_id="1234567890")
        self.assertEqual(ctx.exception.code, "no_active_google_credential")
        self.assertEqual(ctx.exception.error_class, ErrorClass.AUTH)

    def test_revoked_credential_is_rejected(self) -> None:
        self.accounts.link_account(self.principal.id, "1234567890", None)
        vault_ref = self.vault.store("google-refresh-token")
        credential = self.oauth_credentials.upsert(self.principal.id, vault_ref, key_version=1)
        self.oauth_credentials.revoke(self.principal.id, credential.id)

        with self.assertRaises(AdsApiError) as ctx:
            self._resolve(principal_id=self.principal.id, customer_id="1234567890")
        self.assertEqual(ctx.exception.code, "no_active_google_credential")

    def test_unreadable_vault_reference_is_rejected_safely(self) -> None:
        self.accounts.link_account(self.principal.id, "1234567890", None)
        # A credential row pointing at a vault_ref that was never stored (or was revoked).
        self.oauth_credentials.upsert(self.principal.id, "missing-vault-ref", key_version=1)

        with self.assertRaises(AdsApiError) as ctx:
            self._resolve(principal_id=self.principal.id, customer_id="1234567890")
        self.assertEqual(ctx.exception.code, "credential_unreadable")
        self.assertEqual(ctx.exception.error_class, ErrorClass.AUTH)
        self.assertNotIn("missing-vault-ref", ctx.exception.message)

    def test_account_discovery_resolves_only_authenticated_principal_credential(self) -> None:
        other = PrincipalRepository(self.conn).get_or_create("https://accounts.google.com", "sub-2")
        other_ref = self.vault.store("other-refresh-token")
        self.oauth_credentials.upsert(other.id, other_ref, key_version=1)
        owner_ref = self.vault.store("owner-refresh-token")
        self.oauth_credentials.upsert(self.principal.id, owner_ref, key_version=1)

        credentials = resolve_principal_google_ads_credentials(
            principal_id=self.principal.id,
            settings=self.settings,
            oauth_credentials=self.oauth_credentials,
            vault=self.vault,
        )

        self.assertEqual(credentials.refresh_token, "owner-refresh-token")
        self.assertNotEqual(credentials.refresh_token, "other-refresh-token")
        self.assertIsNone(credentials.login_customer_id)

    def test_account_discovery_cannot_fall_back_to_another_principals_credential(self) -> None:
        other = PrincipalRepository(self.conn).get_or_create("https://accounts.google.com", "sub-2")
        other_ref = self.vault.store("other-refresh-token")
        self.oauth_credentials.upsert(other.id, other_ref, key_version=1)

        with self.assertRaises(AdsApiError) as caught:
            resolve_principal_google_ads_credentials(
                principal_id=self.principal.id,
                settings=self.settings,
                oauth_credentials=self.oauth_credentials,
                vault=self.vault,
            )

        self.assertEqual(caught.exception.code, "no_active_google_credential")


class DeactivateCredentialOnAuthFailureTests(unittest.TestCase):
    """ERROR_HANDLING.md 'Auth' row (todo.md 3.6): 'Credential pasifleştir, işleri
    durdur'."""

    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.oauth_credentials = OAuthCredentialRepository(self.conn)
        self.vault = LocalEncryptedVault(self.conn, Fernet.generate_key())
        self.principal = PrincipalRepository(self.conn).get_or_create(
            "https://accounts.google.com", "sub-deactivate"
        )
        vault_ref = self.vault.store("google-refresh-token")
        self.oauth_credentials.upsert(self.principal.id, vault_ref, key_version=1)

    def tearDown(self) -> None:
        self.conn.close()

    def _error(self, error_class: ErrorClass) -> AdsApiError:
        return AdsApiError(
            error_class=error_class, code="some_code", message="msg", request_id=None
        )

    def test_auth_class_failure_deactivates_the_active_credential(self) -> None:
        deactivate_credential_on_auth_failure(
            self._error(ErrorClass.AUTH),
            principal_id=self.principal.id,
            oauth_credentials=self.oauth_credentials,
        )

        self.assertIsNone(self.oauth_credentials.get_active(self.principal.id))

    def test_auth_class_failure_leaves_the_vault_secret_intact(self) -> None:
        """Deactivation is a pause, not the irreversible destroy disconnect performs
        (docs/SECURITY.md 'pasifleştirilir' vs docs/AUTH.md 'Disconnect')."""
        credential = self.oauth_credentials.get_active(self.principal.id)
        assert credential is not None

        deactivate_credential_on_auth_failure(
            self._error(ErrorClass.AUTH),
            principal_id=self.principal.id,
            oauth_credentials=self.oauth_credentials,
        )

        self.assertEqual(self.vault.read(credential.vault_ref), "google-refresh-token")

    def test_non_auth_failure_leaves_the_credential_active(self) -> None:
        for error_class in (
            ErrorClass.VALIDATION,
            ErrorClass.RATE_LIMIT,
            ErrorClass.TRANSIENT,
            ErrorClass.SYNC_STALE,
        ):
            with self.subTest(error_class=error_class):
                deactivate_credential_on_auth_failure(
                    self._error(error_class),
                    principal_id=self.principal.id,
                    oauth_credentials=self.oauth_credentials,
                )
                self.assertIsNotNone(self.oauth_credentials.get_active(self.principal.id))

    def test_subsequent_resolve_fails_fast_without_reaching_google(self) -> None:
        """The whole point: after one AUTH failure, every later call must hit the
        cheap ``no_active_google_credential`` branch instead of retrying Google with a
        token already known to be bad (todo.md 3.6 -- 'sonsuz retry yapma')."""
        deactivate_credential_on_auth_failure(
            self._error(ErrorClass.AUTH),
            principal_id=self.principal.id,
            oauth_credentials=self.oauth_credentials,
        )

        accounts = AdsAccountRepository(self.conn)
        accounts.link_account(self.principal.id, "1234567890", None)
        settings = Settings(
            sqlite_db_path=":memory:",
            environment="test",
            public_base_url="https://connector.example.com",
            mcp_resource_path="/mcp",
            local_vault_key=None,
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_ads_developer_token="dev-token",
            allowed_hosts=("connector.example.com",),
            cors_allowed_origins=(),
        )

        with self.assertRaises(AdsApiError) as ctx:
            resolve_google_ads_credentials(
                principal_id=self.principal.id,
                customer_id="1234567890",
                settings=settings,
                accounts=accounts,
                oauth_credentials=self.oauth_credentials,
                vault=self.vault,
            )
        self.assertEqual(ctx.exception.code, "no_active_google_credential")


if __name__ == "__main__":
    unittest.main()
