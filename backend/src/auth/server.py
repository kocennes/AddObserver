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

import secrets
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..api.identifiers import validate_opaque_id
from ..config import Settings
from ..db.oauth_store import (
    AuthorizationCodeRepository,
    AuthorizationTransactionRepository,
    ClientGrantRepository,
    TokenRepository,
)
from ..db.postgres_repository import PostgresAuthorizationTransactionRepository
from ..db.repository import OAuthCredentialRepository, PrincipalRepository
from .approvals_routes import handle_web_login_callback
from .cimd import fetch_client_metadata
from .context import AuthContext, get_context
from .domain import (
    AuthError,
    AuthorizationTransaction,
    RefreshOutcome,
    complete_transaction,
    consent_transaction,
    consume_authorization_code,
    hash_token,
    issue_authorization_code,
    issue_token_pair,
    verify_consent_csrf,
)

router = APIRouter()
AUTHORIZE_CSRF_COOKIE = "authorize_csrf"

AuthorizationTransactionStore = (
    AuthorizationTransactionRepository | PostgresAuthorizationTransactionRepository
)


@contextmanager
def _authorization_transactions(
    context: AuthContext,
) -> Iterator[AuthorizationTransactionStore]:
    """Yield the configured store inside one short database transaction."""
    if context.postgres_uow_factory is None:
        yield AuthorizationTransactionRepository(context.conn)
        return

    with context.postgres_uow_factory.request() as work:
        if work.repositories is None:
            raise RuntimeError("PostgreSQL unit of work repository'leri kurulmadi")
        yield work.repositories.authorization_transactions


