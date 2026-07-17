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
from datetime import datetime, timezone
from html import escape
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..approval import ApprovalError, Decision, Proposal, approve_proposal
from ..db.models import AuditEvent
from ..db.oauth_store import TokenRepository
from ..db.proposals import ApprovalRepository, AuditRepository, ProposalRepository
from ..db.repository import AdsAccountRepository, OAuthCredentialRepository, PrincipalRepository
from ..db.web_session_store import WebLoginStateRepository, WebSessionRepository
from .context import AuthContext, get_context
from .disconnect import disconnect_principal
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


def _error_page(title: str, description: str, status_code: int = 400) -> HTMLResponse:
    body = f"<h1>{escape(title)}</h1><p>{escape(description)}</p>"
    return HTMLResponse(content=body, status_code=status_code)


async def handle_web_login_callback(
    state: str, *, code: Optional[str], error: Optional[str], context: AuthContext
) -> RedirectResponse:
    """The ``/approvals`` login fallback leg of ``/google/callback`` (called from ``server.py``
    when ``state`` doesn't match a pending Claude-client ``authorization_transaction``).

    Reuses that same redirect_uri instead of registering a second one -- adding or
    changing a redirect URI can trigger Google OAuth re-verification
    (docs/GOOGLE_API_ACCESS.md). Never calls ``vault.store``,
    ``OAuthCredentialRepository.upsert`` or ``ClientGrantRepository.record_consent``:
    a login must never rotate the stored Google Ads credential.
    """
    claimed = WebLoginStateRepository(context.conn).claim(state)
    if claimed is None:
        return _error_page("İşlem bulunamadı", "Google geri çağrısı bilinmeyen bir işlem içeriyor.", 400)
    already_consumed, expires_at = claimed
    now = datetime.now(timezone.utc)
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

    principal = PrincipalRepository(context.conn).get("https://accounts.google.com", google_result.google_subject)
    if principal is None:
        return _error_page(
            "Bağlantı bulunamadı",
            "Bu Google hesabıyla bağlı bir connector bulunamadı. Önce Claude üzerinden bağlanın.",
            403,
        )

    session = issue_web_session(principal.id, now=now)
    WebSessionRepository(context.conn).create(principal.id, session.token, session.csrf_token, session.expires_at)
    response = RedirectResponse(url="/approvals", status_code=302)
    response.set_cookie(
        "web_session",
        session.token,
        httponly=True,
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
    now = datetime.now(timezone.utc)
    login_state = issue_login_state(now=now)
    WebLoginStateRepository(context.conn).create(login_state.state, login_state.expires_at)
    url = context.login_google_client.build_authorization_url(state=login_state.state)
    return RedirectResponse(url=url, status_code=302)


def _require_session(request: Request, context: AuthContext) -> AuthenticatedWebSession:
    raw_token = request.cookies.get("web_session")
    if raw_token is None:
        raise AuthError("invalid_token", "Oturum bulunamadi.")
    lookup = WebSessionRepository(context.conn).lookup(raw_token)
    return verify_web_session(
        principal_id=lookup.principal_id,
        csrf_token=lookup.csrf_token,
        expires_at=lookup.expires_at,
        revoked=lookup.revoked,
        now=datetime.now(timezone.utc),
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
    try:
        session = _require_session(request, context)
    except AuthError:
        return RedirectResponse(url="/login", status_code=302)

    pending = ProposalRepository(context.conn).list_pending(session.principal_id)
    csrf = escape(session.csrf_token)
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
    try:
        session = _require_session(request, context)
        verify_csrf_token(csrf_token, session.csrf_token)
    except AuthError as error:
        return _error_page("Yetkisiz", str(error), 401)

    disconnect_principal(
        session.principal_id,
        tokens=TokenRepository(context.conn),
        credentials=OAuthCredentialRepository(context.conn),
        accounts=AdsAccountRepository(context.conn),
        vault=context.vault,
        audit=AuditRepository(context.conn),
        now=datetime.now(timezone.utc),
        correlation_id=_request_correlation_id(request),
    )

    raw_token = request.cookies.get("web_session")
    if raw_token:
        WebSessionRepository(context.conn).revoke(raw_token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("web_session", path="/")
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
    try:
        session = _require_session(request, context)
        verify_csrf_token(csrf_token, session.csrf_token)
    except AuthError as error:
        return _error_page("Yetkisiz", str(error), 401)

    proposals = ProposalRepository(context.conn)
    proposal = proposals.get(session.principal_id, proposal_id)
    if proposal is None:
        return _error_page("Bulunamadı", "Bu öneri bulunamadı veya bu bağlantıya ait değil.", 404)

    try:
        decision_enum = Decision(decision)
    except ValueError:
        return _error_page("Geçersiz karar", "decision yalnız 'approve' veya 'reject' olabilir.", 400)

    now = datetime.now(timezone.utc)
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

    ApprovalRepository(context.conn).save_decision_with_audit(
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
    try:
        session = _require_session(request, context)
        verify_csrf_token(csrf_token, session.csrf_token)
    except AuthError as error:
        return _error_page("Yetkisiz", str(error), 401)

    raw_token = request.cookies.get("web_session")
    assert raw_token is not None
    WebSessionRepository(context.conn).revoke(raw_token)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("web_session", path="/")
    return response
