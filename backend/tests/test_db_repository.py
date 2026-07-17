"""Tests for backend.src.db.repository (principal, ads_account, oauth_credential)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.db.connection import connect
from backend.src.db.repository import (
    AdsAccountRepository,
    OAuthCredentialRepository,
    PrincipalRepository,
)


class PrincipalRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)

    def test_get_or_create_is_idempotent(self) -> None:
        first = self.principals.get_or_create("https://issuer.example", "user-1")
        second = self.principals.get_or_create("https://issuer.example", "user-1")
        self.assertEqual(first.id, second.id)

    def test_different_subjects_get_different_principals(self) -> None:
        a = self.principals.get_or_create("https://issuer.example", "user-1")
        b = self.principals.get_or_create("https://issuer.example", "user-2")
        self.assertNotEqual(a.id, b.id)


class AdsAccountRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.accounts = AdsAccountRepository(self.conn)
        self.principal_a = self.principals.get_or_create("iss", "user-a")
        self.principal_b = self.principals.get_or_create("iss", "user-b")

    def test_link_account_is_idempotent(self) -> None:
        first = self.accounts.link_account(self.principal_a.id, "1234567890", None)
        second = self.accounts.link_account(self.principal_a.id, "1234567890", None)
        self.assertEqual(first.id, second.id)

    def test_cross_principal_read_returns_none(self) -> None:
        """IDOR koruması: TESTING.md madde 3 — customer_id ile cross-user erişim mümkün olmamalı."""
        self.accounts.link_account(self.principal_a.id, "1234567890", None)
        leaked = self.accounts.get_account(self.principal_b.id, "1234567890")
        self.assertIsNone(leaked)

    def test_list_accounts_only_returns_own_accounts(self) -> None:
        self.accounts.link_account(self.principal_a.id, "1111111111", None)
        self.accounts.link_account(self.principal_b.id, "2222222222", None)
        customer_ids = [a.customer_id for a in self.accounts.list_accounts(self.principal_a.id)]
        self.assertEqual(customer_ids, ["1111111111"])

    def test_active_accessors_hide_disconnected_rows_but_history_accessors_keep_them(self) -> None:
        self.accounts.link_account(self.principal_a.id, "1111111111", None)
        self.accounts.disconnect_all(self.principal_a.id)

        historical = self.accounts.get_account(self.principal_a.id, "1111111111")
        assert historical is not None
        self.assertEqual(historical.status, "disconnected")
        self.assertIsNone(self.accounts.get_active_account(self.principal_a.id, "1111111111"))
        self.assertEqual(self.accounts.list_active_accounts(self.principal_a.id), [])

    def test_relinking_disconnected_account_reactivates_existing_row(self) -> None:
        first = self.accounts.link_account(self.principal_a.id, "1111111111", None)
        self.accounts.disconnect_all(self.principal_a.id)

        relinked = self.accounts.link_account(self.principal_a.id, "1111111111", "9999999999")

        self.assertEqual(relinked.id, first.id)
        self.assertEqual(relinked.status, "active")
        self.assertEqual(relinked.login_customer_id, "9999999999")


class OAuthCredentialRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.credentials = OAuthCredentialRepository(self.conn)
        self.principal = self.principals.get_or_create("iss", "user-1")

    def test_upsert_revokes_previous_active_credential(self) -> None:
        first = self.credentials.upsert(self.principal.id, "vault://ref-1", key_version=1)
        second = self.credentials.upsert(self.principal.id, "vault://ref-2", key_version=1)
        self.assertEqual(self.credentials.get_active(self.principal.id).id, second.id)
        self.assertNotEqual(first.id, second.id)

    def test_no_active_credential_for_unknown_principal(self) -> None:
        self.assertIsNone(self.credentials.get_active("unknown-principal-id"))


if __name__ == "__main__":
    unittest.main()
