"""Principal-scoped HTTP API routes for connector clients.

These routes intentionally reuse the connector bearer-token verifier instead of
browser sessions. The caller's ``principal_id`` is derived from the audience-bound
access token; it is never accepted as request input.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ..api.errors import AdsApiError
from ..api.problems import problem_response
from ..api.queries import validate_customer_id
from ..approval.serialization import proposal_to_dict
from ..auth.context import AuthContext, get_context
from ..auth.deps import AuthenticatedPrincipal
from ..auth.deps import BearerTokenError, extract_bearer_token, verify_access_token, www_authenticate_header
from ..db.oauth_store import TokenRepository
from ..db.proposals import MAX_PENDING_PROPOSAL_LIMIT, ProposalRepository
from ..db.repository import AdsAccountRepository

router = APIRouter(prefix="/api/v1")


def _auth_problem(
    *,
    description: str,
    www_authenticate: str,
    correlation_id: str | None,
) -> JSONResponse:
    return problem_response(
        status_code=401,
        title="Authentication required",
        detail=description,
        code="invalid_token",
        correlation_id=correlation_id,
        headers={"WWW-Authenticate": www_authenticate},
    )


def _correlation_id(request: Request) -> str | None:
    scope_correlation_id = request.scope.get("correlation_id")
    return scope_correlation_id if isinstance(scope_correlation_id, str) else None


def _problem_response(
    *,
    status_code: int,
    title: str,
    detail: str,
    code: str,
    correlation_id: str | None,
) -> JSONResponse:
    return problem_response(
        status_code=status_code,
        title=title,
        detail=detail,
        code=code,
        correlation_id=correlation_id,
    )


def _authenticate(request: Request, context: AuthContext) -> AuthenticatedPrincipal | JSONResponse:
    raw_token = extract_bearer_token(request.headers.get("Authorization"))
    www_authenticate = www_authenticate_header(
        protected_resource_metadata_url=f"{context.settings.public_base_url.rstrip('/')}/.well-known/oauth-protected-resource",
        error="invalid_token" if raw_token else None,
    )
    if raw_token is None:
        return _auth_problem(
            description="Authentication required.",
            www_authenticate=www_authenticate,
            correlation_id=_correlation_id(request),
        )

    try:
        return verify_access_token(
            raw_token,
            TokenRepository(context.conn),
            expected_resource=context.settings.mcp_resource_uri,
            now=datetime.now(timezone.utc),
        )
    except BearerTokenError as error:
        return _auth_problem(
            description=error.description,
            www_authenticate=www_authenticate,
            correlation_id=_correlation_id(request),
        )


def _verify_account_ownership(
    request: Request,
    context: AuthContext,
    principal_id: str,
    customer_id: str,
) -> JSONResponse | None:
    try:
        validate_customer_id(customer_id)
    except AdsApiError as error:
        return _problem_response(
            status_code=400,
            title="Invalid customer_id",
            detail=error.message,
            code=error.code,
            correlation_id=_correlation_id(request),
        )
    account = AdsAccountRepository(context.conn).get_active_account(principal_id, customer_id)
    if account is None:
        return _problem_response(
            status_code=404,
            title="Account not found",
            detail="Bu customer_id bu baglantiya ait degil veya bulunamadi.",
            code="account_not_linked",
            correlation_id=_correlation_id(request),
        )
    return None


@router.get("/accounts")
async def list_accounts(request: Request, context: AuthContext = Depends(get_context)) -> JSONResponse:
    """List Google Ads accounts linked to the authenticated connector principal."""
    principal = _authenticate(request, context)
    if isinstance(principal, JSONResponse):
        return principal

    accounts = AdsAccountRepository(context.conn).list_active_accounts(principal.principal_id)
    return JSONResponse(
        {
            "accounts": [
                {
                    "customer_id": account.customer_id,
                    "login_customer_id": account.login_customer_id,
                    "status": account.status,
                }
                for account in accounts
            ]
        }
    )


@router.get("/proposals")
async def list_proposals(
    request: Request,
    customer_id: str | None = None,
    limit: str = "50",
    context: AuthContext = Depends(get_context),
) -> JSONResponse:
    """List pending, unexpired proposals owned by the authenticated connector principal."""
    principal = _authenticate(request, context)
    if isinstance(principal, JSONResponse):
        return principal
    try:
        parsed_limit = int(limit)
    except ValueError:
        parsed_limit = 0
    if not (1 <= parsed_limit <= MAX_PENDING_PROPOSAL_LIMIT):
        return _problem_response(
            status_code=400,
            title="Invalid limit",
            detail=f"limit 1 ile {MAX_PENDING_PROPOSAL_LIMIT} arasinda olmalidir.",
            code="invalid_limit",
            correlation_id=_correlation_id(request),
        )
    if customer_id is not None:
        ownership_error = _verify_account_ownership(request, context, principal.principal_id, customer_id)
        if ownership_error is not None:
            return ownership_error

    proposals = ProposalRepository(context.conn).list_pending(
        principal.principal_id,
        customer_id=customer_id,
        limit=parsed_limit,
    )
    return JSONResponse({"proposals": [proposal_to_dict(proposal) for proposal in proposals]})


@router.get("/proposals/{proposal_id}")
async def get_proposal(
    proposal_id: str,
    request: Request,
    context: AuthContext = Depends(get_context),
) -> JSONResponse:
    """Return one proposal only if it belongs to the authenticated connector principal."""
    principal = _authenticate(request, context)
    if isinstance(principal, JSONResponse):
        return principal

    proposal = ProposalRepository(context.conn).get(principal.principal_id, proposal_id)
    if proposal is None:
        return _problem_response(
            status_code=404,
            title="Proposal not found",
            detail="Bu proposal_id bu baglantiya ait degil veya bulunamadi.",
            code="proposal_not_found",
            correlation_id=_correlation_id(request),
        )
    return JSONResponse(proposal_to_dict(proposal))
