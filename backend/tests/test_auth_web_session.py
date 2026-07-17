"""Pure-logic tests for backend.src.auth.web_session -- no sqlite, no network, no FastAPI."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.domain import AuthError
from backend.src.auth.web_session import (
    issue_login_state,
    issue_web_session,
    redeem_login_state,
    verify_csrf_token,
    verify_web_session,
)

NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)


class LoginStateTests(unittest.TestCase):
    def test_issue_produces_unpredictable_state(self) -> None:
        first = issue_login_state(now=NOW)
        second = issue_login_state(now=NOW)
        self.assertNotEqual(first.state, second.state)
        self.assertGreater(first.expires_at, NOW)

    def test_redeem_succeeds_when_not_consumed_and_not_expired(self) -> None:
        state = issue_login_state(now=NOW)
        redeem_login_state(already_consumed=False, expires_at=state.expires_at, now=NOW)

    def test_redeem_rejects_replay(self) -> None:
        """Zorunlu vaka: login state tek kullanımlıktır -- ikinci redeem denemesi fail-closed olmalı."""
        state = issue_login_state(now=NOW)
        with self.assertRaises(AuthError) as ctx:
            redeem_login_state(already_consumed=True, expires_at=state.expires_at, now=NOW)
        self.assertEqual(ctx.exception.code, "invalid_grant")

    def test_redeem_rejects_expired_state(self) -> None:
        state = issue_login_state(now=NOW)
        later = state.expires_at + timedelta(seconds=1)
        with self.assertRaises(AuthError):
            redeem_login_state(already_consumed=False, expires_at=state.expires_at, now=later)


class WebSessionTests(unittest.TestCase):
    def test_issue_requires_principal_id(self) -> None:
        with self.assertRaises(AuthError):
            issue_web_session("", now=NOW)

    def test_issue_produces_independent_token_and_csrf(self) -> None:
        session = issue_web_session("principal-1", now=NOW)
        self.assertNotEqual(session.token, session.csrf_token)
        self.assertEqual(session.principal_id, "principal-1")

    def test_verify_succeeds_for_active_session(self) -> None:
        session = issue_web_session("principal-1", now=NOW)
        verified = verify_web_session(
            principal_id=session.principal_id,
            csrf_token=session.csrf_token,
            expires_at=session.expires_at,
            revoked=False,
            now=NOW,
        )
        self.assertEqual(verified.principal_id, "principal-1")
        self.assertEqual(verified.csrf_token, session.csrf_token)

    def test_verify_rejects_unknown_token(self) -> None:
        """Bir WebSessionRepository.lookup miss -- her alan None gelir."""
        with self.assertRaises(AuthError):
            verify_web_session(principal_id=None, csrf_token=None, expires_at=None, revoked=False, now=NOW)

    def test_verify_rejects_revoked_session(self) -> None:
        session = issue_web_session("principal-1", now=NOW)
        with self.assertRaises(AuthError):
            verify_web_session(
                principal_id=session.principal_id,
                csrf_token=session.csrf_token,
                expires_at=session.expires_at,
                revoked=True,
                now=NOW,
            )

    def test_verify_rejects_expired_session(self) -> None:
        session = issue_web_session("principal-1", now=NOW)
        later = session.expires_at + timedelta(seconds=1)
        with self.assertRaises(AuthError):
            verify_web_session(
                principal_id=session.principal_id,
                csrf_token=session.csrf_token,
                expires_at=session.expires_at,
                revoked=False,
                now=later,
            )


class CsrfTokenTests(unittest.TestCase):
    def test_matching_token_passes(self) -> None:
        verify_csrf_token("token-value", "token-value")

    def test_missing_token_fails_closed(self) -> None:
        with self.assertRaises(AuthError):
            verify_csrf_token(None, "token-value")
        with self.assertRaises(AuthError):
            verify_csrf_token("", "token-value")

    def test_mismatched_token_fails_closed(self) -> None:
        with self.assertRaises(AuthError):
            verify_csrf_token("wrong-value", "token-value")


if __name__ == "__main__":
    unittest.main()
