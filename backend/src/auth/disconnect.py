"""Principal-initiated disconnect (docs/AUTH.md -- "Disconnect/revoke connector session
ve Google credential'i ayri ayri iptal eder").

Revokes every persisted grant this connector holds for one principal in a single
orchestrated step: connector access/refresh tokens (Claude's side), the Google
credential vault secret and its DB reference, and every linked ads_account -- then
records one append-only audit_event. This is also how docs/PRODUCT.md's "kullanici
disconnect ile gelecek erisimi durdurabilir, account deletion talebi baslatabilir"
requirement is satisfied: nothing here is soft -- the vault secret is permanently
destroyed (``VaultClient.revoke``), not merely marked inactive. Rows in
``ads_account``/``oauth_credential`` are marked revoked rather than deleted only to
keep the ``proposal``/``approval``/``execution``/``audit_event`` foreign keys and
history intact (docs/DATA_MODEL.md -- "Audit olaylari append-only'dir; soft delete
audit yerine gecmez").
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from ..db.models import AuditEvent
from ..db.oauth_store import TokenRepository
from ..db.proposals import AuditRepository
from ..db.repository import AdsAccountRepository, OAuthCredentialRepository
from .vault import VaultClient

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


@dataclass(frozen=True, slots=True)
class DisconnectResult:
    credential_revoked: bool
    accounts_disconnected: int


def disconnect_principal(
    principal_id: str,
    *,
    tokens: TokenRepository,
    credentials: OAuthCredentialRepository,
    accounts: AdsAccountRepository,
    vault: VaultClient,
    audit: AuditRepository,
    now: datetime,
    correlation_id: str | None = None,
) -> DisconnectResult:
    """Revoke everything this connector holds for ``principal_id``.

    Idempotent: calling this a second time is safe and simply reports
    ``credential_revoked=False`` (nothing left to revoke). Revocation runs before the
    audit write on purpose -- access must come down even if the audit insert then
    fails; a revoke that succeeded without a log entry is safe, an unlogged action
    that *doesn't* revoke would not be.
    """
    linked_accounts = accounts.list_accounts(principal_id)
    tokens.revoke_all_for_principal(principal_id, now=now)
    credential = credentials.revoke_active(principal_id)
    if credential is not None:
        vault.revoke(credential.vault_ref)
    accounts.disconnect_all(principal_id)

    audit.insert(
        AuditEvent(
            event_id=str(uuid.uuid4()),
            occurred_at=now,
            actor=principal_id,
            principal_id=principal_id,
            customer_id=None,
            event_type="principal.disconnected",
            proposal_id=None,
            approval_id=None,
            execution_id=None,
            outcome="revoked",
            reason_code=None,
            correlation_id=_safe_correlation_id(correlation_id),
            google_request_id=None,
        )
    )
    return DisconnectResult(
        credential_revoked=credential is not None,
        accounts_disconnected=len(linked_accounts),
    )


def _safe_correlation_id(correlation_id: str | None) -> str:
    if correlation_id and _CORRELATION_ID_PATTERN.fullmatch(correlation_id):
        return correlation_id
    return str(uuid.uuid4())
