"""Tests for backend.src.db.web_session_store (the /approvals browser login/session store)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.web_session import hash_token
from backend.src.db.connection import connect
from backend.src.db.repository import PrincipalRepository
from backend.src.db.web_session_store import WebLoginStateRepository, WebSessionRepository

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


class WebLoginStateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.states = WebLoginStateRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_claim_succeeds_once(self) -> None:
        self.states.create("raw-state-1", NOW + timedelta(minutes=10))
        already_consumed, expires_at = self.states.claim("raw-state-1")
        self.assertFalse(already_consumed)
        self.assertEqual(expires_at, NOW + timedelta(minutes=10))

    def test_duplicate_claim_is_reported_as_already_consumed(self) -> None:
        """Zorunlu vaka: login state tek kullanımlıktır -- ikinci redeem denemesi fail-closed olmalı."""
        self.states.create("raw-state-1", NOW + timedelta(minutes=10))
        self.states.claim("raw-state-1")
        already_consumed, _ = self.states.claim("raw-state-1")
        self.assertTrue(already_consumed)

    def test_unknown_state_returns_none(self) -> None:
        self.assertIsNone(self.states.claim("never-issued"))


class WebSessionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.sessions = WebSessionRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_create_and_lookup_round_trip(self) -> None:
        principal = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        expires_at = NOW + timedelta(minutes=30)
        issued = self.sessions.create(principal.id, "raw-token-1", "raw-csrf-1", expires_at)
        self.assertEqual(issued.token, "raw-token-1")

        lookup = self.sessions.lookup("raw-token-1")
        self.assertEqual(lookup.principal_id, principal.id)
        self.assertEqual(lookup.csrf_token_hash, hash_token("raw-csrf-1"))
        self.assertEqual(lookup.expires_at, expires_at)
        self.assertFalse(lookup.revoked)
        stored = self.conn.execute("SELECT token_hash, csrf_token_hash FROM web_session").fetchone()
        self.assertEqual(stored["token_hash"], hash_token("raw-token-1"))
        self.assertEqual(stored["csrf_token_hash"], hash_token("raw-csrf-1"))
        self.assertNotEqual(stored["csrf_token_hash"], "raw-csrf-1")

    def test_lookup_of_unknown_token_is_fail_closed_shape(self) -> None:
        lookup = self.sessions.lookup("never-issued")
        self.assertIsNone(lookup.principal_id)
        self.assertIsNone(lookup.csrf_token_hash)
        self.assertIsNone(lookup.expires_at)
        self.assertFalse(lookup.revoked)

    def test_revoke_marks_session_revoked(self) -> None:
        principal = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        self.sessions.create(principal.id, "raw-token-1", "raw-csrf-1", NOW + timedelta(minutes=30))
        self.sessions.revoke("raw-token-1")
        lookup = self.sessions.lookup("raw-token-1")
        self.assertTrue(lookup.revoked)

    def test_two_principals_sessions_are_independent(self) -> None:
        """İzolasyon: bir principal'in session token'ı başka principal'a çözülemez (farklı token = farklı satır)."""
        principal_a = self.principals.get_or_create("https://accounts.google.com", "google-sub-a")
        principal_b = self.principals.get_or_create("https://accounts.google.com", "google-sub-b")
        self.sessions.create(principal_a.id, "token-a", "csrf-a", NOW + timedelta(minutes=30))
        self.sessions.create(principal_b.id, "token-b", "csrf-b", NOW + timedelta(minutes=30))

        self.assertEqual(self.sessions.lookup("token-a").principal_id, principal_a.id)
        self.assertEqual(self.sessions.lookup("token-b").principal_id, principal_b.id)


class PrincipalRepositoryGetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_get_returns_none_for_unknown_subject(self) -> None:
        """Login asla yeni principal yaratmaz -- yalnız get_or_create yaratır."""
        self.assertIsNone(self.principals.get("https://accounts.google.com", "never-connected"))

    def test_get_returns_existing_principal(self) -> None:
        created = self.principals.get_or_create("https://accounts.google.com", "google-sub-1")
        found = self.principals.get("https://accounts.google.com", "google-sub-1")
        assert found is not None
        self.assertEqual(found.id, created.id)
        self.assertEqual(found.issuer, created.issuer)
        self.assertEqual(found.subject, created.subject)


if __name__ == "__main__":
    unittest.main()
