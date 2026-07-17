"""Google upstream OAuth (the second, separate token plane -- docs/AUTH.md).

Per ADR-0001 (kept by ADR-0002), the Google leg uses the official
``google-auth-oauthlib``/``google-auth`` libraries rather than Authlib. This is the
only place a Google refresh token exists outside the vault: ``exchange_code`` reads
it off the library's ``Credentials`` object and the caller (``auth/server.py``) must
hand it to ``VaultClient.store`` immediately and never persist or log it directly
(SECURITY.md -- token passthrough / no secrets in logs).

The connector's principal identity (``docs/AUTH.md`` -- "subject = Google's OpenID
``sub`` claim") is resolved here too, from a signature-verified ID token -- never
from unverified JWT claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import google.auth.transport.requests
import google.oauth2.id_token
from google_auth_oauthlib.flow import Flow

GOOGLE_SCOPES: tuple[str, ...] = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/adwords",
)


class GoogleOAuthError(RuntimeError):
    """Raised when the Google upstream leg cannot be completed."""


@dataclass(frozen=True, slots=True)
class GoogleTokenResult:
    """The outcome of redeeming a Google authorization code.

    ``refresh_token``/``access_token`` must be handed to a ``VaultClient`` and never
    stored, logged or returned to any MCP/Claude-facing response.
    """

    refresh_token: str
    access_token: str
    google_subject: str
    email: str | None


class GoogleOAuthClient(Protocol):
    """Boundary for the Google upstream OAuth leg, so tests never need real network/Google."""

    def build_authorization_url(self, *, state: str) -> str:
        """Return the URL the user is redirected to for Google consent."""

    def exchange_code(self, *, code: str) -> GoogleTokenResult:
        """Redeem a Google authorization code for tokens and a verified subject."""


class GoogleWebFlowOAuthClient:
    """Real ``GoogleOAuthClient`` backed by ``google_auth_oauthlib.flow.Flow``."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: Sequence[str] = GOOGLE_SCOPES,
    ) -> None:
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scopes = list(scopes)
        self._client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

    def _new_flow(self) -> Flow:
        return Flow.from_client_config(
            self._client_config, scopes=self._scopes, redirect_uri=self._redirect_uri
        )

    def build_authorization_url(self, *, state: str) -> str:
        flow = self._new_flow()
        url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
            state=state,
            include_granted_scopes="true",
        )
        return url

    def exchange_code(self, *, code: str) -> GoogleTokenResult:
        flow = self._new_flow()
        flow.fetch_token(code=code)
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise GoogleOAuthError(
                "Google refresh_token alinamadi; access_type=offline ve prompt=consent kontrol edin."
            )
        if not credentials.id_token:
            raise GoogleOAuthError("Google id_token alinamadi; 'openid' scope'u istendiginden emin olun.")
        claims = google.oauth2.id_token.verify_oauth2_token(
            credentials.id_token, google.auth.transport.requests.Request(), self._client_id
        )
        subject = claims.get("sub")
        if not subject:
            raise GoogleOAuthError("Google id_token dogrulamasi 'sub' claim'i dondurmedi.")
        return GoogleTokenResult(
            refresh_token=credentials.refresh_token,
            access_token=credentials.token,
            google_subject=subject,
            email=claims.get("email"),
        )


class FakeGoogleOAuthClient:
    """Deterministic test double -- no real network/Google call (docs/TESTING.md mock policy)."""

    def __init__(self, *, google_subject: str = "google-sub-1", email: str = "user@example.com") -> None:
        self.google_subject = google_subject
        self.email = email
        self.last_code: str | None = None

    def build_authorization_url(self, *, state: str) -> str:
        return f"https://accounts.google.com/o/oauth2/auth?state={state}&fake=1"

    def exchange_code(self, *, code: str) -> GoogleTokenResult:
        self.last_code = code
        return GoogleTokenResult(
            refresh_token=f"fake-google-refresh-{code}",
            access_token=f"fake-google-access-{code}",
            google_subject=self.google_subject,
            email=self.email,
        )
