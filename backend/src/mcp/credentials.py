"""Resolves a verified ``(principal_id, customer_id)`` pair into Google Ads credentials.

The one place account ownership, active-credential and vault checks are all
combined before any ``backend.src.api`` adapter call is made
(docs/SECURITY.md -- "customer_id, principal'in Google credential'iyla
erisebildigi dogrulanmis hesap eslemesine karsi kontrol edilir"; "Credential
cozumleme principal_id + credential_id ile yapilir ve hesap erisimi ikinci kez
dogrulanir"). Every failure here is an ``AdsApiError`` so it flows through the
same safe-message path as an adapter failure -- never a raw DB/vault exception.
"""

from __future__ import annotations

from ..api.errors import AdsApiError, ErrorClass
from ..api.reporting import GoogleAdsCredentials
from ..auth.vault import VaultClient, VaultError
from ..config import Settings
from ..db.repository import AdsAccountRepository, OAuthCredentialRepository


def resolve_google_ads_credentials(
    *,
    principal_id: str,
    customer_id: str,
    settings: Settings,
    accounts: AdsAccountRepository,
    oauth_credentials: OAuthCredentialRepository,
    vault: VaultClient,
) -> GoogleAdsCredentials:
    """Return credentials for a Google Ads call, or raise a safe ``AdsApiError``.

    ``accounts.get_account`` already scopes its lookup to ``principal_id``
    (``backend.src.db.repository`` -- "cross-principal reads return None"), so
    a ``customer_id`` belonging to a different principal is indistinguishable
    from an unknown one here -- both fail closed the same way.
    """
    account = accounts.get_active_account(principal_id, customer_id)
    if account is None:
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="account_not_linked",
            message="Bu customer_id bu baglantiya ait degil veya henuz baglanmamis.",
            request_id=None,
        )

    credential = oauth_credentials.get_active(principal_id)
    if credential is None:
        raise AdsApiError(
            error_class=ErrorClass.AUTH,
            code="no_active_google_credential",
            message=(
                "Google hesabi bagli degil veya baglanti iptal edilmis; "
                "yeniden baglanmaniz gerekiyor."
            ),
            request_id=None,
        )

    try:
        refresh_token = vault.read(credential.vault_ref)
    except VaultError as error:
        raise AdsApiError(
            error_class=ErrorClass.AUTH,
            code="credential_unreadable",
            message="Google kimlik bilgisi cozulemedi; yeniden baglanmaniz gerekiyor.",
            request_id=None,
        ) from error

    return GoogleAdsCredentials(
        developer_token=settings.google_ads_developer_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        refresh_token=refresh_token,
        login_customer_id=account.login_customer_id,
    )


def deactivate_credential_on_auth_failure(
    error: AdsApiError, *, principal_id: str, oauth_credentials: OAuthCredentialRepository
) -> None:
    """ERROR_HANDLING.md 'Auth' row: '`invalid_grant`, permission -> Credential
    pasifleştir, işleri durdur'.

    Any AUTH-class failure (revoked/expired refresh token, 2SV no longer enrolled,
    permission withdrawn, ...) means the stored refresh token can no longer be trusted
    for this principal. Deactivating it here (DB-only -- the vault secret itself is
    left alone, unlike disconnect's deliberate destroy; docs/SECURITY.md
    "pasifleştirilir") makes every subsequent call fail fast through the
    ``no_active_google_credential`` branch above instead of repeatedly re-trying
    Google with a token already known to be bad (todo.md 3.6 -- "sonsuz retry
    yapma").
    """
    if error.error_class is ErrorClass.AUTH:
        oauth_credentials.revoke_active(principal_id)
