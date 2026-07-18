"""Tests for backend.src.auth.google_oauth::GoogleWebFlowOAuthClient (todo.md 3.5).

Everything here drives the *real* ``google_auth_oauthlib.flow.Flow`` /
``google.oauth2.credentials.Credentials`` construction path (the official Google
client libraries, per ADR-0001/ADR-0002) -- only two seams are stubbed, both because
they would otherwise require a real network round-trip to Google:

* ``Flow.fetch_token`` (normally POSTs the authorization code to Google's token
  endpoint) is replaced with a fake that sets ``oauth2session.token`` directly, so
  ``Flow.credentials`` still runs its real, unmodified conversion logic
  (``google_auth_oauthlib.helpers.credentials_from_session``) against it.
* ``google.oauth2.id_token.verify_oauth2_token`` (normally fetches Google's public
  certs and verifies the ID token's signature) is replaced with a controlled stub, so
  tests can assert the *verified* claims -- not a naive unverified JWT decode -- are
  what ``exchange_code`` trusts for the principal's subject.

``FakeGoogleOAuthClient`` (used everywhere else in the test suite) never exercises any
of this; this file is the only place ``GoogleWebFlowOAuthClient`` itself is tested.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.app import _LOGIN_ONLY_SCOPES
from backend.src.auth.google_oauth import (
    GOOGLE_SCOPES,
    GoogleOAuthError,
    GoogleWebFlowOAuthClient,
)
from google_auth_oauthlib.flow import Flow

REDIRECT_URI = "https://connector.example.com/google/callback"


def _client(**overrides: object) -> GoogleWebFlowOAuthClient:
    kwargs: dict[str, object] = dict(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri=REDIRECT_URI,
    )
    kwargs.update(overrides)
    return GoogleWebFlowOAuthClient(**kwargs)  # type: ignore[arg-type]


def _fake_fetch_token(self: Flow, **_kwargs: object) -> dict[str, object]:
    """Stand-in for the real network POST -- sets exactly what
    ``credentials_from_session`` (the official conversion helper) reads."""
    token = {
        "access_token": "ya29.fake-access-token",
        "refresh_token": "1//fake-refresh-token",
        "id_token": "fake-id-token-opaque-value",
        "expires_at": time.time() + 3600,
        "scope": self.oauth2session.scope,
    }
    self.oauth2session.token = token
    return token


def _fake_fetch_token_without(*missing: str):
    def _fetch(self: Flow, **_kwargs: object) -> dict[str, object]:
        token = _fake_fetch_token(self)
        for key in missing:
            token.pop(key, None)
        self.oauth2session.token = token
        return token

    return _fetch


class AuthorizationUrlTests(unittest.TestCase):
    def _query(self, url: str) -> dict[str, list[str]]:
        self.assertTrue(url.startswith("https://accounts.google.com/o/oauth2/auth"))
        return parse_qs(urlsplit(url).query)

    def test_offline_access_and_consent_prompt_are_requested(self) -> None:
        """access_type=offline is required to receive a refresh_token at all;
        prompt=consent is required so a *returning* user still gets one (Google
        only issues refresh_token on first consent otherwise)."""
        query = self._query(_client().build_authorization_url(state="s1"))

        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])

    def test_redirect_uri_matches_exactly_what_was_configured(self) -> None:
        query = self._query(_client().build_authorization_url(state="s1"))

        self.assertEqual(query["redirect_uri"], [REDIRECT_URI])

    def test_state_is_echoed_verbatim(self) -> None:
        query = self._query(_client().build_authorization_url(state="unpredictable-opaque-state-1"))

        self.assertEqual(query["state"], ["unpredictable-opaque-state-1"])

    def test_restricted_adwords_scope_is_included_by_default(self) -> None:
        query = self._query(_client().build_authorization_url(state="s1"))

        self.assertIn("https://www.googleapis.com/auth/adwords", query["scope"][0].split(" "))

    def test_login_only_scopes_never_include_adwords(self) -> None:
        """docs/AUTH.md 'Approval-UI web girişi': the /login-only client must never
        be able to request Google Ads write/read access, only identity."""
        self.assertNotIn("https://www.googleapis.com/auth/adwords", _LOGIN_ONLY_SCOPES)
        query = self._query(_client(scopes=_LOGIN_ONLY_SCOPES).build_authorization_url(state="s1"))

        self.assertNotIn("https://www.googleapis.com/auth/adwords", query["scope"][0].split(" "))

    def test_default_scopes_are_the_documented_google_scopes_constant(self) -> None:
        self.assertEqual(_client()._scopes, list(GOOGLE_SCOPES))  # noqa: SLF001


class ExchangeCodeTests(unittest.TestCase):
    def test_successful_exchange_returns_refresh_token_and_verified_subject(self) -> None:
        with (
            patch.object(Flow, "fetch_token", _fake_fetch_token),
            patch(
                "backend.src.auth.google_oauth.google.oauth2.id_token.verify_oauth2_token",
                return_value={"sub": "verified-google-subject", "email": "user@example.com"},
            ) as verify,
        ):
            result = _client().exchange_code(code="auth-code-1")

        self.assertEqual(result.refresh_token, "1//fake-refresh-token")
        self.assertEqual(result.access_token, "ya29.fake-access-token")
        self.assertEqual(result.google_subject, "verified-google-subject")
        self.assertEqual(result.email, "user@example.com")
        # The subject must come from the *verified* return value, not a raw decode --
        # confirm verify_oauth2_token was actually invoked with this id_token and our
        # own client_id as the expected audience (RFC 7519 aud check).
        verify.assert_called_once()
        called_id_token, _request, called_audience = verify.call_args[0]
        self.assertEqual(called_id_token, "fake-id-token-opaque-value")
        self.assertEqual(called_audience, "test-client-id")

    def test_missing_refresh_token_raises_without_calling_verify(self) -> None:
        """Google only omits refresh_token when access_type=offline/prompt=consent
        were not honoured -- this must fail closed, not silently issue an
        access-token-only credential (SECURITY.md 'yalniz gereken scope', AUTH.md
        offline access requirement)."""
        with (
            patch.object(Flow, "fetch_token", _fake_fetch_token_without("refresh_token")),
            patch(
                "backend.src.auth.google_oauth.google.oauth2.id_token.verify_oauth2_token"
            ) as verify,
            self.assertRaises(GoogleOAuthError),
        ):
            _client().exchange_code(code="auth-code-2")
        verify.assert_not_called()

    def test_missing_id_token_raises_without_calling_verify(self) -> None:
        with (
            patch.object(Flow, "fetch_token", _fake_fetch_token_without("id_token")),
            patch(
                "backend.src.auth.google_oauth.google.oauth2.id_token.verify_oauth2_token"
            ) as verify,
            self.assertRaises(GoogleOAuthError),
        ):
            _client().exchange_code(code="auth-code-3")
        verify.assert_not_called()

    def test_verified_claims_missing_sub_raises(self) -> None:
        with (
            patch.object(Flow, "fetch_token", _fake_fetch_token),
            patch(
                "backend.src.auth.google_oauth.google.oauth2.id_token.verify_oauth2_token",
                return_value={"email": "user@example.com"},  # no "sub"
            ),
            self.assertRaises(GoogleOAuthError),
        ):
            _client().exchange_code(code="auth-code-4")

    def test_signature_verification_failure_propagates_not_swallowed(self) -> None:
        """A forged/expired/wrong-audience ID token must not be silently accepted --
        confirms there is no fallback path that trusts an unverified token."""
        with (
            patch.object(Flow, "fetch_token", _fake_fetch_token),
            patch(
                "backend.src.auth.google_oauth.google.oauth2.id_token.verify_oauth2_token",
                side_effect=ValueError("Token has invalid signature."),
            ),
            self.assertRaises(ValueError),
        ):
            _client().exchange_code(code="auth-code-5")


if __name__ == "__main__":
    unittest.main()
