"""FastAPI routes for the connector's own OAuth 2.1 authorization server (ADR-0002).

Route handlers are all ``async def`` and call the (synchronous) sqlite/httpx calls
directly, deliberately avoiding Starlette's default threadpool offload for sync
routes -- the underlying ``sqlite3.Connection`` is bound to the thread that created
it, and this keeps every call on that same thread. Production will replace sqlite
with PostgreSQL (ADR-0001/DATABASE.md) before concurrency matters.

Every response here follows an existing accepted contract: RFC 6749 JSON errors on
``/token``, RFC 9728/8414 discovery documents, and Anthropic's documented CIMD/
consent-screen requirements (see ADR-0002 and docs/AUTH.md for the research this is
based on).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from html import escape
from typing import Optional
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..db.oauth_store import (
    AuthorizationCodeRepository,
    AuthorizationTransactionRepository,
    ClientGrantRepository,
    TokenRepository,
)
from ..db.repository import OAuthCredentialRepository, PrincipalRepository
from .approvals_routes import handle_web_login_callback
from .cimd import fetch_client_metadata
from .context import AuthContext, get_context
from .domain import (
    AuthError,
    AuthorizationTransaction,
    complete_transaction,
    consent_transaction,
    consume_authorization_code,
    issue_authorization_code,
    issue_token_pair,
)

router = APIRouter()


def _oauth_error(code: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": code, "error_description": description})


def _error_page(title: str, description: str, status_code: int = 400) -> HTMLResponse:
    body = f"<h1>{escape(title)}</h1><p>{escape(description)}</p>"
    return HTMLResponse(content=body, status_code=status_code)


# ---------------------------------------------------------------------------
# Discovery (RFC 9728 protected-resource metadata, RFC 8414 AS metadata)
# ---------------------------------------------------------------------------

def _protected_resource_metadata(settings: Settings) -> dict:
    return {
        "resource": settings.mcp_resource_uri,
        "authorization_servers": [settings.public_base_url],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(context: AuthContext = Depends(get_context)) -> JSONResponse:
    return JSONResponse(_protected_resource_metadata(context.settings))


@router.get("/.well-known/oauth-protected-resource{path:path}")
async def protected_resource_metadata_path_suffixed(
    path: str, context: AuthContext = Depends(get_context)
) -> JSONResponse:
    """RFC 9728 s3.1 path-aware variant, e.g. ``/.well-known/oauth-protected-resource/mcp``."""
    return JSONResponse(_protected_resource_metadata(context.settings))


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(context: AuthContext = Depends(get_context)) -> JSONResponse:
    base = context.settings.public_base_url.rstrip("/")
    return JSONResponse(
        {
            "issuer": context.settings.public_base_url,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "scopes_supported": ["adwords", "offline_access"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": True,
        }
    )


# ---------------------------------------------------------------------------
# /authorize -- resolves the CIMD client, opens a transaction, renders consent.
# ---------------------------------------------------------------------------

@router.get("/authorize")
async def authorize(
    request: Request,
    response_type: str = Query(...),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(...),
    resource: str = Query(...),
    scope: str = Query(""),
    state: str = Query(...),
    context: AuthContext = Depends(get_context),
) -> HTMLResponse:
    if response_type != "code":
        return _error_page("Desteklenmeyen response_type", "Yalnız 'code' desteklenir.", 400)

    kwargs = {"resolve": context.resolve} if context.resolve is not None else {}
    try:
        client = fetch_client_metadata(client_id, context.http_client, **kwargs)
    except AuthError as error:
        return _error_page("İstemci doğrulanamadı", str(error), 400)

    transaction_id = str(uuid.uuid4())
    try:
        transaction = AuthorizationTransaction.create(
            transaction_id=transaction_id,
            client=client,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
            expected_resource=context.settings.mcp_resource_uri,
            scope=scope,
            client_state=state,
            now=datetime.now(timezone.utc),
        )
    except AuthError as error:
        return _error_page("Yetkilendirme isteği geçersiz", str(error), 400)

    AuthorizationTransactionRepository(context.conn).save(transaction)

    redirect_host = urlsplit(redirect_uri).hostname or redirect_uri
    client_host = urlsplit(client_id).hostname or client_id
    loopback_warning = ""
    if redirect_host in {"localhost", "127.0.0.1", "::1"}:
        loopback_warning = (
            "<p><strong>Uyarı:</strong> Bu istek yerel bir uygulamadan (loopback) geliyor. "
            "Bu uygulamayı başlattığınızdan emin olmadıysanız onaylamayın.</p>"
        )

    body = f"""
    <h1>AddObserver connector erişim isteği</h1>
    <p><strong>{escape(client_host)}</strong> adresindeki uygulama Google Ads hesabınıza
    salt-okunur analiz ve (onayınızla) sınırlı değişiklik erişimi istiyor.</p>
    <p>Yönlendirme adresi (redirect_uri): <code>{escape(redirect_uri)}</code></p>
    {loopback_warning}
    <form method="post" action="/authorize/consent">
        <input type="hidden" name="transaction_id" value="{escape(transaction_id)}" />
        <button type="submit" name="decision" value="approve">Google hesabımla bağlan</button>
        <button type="submit" name="decision" value="deny">Reddet</button>
    </form>
    """
    return HTMLResponse(content=body)


@router.post("/authorize/consent")
async def authorize_consent(
    transaction_id: str = Form(...),
    decision: str = Form(...),
    context: AuthContext = Depends(get_context),
) -> RedirectResponse:
    transactions = AuthorizationTransactionRepository(context.conn)
    transaction = transactions.get(transaction_id)
    if transaction is None:
        return _error_page("İşlem bulunamadı", "Yetkilendirme işlemi bulunamadı veya süresi dolmuş.", 400)

    if decision != "approve":
        query = urlencode({"error": "access_denied", "state": transaction.client_state})
        return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

    try:
        consented = consent_transaction(transaction, now=datetime.now(timezone.utc))
    except AuthError as error:
        return _error_page("İşlem geçersiz", str(error), 400)
    transactions.save(consented)

    google_url = context.google_client.build_authorization_url(state=transaction_id)
    return RedirectResponse(url=google_url, status_code=302)


# ---------------------------------------------------------------------------
# /google/callback -- completes the upstream Google leg, issues OUR own code.
# ---------------------------------------------------------------------------

@router.get("/google/callback")
async def google_callback(
    state: str = Query(...),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    context: AuthContext = Depends(get_context),
) -> RedirectResponse:
    transactions = AuthorizationTransactionRepository(context.conn)
    transaction = transactions.get(state)
    if transaction is None:
        # Not a Claude-client transaction -- try the /approvals login fallback
        # (docs/AUTH.md "Approval-UI web girişi").
        return await handle_web_login_callback(state, code=code, error=error, context=context)

    if error is not None or code is None:
        query = urlencode({"error": "access_denied", "state": transaction.client_state})
        return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

    now = datetime.now(timezone.utc)
    try:
        google_result = context.google_client.exchange_code(code=code)
    except Exception as exc:  # noqa: BLE001 -- classified below into a safe redirect
        query = urlencode({"error": "server_error", "state": transaction.client_state})
        return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

    principal = PrincipalRepository(context.conn).get_or_create(
        "https://accounts.google.com", google_result.google_subject
    )
    vault_ref = context.vault.store(google_result.refresh_token)
    OAuthCredentialRepository(context.conn).upsert(principal.id, vault_ref, key_version=1)
    ClientGrantRepository(context.conn).record_consent(principal.id, transaction.client_id, transaction.scope)

    try:
        completed = complete_transaction(transaction, now=now)
        transactions.save(completed)
        auth_code = issue_authorization_code(completed, principal_id=principal.id, now=now)
    except AuthError as error:
        return _error_page("İşlem tamamlanamadı", str(error), 400)
    AuthorizationCodeRepository(context.conn).save(auth_code)

    query = urlencode({"code": auth_code.code, "state": transaction.client_state})
    return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)


# ---------------------------------------------------------------------------
# /token -- authorization_code and refresh_token grants (RFC 6749 JSON errors).
# ---------------------------------------------------------------------------

def _token_response(access_token, refresh_token) -> JSONResponse:
    return JSONResponse(
        {
            "access_token": access_token.token,
            "token_type": "Bearer",
            "expires_in": int((access_token.expires_at - datetime.now(timezone.utc)).total_seconds()),
            "refresh_token": refresh_token.token,
            "scope": access_token.scope,
        }
    )


@router.post("/token")
async def token(
    grant_type: str = Form(...),
    code: Optional[str] = Form(None),
    redirect_uri: Optional[str] = Form(None),
    client_id: Optional[str] = Form(None),
    code_verifier: Optional[str] = Form(None),
    resource: Optional[str] = Form(None),
    refresh_token: Optional[str] = Form(None),
    context: AuthContext = Depends(get_context),
) -> JSONResponse:
    now = datetime.now(timezone.utc)
    codes = AuthorizationCodeRepository(context.conn)
    tokens = TokenRepository(context.conn)

    if grant_type == "authorization_code":
        if not all([code, redirect_uri, client_id, code_verifier, resource]):
            return _oauth_error("invalid_request", "authorization_code grant icin zorunlu alanlar eksik.")
        try:
            stored, already_consumed = codes.claim(code)
            grant = consume_authorization_code(
                stored,
                client_id=client_id,
                redirect_uri=redirect_uri,
                resource=resource,
                code_verifier=code_verifier,
                already_consumed=already_consumed,
                now=now,
            )
        except AuthError as error:
            return _oauth_error(error.code, str(error))
        access, refresh = issue_token_pair(grant, now=now)
        tokens.save_access(access)
        tokens.save_refresh(refresh)
        return _token_response(access, refresh)

    if grant_type == "refresh_token":
        if not refresh_token:
            return _oauth_error("invalid_request", "refresh_token grant icin refresh_token zorunlu.")
        try:
            outcome = tokens.rotate(refresh_token, now=now)
        except AuthError as error:
            return _oauth_error(error.code, str(error))
        return _token_response(outcome.access_token, outcome.refresh_token)

    return _oauth_error("unsupported_grant_type", f"Desteklenmeyen grant_type: {grant_type}")
