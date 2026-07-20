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


def _error_page(title: str, description: str, status_code: int = 400) -> HTMLResponse:
    body = f"<h1>{escape(title)}</h1><p>{escape(description)}</p>"
    return HTMLResponse(content=body, status_code=status_code)


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


def _proposal_summary(proposal: Proposal) -> str:
    payload = proposal.payload
    proposal_type = payload.get("type", "?")
    campaign_id = payload.get("campaign_id", "?")
    return f"{proposal_type} / kampanya {campaign_id}"


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
            summary = escape(_proposal_summary(proposal))
            customer = escape(proposal.customer_id)
            expires = escape(proposal.expires_at.isoformat())
            proposal_id = escape(proposal.proposal_id)
            rows.append(
                "<li>"
                f"<p>Hesap {customer} — {summary} (son geçerlilik: {expires})</p>"
                f'<form method="post" action="/approvals/{proposal_id}/decision">'
                f'<input type="hidden" name="csrf_token" value="{csrf}" />'
                f'<button type="submit" name="decision" value="approve">Onayla: {summary}</button>'
                f'<button type="submit" name="decision" value="reject">Reddet: {summary}</button>'
                "</form>"
                "</li>"
            )
        body = (
            "<h1>Bekleyen öneriler</h1>"
            f"<ul>{''.join(rows) if rows else '<li>Bekleyen öneri yok.</li>'}</ul>"
            '<form method="post" action="/logout">'
            f'<input type="hidden" name="csrf_token" value="{csrf}" />'
            '<button type="submit">Çıkış yap</button>'
            "</form>"
            '<form method="post" action="/disconnect">'
            f'<input type="hidden" name="csrf_token" value="{csrf}" />'
            '<button type="submit">Bağlantıyı kes (disconnect)</button>'
            "</form>"
        )
        return HTMLResponse(content=body)


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
