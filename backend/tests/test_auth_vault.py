"""Tests for backend.src.auth.vault.LocalEncryptedVault (local/dev-only secret storage)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.vault import LocalEncryptedVault, VaultError
from backend.src.db.connection import connect
from cryptography.fernet import Fernet


class LocalEncryptedVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.vault = LocalEncryptedVault(self.conn, Fernet.generate_key())

    def test_store_and_read_round_trip(self) -> None:
        vault_ref = self.vault.store("google-refresh-token-value")
        self.assertEqual(self.vault.read(vault_ref), "google-refresh-token-value")

    def test_ciphertext_is_not_the_plaintext(self) -> None:
        vault_ref = self.vault.store("super-secret-refresh-token")
        row = self.conn.execute(
            "SELECT ciphertext FROM vault_secret WHERE vault_ref = ?", (vault_ref,)
        ).fetchone()
        self.assertNotIn(b"super-secret-refresh-token", bytes(row["ciphertext"]))

    def test_revoked_secret_cannot_be_read(self) -> None:
        vault_ref = self.vault.store("value")
        self.vault.revoke(vault_ref)
        with self.assertRaises(VaultError):
            self.vault.read(vault_ref)

    def test_unknown_ref_raises(self) -> None:
        with self.assertRaises(VaultError):
            self.vault.read("does-not-exist")

    def test_wrong_key_cannot_decrypt(self) -> None:
        vault_ref = self.vault.store("value")
        other_vault = LocalEncryptedVault(self.conn, Fernet.generate_key())
        with self.assertRaises(VaultError):
            other_vault.read(vault_ref)


if __name__ == "__main__":
    unittest.main()