def _oauth_error(code: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": code, "error_description": description}
    )


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
async def authorization_server_metadata(
    context: AuthContext = Depends(get_context),
) -> JSONResponse:
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
    consent_csrf = secrets.token_urlsafe(32)
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
            consent_csrf_hash=hash_token(consent_csrf),
            now=datetime.now(UTC),
        )
    except AuthError as error:
        return _error_page("Yetkilendirme isteği geçersiz", str(error), 400)

    with _authorization_transactions(context) as transactions:
        transactions.save(transaction)

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
        <input type="hidden" name="csrf_token" value="{escape(consent_csrf)}" />
        <button type="submit" name="decision" value="approve">Google hesabımla bağlan</button>
        <button type="submit" name="decision" value="deny">Reddet</button>
    </form>
    """
    response = HTMLResponse(content=body)
    response.set_cookie(
        AUTHORIZE_CSRF_COOKIE,
        consent_csrf,
        httponly=True,
        secure=context.settings.environment != "local",
        samesite="strict",
        max_age=600,
        path="/authorize/consent",
    )
    return response


@router.post("/authorize/consent")
async def authorize_consent(
    request: Request,
    transaction_id: str = Form(...),
    decision: str = Form(...),
    context: AuthContext = Depends(get_context),
) -> Response:
    try:
        validate_opaque_id(transaction_id, field_name="transaction_id")
    except ValueError:
        return _error_page(
            "İşlem bulunamadı", "Yetkilendirme işlemi bulunamadı veya süresi dolmuş.", 400
        )

    with _authorization_transactions(context) as transactions:
        transaction = transactions.get(transaction_id)
        if transaction is None:
            return _error_page(
                "İşlem bulunamadı", "Yetkilendirme işlemi bulunamadı veya süresi dolmuş.", 400
            )

        try:
            verify_consent_csrf(
                request.cookies.get(AUTHORIZE_CSRF_COOKIE), transaction.consent_csrf_hash
            )
        except AuthError as error:
            return _error_page("Yetkilendirme onayı doğrulanamadı", str(error), 400)

        if decision != "approve":
            query = urlencode({"error": "access_denied", "state": transaction.client_state})
            return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

        try:
            consented = consent_transaction(transaction, now=datetime.now(UTC))
            transactions.save(consented)
        except AuthError as error:
            return _error_page("İşlem geçersiz", str(error), 400)

    google_url = context.google_client.build_authorization_url(state=transaction_id)
    return RedirectResponse(url=google_url, status_code=302)


# ---------------------------------------------------------------------------
# /google/callback -- completes the upstream Google leg, issues OUR own code.
# ---------------------------------------------------------------------------


@router.get("/google/callback")
async def google_callback(
    state: str = Query(...),
    code: str | None = Query(None),
    error: str | None = Query(None),
    context: AuthContext = Depends(get_context),
) -> Response:
    try:
        validate_opaque_id(state, field_name="state")
    except ValueError:
        return _error_page(
            "İşlem bulunamadı", "Google geri çağrısı bilinmeyen bir işlem içeriyor.", 400
        )

    transactions = AuthorizationTransactionRepository(context.conn)
    transaction = transactions.get(state)
    if transaction is None:
        # Not a Claude-client transaction -- try the /approvals login fallback
        # (docs/AUTH.md "Approval-UI web girişi").
        return await handle_web_login_callback(state, code=code, error=error, context=context)

    if error is not None or code is None:
        query = urlencode({"error": "access_denied", "state": transaction.client_state})
        return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

    now = datetime.now(UTC)
    try:
        google_result = context.google_client.exchange_code(code=code)
    except Exception:  # noqa: BLE001 -- classified below into a safe redirect
        query = urlencode({"error": "server_error", "state": transaction.client_state})
        return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

    # Google lets a user approve some scopes on a multi-scope consent screen while
    # declining others -- the redirect still carries a successful `code`, so this can
    # only be caught here, not via the `error=` branch above. `adwords` is this
    # connector's entire reason for existing; treat a partial grant that excludes it
    # the same as an outright denial (docs/AUTH.md "Upstream Google OAuth" -- "scope
    # denial", todo.md 3.6) and never persist a credential that cannot do what it was
    # requested for.
    if (
        google_result.granted_scopes is not None
        and "https://www.googleapis.com/auth/adwords" not in google_result.granted_scopes
    ):
        query = urlencode({"error": "access_denied", "state": transaction.client_state})
        return RedirectResponse(url=f"{transaction.redirect_uri}?{query}", status_code=302)

    principal = PrincipalRepository(context.conn).get_or_create(
        "https://accounts.google.com", google_result.google_subject
    )
    vault_ref = context.vault.store(google_result.refresh_token)
    OAuthCredentialRepository(context.conn).upsert(principal.id, vault_ref, key_version=1)
    ClientGrantRepository(context.conn).record_consent(
        principal.id, transaction.client_id, transaction.scope
    )

    try:
        # issue_authorization_code requires CONSENTED status (auth/domain.py), so the
        # code must be minted before complete_transaction flips the transaction to
        # COMPLETED -- doing this in the opposite order made every real callback fail
        # with "Onay tamamlanmadan kod uretilemez." (no HTTP test drove this far until
        # test_auth_authorization_flow_http.py, todo.md 3.3).
        auth_code = issue_authorization_code(transaction, principal_id=principal.id, now=now)
        completed = complete_transaction(transaction, now=now)
        transactions.save(completed)
    except AuthError as auth_error:
        return _error_page("İşlem tamamlanamadı", str(auth_error), 400)
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
            "token_type": "Bearer",  # nosec B105 -- RFC 6749 token_type literal, not a credential
            "expires_in": int((access_token.expires_at - datetime.now(UTC)).total_seconds()),
            "refresh_token": refresh_token.token,
            "scope": access_token.scope,
        }
    )


@router.post("/token")
async def token(
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    client_id: str | None = Form(None),
    code_verifier: str | None = Form(None),
    resource: str | None = Form(None),
    refresh_token: str | None = Form(None),
    context: AuthContext = Depends(get_context),
) -> JSONResponse:
    now = datetime.now(UTC)
    if context.postgres_uow_factory is not None:
        return _postgres_token_response(
            context=context,
            grant_type=grant_type,
            code=code,
            redirect_uri=redirect_uri,
            client_id=client_id,
            code_verifier=code_verifier,
            resource=resource,
            raw_refresh_token=refresh_token,
            now=now,
        )
    codes = AuthorizationCodeRepository(context.conn)
    tokens = TokenRepository(context.conn)

    if grant_type == "authorization_code":
        if not code or not redirect_uri or not client_id or not code_verifier or not resource:
            return _oauth_error(
                "invalid_request", "authorization_code grant icin zorunlu alanlar eksik."
            )
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
            return _oauth_error(
                "invalid_request", "refresh_token grant icin refresh_token zorunlu."
            )
        try:
            outcome = tokens.rotate(refresh_token, now=now)
        except AuthError as error:
            return _oauth_error(error.code, str(error))
        return _token_response(outcome.access_token, outcome.refresh_token)

    return _oauth_error("unsupported_grant_type", f"Desteklenmeyen grant_type: {grant_type}")


def _postgres_token_response(
    *,
    context: AuthContext,
    grant_type: str,
    code: str | None,
    redirect_uri: str | None,
    client_id: str | None,
    code_verifier: str | None,
    resource: str | None,
    raw_refresh_token: str | None,
    now: datetime,
) -> JSONResponse:
    """Execute a token grant inside one PostgreSQL RLS unit of work."""
    assert context.postgres_uow_factory is not None  # nosec B101 - guarded by caller
    if grant_type == "authorization_code":
        if not code or not redirect_uri or not client_id or not code_verifier or not resource:
            return _oauth_error(
                "invalid_request", "authorization_code grant icin zorunlu alanlar eksik."
            )
        try:
            with context.postgres_uow_factory.request() as work:
                if work.bootstrap_authorization_code(code) is None:
                    raise AuthError("invalid_grant", "Yetkilendirme kodu bulunamadi.")
                assert work.repositories is not None  # nosec B101 - entered unit of work
                stored, already_consumed = work.repositories.authorization_codes.claim(code)
                grant = consume_authorization_code(
                    stored,
                    client_id=client_id,
                    redirect_uri=redirect_uri,
                    resource=resource,
                    code_verifier=code_verifier,
                    already_consumed=already_consumed,
                    now=now,
                )
                access, refresh = issue_token_pair(grant, now=now)
                work.repositories.tokens.save_access(access)
                work.repositories.tokens.save_refresh(refresh)
        except AuthError as error:
            return _oauth_error(error.code, str(error))
        return _token_response(access, refresh)

    if grant_type == "refresh_token":
        if not raw_refresh_token:
            return _oauth_error(
                "invalid_request", "refresh_token grant icin refresh_token zorunlu."
            )
        refresh_error: AuthError | None = None
        outcome: RefreshOutcome | None = None
        with context.postgres_uow_factory.request() as work:
            try:
                if work.bootstrap_refresh_token(raw_refresh_token) is None:
                    raise AuthError("invalid_grant", "refresh_token bulunamadi.")
                assert work.repositories is not None  # nosec B101 - entered unit of work
                outcome = work.repositories.tokens.rotate(raw_refresh_token, now=now)
            except AuthError as error:
                # Replay detection deliberately revokes the complete token family. Keep the
                # exception inside the unit-of-work so that security state commits; unexpected
                # exceptions still escape and roll the transaction back.
                refresh_error = error
        if refresh_error is not None:
            return _oauth_error(refresh_error.code, str(refresh_error))
        if outcome is None:
            raise RuntimeError("Refresh rotation outcome eksik")
        return _token_response(outcome.access_token, outcome.refresh_token)

    return _oauth_error("unsupported_grant_type", f"Desteklenmeyen grant_type: {grant_type}")
