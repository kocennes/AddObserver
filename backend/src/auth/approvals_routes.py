"""Browser-facing human approval routes (docs/ARCHITECTURE.md, docs/AUTH.md).

Separate from ``auth/server.py``'s Claude-facing OAuth AS: this is the surface a
human uses directly, outside Claude's tool-calling loop, to approve or reject a
proposal Claude prepared via ``mcp/proposals.py``. The session here is a
lightweight browser cookie issued by the login-only Google flow in
``server.py``'s ``/google/callback`` fallback branch -- it carries no Google Ads
scope and never touches the stored Ads credential (see that module's docstring).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..api.identifiers import validate_opaque_id
from ..approval import ApprovalError, Decision, Proposal, approve_proposal
from ..db.models import AuditEvent
from ..db.oauth_store import TokenRepository
from ..db.postgres_repository import (
    PostgresApprovalRepository,
    PostgresProposalRepository,
    PostgresWebSessionRepository,
)
from ..db.proposals import ApprovalRepository, AuditRepository, ProposalRepository
from ..db.repository import AdsAccountRepository, OAuthCredentialRepository, PrincipalRepository
from ..db.web_session_store import WebLoginStateRepository, WebSessionRepository
from .context import AuthContext, get_context
from .disconnect import disconnect_principal, disconnect_principal_durable
from .domain import AuthError
from .web_session import (
    WEB_SESSION_TTL_SECONDS,
    AuthenticatedWebSession,
    issue_login_state,
    issue_web_session,
    redeem_login_state,
    verify_csrf_token,
    verify_web_session,
)

router = APIRouter()
WEB_SESSION_COOKIE = "web_session"
WEB_CSRF_COOKIE = "web_csrf"

WebSessionStore = WebSessionRepository | PostgresWebSessionRepository
ProposalStore = ProposalRepository | PostgresProposalRepository
ApprovalStore = ApprovalRepository | PostgresApprovalRepository


@contextmanager
def _browser_request_repositories(
    request: Request, context: AuthContext
) -> Iterator[tuple[WebSessionStore, ProposalStore, ApprovalStore]]:
    """Bind an exact browser session and keep DB-only approval work atomic."""
    if context.postgres_uow_factory is None:
        yield (
            WebSessionRepository(context.conn),
            ProposalRepository(context.conn),
            ApprovalRepository(context.conn),
        )
        return

    with context.postgres_uow_factory.request() as work:
        raw_token = request.cookies.get(WEB_SESSION_COOKIE)
        if raw_token is not None:
            work.bootstrap_web_session(raw_token)
        if work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
        yield (
            work.repositories.web_sessions,
            work.repositories.proposals,
            work.repositories.approvals,
        )


#: Minimal, dependency-free CSS covering docs/DESIGN.md's WCAG 2.2 AA baseline: visible
#: 2px focus outline (focus-appearance), >=24px pointer targets, prefers-reduced-motion,
#: and a light/dark pair that keeps body-text contrast >=4.5:1 in both (color-scheme lets
#: the browser pick UA form-control colors that already meet this).
_PAGE_STYLE = """
:root{color-scheme:light dark;font-size:100%;}
body{font-family:system-ui,sans-serif;line-height:1.5;margin:0;padding:0 1rem 2rem;
  color:#111;background:#fff;max-width:60rem;}
@media (prefers-color-scheme:dark){body{color:#f2f2f2;background:#121212;}}
.skip-link{position:absolute;left:-999px;top:0;padding:.5rem 1rem;background:#fff;color:#111;}
.skip-link:focus{left:.5rem;top:.5rem;z-index:1;}
a:focus-visible,button:focus-visible,input:focus-visible{
  outline:2px solid #1a56db;outline-offset:2px;}
button{min-height:44px;min-width:44px;padding:.5rem 1rem;
  margin:.25rem .5rem .25rem 0;font-size:1rem;}
dt{font-weight:bold;margin-top:.5rem;}
dd{margin-left:0;}
article{border:1px solid currentColor;border-radius:4px;padding:1rem;margin-bottom:1rem;}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important;}}
"""


def _page(title: str, body: str) -> str:
    """Wrap route-specific markup in one accessible HTML5 document (docs/DESIGN.md
    "Erişilebilirlik — WCAG 2.2 AA"): ``lang``, viewport, a skip link to ``#main``,
    and the shared focus/contrast/reduced-motion baseline every route needs."""
    return (
        "<!doctype html>"
        '<html lang="tr">'
        "<head>"
        '<meta charset="utf-8" />'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />'
        f"<title>{escape(title)}</title>"
        f"<style>{_PAGE_STYLE}</style>"
        "</head>"
        "<body>"
        '<a class="skip-link" href="#main">İçeriğe geç</a>'
        f'<main id="main">{body}</main>'
        "</body>"
        "</html>"
    )


def _error_page(title: str, description: str, status_code: int = 400) -> HTMLResponse:
    body = f'<h1>{escape(title)}</h1><p role="alert">{escape(description)}</p>'
    return HTMLResponse(content=_page(title, body), status_code=status_code)


async def handle_web_login_callback(
    state: str, *, code: str | None, error: str | None, context: AuthContext
) -> HTMLResponse | RedirectResponse:
    """The ``/approvals`` login fallback leg of ``/google/callback`` (called from ``server.py``
    when ``state`` doesn't match a pending Claude-client ``authorization_transaction``).

    Reuses that same redirect_uri instead of registering a second one -- adding or
    changing a redirect URI can trigger Google OAuth re-verification
    (docs/GOOGLE_API_ACCESS.md). Never calls ``vault.store``,
    ``OAuthCredentialRepository.upsert`` or ``ClientGrantRepository.record_consent``:
    a login must never rotate the stored Google Ads credential.
    """
    if context.postgres_uow_factory is None:
        claimed = WebLoginStateRepository(context.conn).claim(state)
    else:
        with context.postgres_uow_factory.request() as work:
            if work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
            claimed = work.repositories.web_login_states.claim(state)
    if claimed is None:
        return _error_page(
            "İşlem bulunamadı", "Google geri çağrısı bilinmeyen bir işlem içeriyor.", 400
        )
    already_consumed, expires_at = claimed
    now = datetime.now(UTC)
    try:
        redeem_login_state(already_consumed=already_consumed, expires_at=expires_at, now=now)
    except AuthError as login_error:
        return _error_page("Giriş işlemi geçersiz", str(login_error), 400)

    if error is not None or code is None:
        return _error_page("Giriş reddedildi", "Google girişi tamamlanmadı.", 400)
    if context.login_google_client is None:
        return _error_page("Yapılandırma hatası", "Giriş istemcisi yapılandırılmamış.", 500)

    try:
        google_result = context.login_google_client.exchange_code(code=code)
    except Exception:  # noqa: BLE001 -- never leak raw Google/library errors to the browser
        return _error_page("Google girişi başarısız", "Google ile giriş tamamlanamadı.", 400)

    if context.postgres_uow_factory is None:
        principal = PrincipalRepository(context.conn).get(
            "https://accounts.google.com", google_result.google_subject
        )
        if principal is None:
            return _error_page(
                "Bağlantı bulunamadı",
                "Bu Google hesabıyla bağlı bir connector bulunamadı. "
                "Önce Claude üzerinden bağlanın.",
                403,
            )
        session = issue_web_session(principal.id, now=now)
        WebSessionRepository(context.conn).create(
            principal.id, session.token, session.csrf_token, session.expires_at
        )
    else:
        with context.postgres_uow_factory.request() as work:
            if work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
            principal = work.repositories.principals.get(
                "https://accounts.google.com", google_result.google_subject
            )
            if principal is None:
                return _error_page(
                    "Bağlantı bulunamadı",
                    "Bu Google hesabıyla bağlı bir connector bulunamadı. "
                    "Önce Claude üzerinden bağlanın.",
                    403,
                )
            work.bind_principal(principal.id)
            session = issue_web_session(principal.id, now=now)
            work.repositories.web_sessions.create(
                principal.id, session.token, session.csrf_token, session.expires_at
            )
    response = RedirectResponse(url="/approvals", status_code=302)
    response.set_cookie(
        WEB_SESSION_COOKIE,
        session.token,
        httponly=True,
        secure=context.settings.environment != "local",
        samesite="strict",
        max_age=WEB_SESSION_TTL_SECONDS,
        path="/",
    )
    response.set_cookie(
        WEB_CSRF_COOKIE,
        session.csrf_token,
        httponly=False,
        secure=context.settings.environment != "local",
        samesite="strict",
        max_age=WEB_SESSION_TTL_SECONDS,
        path="/",
    )
    return response


@router.get("/login")
async def login(context: AuthContext = Depends(get_context)):
    """Start the login-only Google sign-in used solely to reach ``/approvals``.

    Requests only ``openid``+``email`` (see ``context.login_google_client``) --
    never ``adwords`` -- and never issues a session unless a principal already
    exists for the verified Google subject (login can never create one).
    """
    if context.login_google_client is None:
        return _error_page("Yapılandırma hatası", "Giriş istemcisi yapılandırılmamış.", 500)
    now = datetime.now(UTC)
    login_state = issue_login_state(now=now)
    if context.postgres_uow_factory is None:
        WebLoginStateRepository(context.conn).create(login_state.state, login_state.expires_at)
    else:
        with context.postgres_uow_factory.request() as work:
            if work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
            work.repositories.web_login_states.create(login_state.state, login_state.expires_at)
    url = context.login_google_client.build_authorization_url(state=login_state.state)
    return RedirectResponse(url=url, status_code=302)


def _require_session(request: Request, web_sessions: WebSessionStore) -> AuthenticatedWebSession:
    raw_token = request.cookies.get(WEB_SESSION_COOKIE)
    if raw_token is None:
        raise AuthError("invalid_token", "Oturum bulunamadi.")
    lookup = web_sessions.lookup(raw_token)
    return verify_web_session(
        principal_id=lookup.principal_id,
        csrf_token_hash=lookup.csrf_token_hash,
        expires_at=lookup.expires_at,
        revoked=lookup.revoked,
        now=datetime.now(UTC),
    )


#: Human-readable Turkish label per docs/approval/payload_schema.py::ProposalType --
#: docs/DESIGN.md "Öneri ile gerçeği ayır": the raw enum value stays visible too (existing
#: tests assert on it), this is additive, not a replacement.
_PROPOSAL_TYPE_LABELS: dict[str, str] = {
    "campaign_pause": "Kampanyayı duraklat",
    "campaign_enable": "Kampanyayı etkinleştir",
    "campaign_budget_update": "Kampanya bütçesini güncelle",
}

_RISK_LABELS: dict[str, str] = {"low": "Düşük", "medium": "Orta", "high": "Yüksek"}


def _proposal_summary(proposal: Proposal) -> str:
    payload = proposal.payload
    proposal_type = payload.get("type", "?")
    campaign_id = payload.get("campaign_id", "?")
    return f"{proposal_type} / kampanya {campaign_id}"


def _format_value(value: object) -> str:
    """Render one side of a ``before``/``after`` payload dict for the approval preview.

    Only the two shapes ``build_proposal_payload`` ever produces (a campaign
    ``status`` string or an ``amount_micros`` integer) are known here; anything else
    falls back to a plain str() so a future proposal type still renders instead of
    crashing the page.
    """
    if not isinstance(value, dict):
        return str(value)
    if "status" in value:
        return f"Durum: {value['status']}"
    if "amount_micros" in value:
        # Google Ads API micros'u dogrudan dondurur (docs/API_CONTRACTS.md); hesabin para
        # birimi bu connector'da hic sorgulanmadigi icin bir birim/kur varsayilmiyor.
        return f"Bütçe: {value['amount_micros']} micros"
    return str(value)


def _request_correlation_id(request: Request) -> str | None:
    correlation_id = request.scope.get("correlation_id")
    return correlation_id if isinstance(correlation_id, str) and correlation_id else None


@router.get("/approvals")
async def list_approvals(request: Request, context: AuthContext = Depends(get_context)):
    """Render the caller's pending proposals with per-row, action-labelled approve/reject forms."""
    with _browser_request_repositories(request, context) as (sessions, proposals, _approvals):
        try:
            session = _require_session(request, sessions)
        except AuthError:
            return RedirectResponse(url="/login", status_code=302)

        pending = proposals.list_pending(session.principal_id).proposals
        csrf_cookie = request.cookies.get(WEB_CSRF_COOKIE)
        try:
            verify_csrf_token(csrf_cookie, session.csrf_token_hash)
        except AuthError:
            return RedirectResponse(url="/login", status_code=302)
        if csrf_cookie is None:
            raise RuntimeError("CSRF token verification accepted an absent cookie")
        csrf = escape(csrf_cookie)
        rows: list[str] = []
        for proposal in pending:
            rows.append(_render_proposal_article(proposal, csrf))
        body = (
            "<h1>Bekleyen öneriler</h1>"
            f"<ul>{''.join(rows) if rows else '<li>Bekleyen öneri yok.</li>'}</ul>"
            '<nav aria-label="Hesap işlemleri">'
            '<form method="post" action="/logout">'
            f'<input type="hidden" name="csrf_token" value="{csrf}" />'
            '<button type="submit">Çıkış yap</button>'
            "</form>"
            '<a href="/disconnect">Bağlantıyı kes (disconnect)</a>'
            "</nav>"
        )
        return HTMLResponse(content=_page("Bekleyen öneriler", body))


def _render_proposal_article(proposal: Proposal, csrf: str) -> str:
    """Render one proposal as a full pre-decision preview (docs/PRODUCT.md, docs/DESIGN.md
    "Bilgi mimarisi" -> Öneri detayı): account, operation, resource, current/proposed
    value, rationale/evidence, risk and expiry, plus an explicit not-yet-applied notice --
    Faz 1 never sends a mutate to Google Ads from this screen."""
    payload = proposal.payload
    summary = escape(_proposal_summary(proposal))
    customer = escape(proposal.customer_id)
    proposal_type = str(payload.get("type", "?"))
    operation_label = escape(_PROPOSAL_TYPE_LABELS.get(proposal_type, proposal_type))
    campaign_id = escape(str(payload.get("campaign_id", "?")))
    current_value = escape(_format_value(payload.get("before")))
    proposed_value = escape(_format_value(payload.get("after")))
    rationale = escape(str(payload.get("rationale", "")))
    risk_raw = str(payload.get("risk", "?"))
    risk_label = escape(_RISK_LABELS.get(risk_raw, risk_raw))
    evidence_refs = payload.get("evidence_refs") or []
    evidence_html = (
        "".join(f"<li>{escape(str(ref))}</li>" for ref in evidence_refs)
        if evidence_refs
        else "<li>Kaynak metrik referansı belirtilmemiş.</li>"
    )
    expires = escape(proposal.expires_at.isoformat())
    proposal_id = escape(proposal.proposal_id)
    # id must be a valid HTML id token; proposal_id already passed validate_opaque_id
    # ([A-Za-z0-9._-]) before it ever reaches a pending-list row, so no extra escaping is
    # needed to make it a safe id/aria-labelledby target beyond the html.escape() above.
    heading_id = f"proposal-{proposal_id}-heading"

    return (
        "<li>"
        f'<article aria-labelledby="{heading_id}">'
        f'<h2 id="{heading_id}">{summary}</h2>'
        "<dl>"
        f"<dt>Hesap</dt><dd>{customer}</dd>"
        f"<dt>İşlem</dt><dd>{operation_label} ({escape(proposal_type)})</dd>"
        f"<dt>Kaynak (kampanya)</dt><dd>{campaign_id}</dd>"
        f"<dt>Mevcut değer</dt><dd>{current_value}</dd>"
        f"<dt>Önerilen değer</dt><dd>{proposed_value}</dd>"
        f"<dt>Gerekçe</dt><dd>{rationale}</dd>"
        f"<dt>Kaynak metrikler</dt><dd><ul>{evidence_html}</ul></dd>"
        f"<dt>Risk</dt><dd>{risk_label}</dd>"
        f"<dt>Son geçerlilik</dt><dd>{expires}</dd>"
        "<dt>Durum</dt>"
        "<dd>Onay bekliyor. Bu ekrandan verilen karar yalnız connector veritabanına "
        "kaydedilir; Google Ads hesabına henüz hiçbir değişiklik gönderilmedi.</dd>"
        "</dl>"
        f'<form method="post" action="/approvals/{proposal_id}/decision">'
        f'<input type="hidden" name="csrf_token" value="{csrf}" />'
        f'<button type="submit" name="decision" value="approve">Onayla: {summary}</button>'
        f'<button type="submit" name="decision" value="reject">Reddet: {summary}</button>'
        "</form>"
        "</article>"
        "</li>"
    )


@router.get("/disconnect")
async def confirm_disconnect(request: Request, context: AuthContext = Depends(get_context)):
    """Show the impact summary and irreversible-deletion warning before the real
    ``POST /disconnect`` (docs/PRODUCT.md "disconnect ile gelecek erisimi durdurabilir";
    docs/DESIGN.md "Onay modalı ... geri alma bilgisini tekrarlar"). A GET, so viewing it
    never itself revokes anything -- only the CSRF-protected POST below does that."""
    with _browser_request_repositories(request, context) as (sessions, _proposals, _approvals):
        try:
            session = _require_session(request, sessions)
        except AuthError:
            return RedirectResponse(url="/login", status_code=302)

        csrf_cookie = request.cookies.get(WEB_CSRF_COOKIE)
        try:
            verify_csrf_token(csrf_cookie, session.csrf_token_hash)
        except AuthError:
            return RedirectResponse(url="/login", status_code=302)
        if csrf_cookie is None:
            raise RuntimeError("CSRF token verification accepted an absent cookie")
        csrf = escape(csrf_cookie)

        if context.postgres_uow_factory is None:
            accounts_count = len(
                AdsAccountRepository(context.conn).list_accounts(session.principal_id)
            )
            has_credential = (
                OAuthCredentialRepository(context.conn).get_active(session.principal_id) is not None
            )
        else:
            with context.postgres_uow_factory.request() as work:
                if work.repositories is None:
                    raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
                accounts_count = len(work.repositories.accounts.list_accounts(session.principal_id))
                has_credential = (
                    work.repositories.credentials.get_active(session.principal_id) is not None
                )

    credential_line = (
        "Google Ads bağlantı bilgileriniz (vault'ta şifreli saklanan credential) kalıcı olarak "
        "silinecek."
        if has_credential
        else "Şu anda saklanan aktif bir Google Ads credential'ı yok."
    )
    body = (
        "<h1>Bağlantıyı kes</h1>"
        '<p role="alert">Bu işlem geri alınamaz. Onayladığınızda:</p>'
        "<ul>"
        f"<li>{accounts_count} bağlı Google Ads hesabının erişimi sonlandırılır.</li>"
        f"<li>{escape(credential_line)}</li>"
        "<li>Bu bağlantıya ait tüm oturumlar (bu tarayıcı dahil, diğer tarayıcılar da) "
        "kapatılır.</li>"
        "<li>Bekleyen öneriler ve geçmiş denetim (audit) kayıtları silinmez; ancak hesap "
        "bağlantısı kesildiği için performans verisi bir daha çekilemez ve yeniden bağlanmak "
        "Google ile yeni bir onay (consent) gerektirir.</li>"
        "</ul>"
        '<form method="post" action="/disconnect">'
        f'<input type="hidden" name="csrf_token" value="{csrf}" />'
        '<button type="submit">Evet, bağlantıyı kalıcı olarak kes</button>'
        "</form>"
        '<p><a href="/approvals">Vazgeç</a></p>'
    )
    return HTMLResponse(content=_page("Bağlantıyı kes", body))


@router.post("/disconnect")
async def disconnect(
    request: Request,
    csrf_token: str = Form(...),
    context: AuthContext = Depends(get_context),
):
    """Revoke this principal's connector session and Google credential, then log out.

    A destructive, irreversible action gated by the same session + CSRF proof as a
    proposal decision (docs/AUTH.md disconnect decision) -- see ``disconnect_principal``
    for exactly what gets revoked.
    """
    if context.postgres_uow_factory is None:
        try:
            session = _require_session(request, WebSessionRepository(context.conn))
            verify_csrf_token(csrf_token, session.csrf_token_hash)
        except AuthError as error:
            return _error_page("Yetkisiz", str(error), 401)

        disconnect_principal(
            session.principal_id,
            tokens=TokenRepository(context.conn),
            credentials=OAuthCredentialRepository(context.conn),
            accounts=AdsAccountRepository(context.conn),
            vault=context.vault,
            audit=AuditRepository(context.conn),
            now=datetime.now(UTC),
            web_sessions=WebSessionRepository(context.conn),
            correlation_id=_request_correlation_id(request),
        )
    else:
        with context.postgres_uow_factory.request() as work:
            raw_token = request.cookies.get(WEB_SESSION_COOKIE)
            if raw_token is not None:
                work.bootstrap_web_session(raw_token)
            if work.repositories is None:
                raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
            try:
                session = _require_session(request, work.repositories.web_sessions)
                verify_csrf_token(csrf_token, session.csrf_token_hash)
            except AuthError as error:
                return _error_page("Yetkisiz", str(error), 401)
            disconnect_principal_durable(
                session.principal_id,
                tokens=work.repositories.tokens,
                credentials=work.repositories.credentials,
                credential_revocations=work.repositories.credential_revocations,
                accounts=work.repositories.accounts,
                audit=work.repositories.audit,
                now=datetime.now(UTC),
                web_sessions=work.repositories.web_sessions,
                correlation_id=_request_correlation_id(request),
            )

    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(WEB_SESSION_COOKIE, path="/")
    response.delete_cookie(WEB_CSRF_COOKIE, path="/")
    return response


@router.post("/approvals/{proposal_id}/decision")
async def decide_proposal(
    proposal_id: str,
    request: Request,
    decision: str = Form(...),
    csrf_token: str = Form(...),
    context: AuthContext = Depends(get_context),
):
    """Record one human decision. Fails closed on a missing session, CSRF mismatch,
    or a ``proposal_id`` that does not belong to the calling principal."""
    with _browser_request_repositories(request, context) as (sessions, proposals, approvals):
        try:
            session = _require_session(request, sessions)
            verify_csrf_token(csrf_token, session.csrf_token_hash)
        except AuthError as error:
            return _error_page("Yetkisiz", str(error), 401)

        try:
            validate_opaque_id(proposal_id, field_name="proposal_id")
        except ValueError:
            return _error_page(
                "Bulunamadı", "Bu öneri bulunamadı veya bu bağlantıya ait değil.", 404
            )

        try:
            decision_enum = Decision(decision)
        except ValueError:
            return _error_page(
                "Geçersiz karar", "decision yalnız 'approve' veya 'reject' olabilir.", 400
            )

        proposal = proposals.get(session.principal_id, proposal_id)
        if proposal is None:
            return _error_page(
                "Bulunamadı", "Bu öneri bulunamadı veya bu bağlantıya ait değil.", 404
            )

        now = datetime.now(UTC)
        try:
            updated_proposal, approval = approve_proposal(
                proposal,
                principal_id=session.principal_id,
                approver_id=session.principal_id,
                decision=decision_enum,
                now=now,
            )
        except ApprovalError as error:
            return _error_page("Karar kaydedilemedi", str(error), 400)

        approvals.save_decision_with_audit(
            updated_proposal,
            approval,
            AuditEvent(
                event_id=str(uuid.uuid4()),
                occurred_at=now,
                actor=session.principal_id,
                principal_id=session.principal_id,
                customer_id=proposal.customer_id,
                event_type="approval.decided",
                proposal_id=proposal.proposal_id,
                approval_id=None,
                execution_id=None,
                outcome=decision_enum.value,
                reason_code=None,
                correlation_id=_request_correlation_id(request) or str(uuid.uuid4()),
                google_request_id=None,
            ),
        )
        return RedirectResponse(url="/approvals", status_code=302)


@router.post("/logout")
async def logout(
    request: Request,
    csrf_token: str = Form(...),
    context: AuthContext = Depends(get_context),
):
    with _browser_request_repositories(request, context) as (sessions, _proposals, _approvals):
        try:
            session = _require_session(request, sessions)
            verify_csrf_token(csrf_token, session.csrf_token_hash)
        except AuthError as error:
            return _error_page("Yetkisiz", str(error), 401)

        raw_token = request.cookies.get(WEB_SESSION_COOKIE)
        if raw_token is None:
            raise RuntimeError("Session verification accepted an absent cookie")
        sessions.revoke(raw_token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(WEB_SESSION_COOKIE, path="/")
    response.delete_cookie(WEB_CSRF_COOKIE, path="/")
    return response
