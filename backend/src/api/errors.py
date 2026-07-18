"""Google Ads error taxonomy (docs/ERROR_HANDLING.md -- "Karar" table).

Classifies every failure the reporting adapter can see -- a structured
``GoogleAdsException`` (the request reached the API and got a business error)
or a transport-level failure (grpc/``google.api_core`` -- the request never
got a structured response) -- into the five decision-table classes and a
stable, safe ``AdsApiError``. Nothing here ever carries a token, secret or
full request payload (docs/SECURITY.md -- "Istek hatalarinda ... credential
ve hassas payload kaydedilmez").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import grpc
from google.ads.googleads.errors import GoogleAdsException
from google.api_core import exceptions as core_exceptions
from google.auth.exceptions import RefreshError


class ErrorClass(StrEnum):
    """The five Google-facing rows of the ERROR_HANDLING.md decision table.

    ``Internal invariant`` (the sixth row) is deliberately absent: it is
    raised directly by our own domain code (e.g. ``approval.domain``) for
    violations that have nothing to do with a Google Ads response.
    """

    VALIDATION = "validation"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    SYNC_STALE = "sync_stale"


#: Only these two classes are safe to retry automatically (ERROR_HANDLING.md:
#: "Read cagrilari guvenli bicimde retry edilebilir" applies to RATE_LIMIT and
#: TRANSIENT; SYNC_STALE explicitly gets "Kor retry yok" -- a caller must
#: re-read first, never blindly repeat the same request).
RETRYABLE_CLASSES: Final[frozenset[ErrorClass]] = frozenset(
    {ErrorClass.RATE_LIMIT, ErrorClass.TRANSIENT}
)

#: ``ErrorCode`` oneof field names that map unambiguously to a class. Every
#: other oneof field (the ~160 business/policy-rule errors, e.g.
#: ``campaign_error``, ``ad_group_error``) defaults to VALIDATION, which is
#: the safe (non-retryable, user-actionable) default for an unrecognised code.
_AUTH_FIELDS: Final[frozenset[str]] = frozenset({"authentication_error", "authorization_error"})
_RATE_LIMIT_FIELDS: Final[frozenset[str]] = frozenset({"quota_error"})

#: ``internal_error``/``request_error`` enum *value* names that mean "this was
#: a transient backend/deadline problem", not a real business error.
_TRANSIENT_VALUE_NAMES: Final[frozenset[str]] = frozenset(
    {"TRANSIENT_ERROR", "DEADLINE_EXCEEDED", "INTERNAL_ERROR", "UNKNOWN"}
)

#: Enum *value* names that mean the resource we asked about moved/vanished
#: since our last read -- ERROR_HANDLING.md's "Sync/stale" row.
_SYNC_STALE_VALUE_NAMES: Final[frozenset[str]] = frozenset(
    {"RESOURCE_NOT_FOUND", "EXPIRED_PAGE_TOKEN", "INVALID_PAGE_TOKEN"}
)

#: grpc status codes that never reach Google's structured ``GoogleAdsFailure``
#: parsing -- a bare transport failure is always TRANSIENT and safe to retry.
_TRANSIENT_GRPC_CODES: Final[frozenset[grpc.StatusCode]] = frozenset(
    {
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.ABORTED,
        grpc.StatusCode.INTERNAL,
    }
)


class AdsApiError(Exception):
    """A safe, stable failure raised in place of any Google Ads exception.

    ``message`` is Google's own error text (already meant to be user-facing);
    it never contains a token, credential or the full request. ``code`` is a
    stable machine string a caller/UI can branch on; ``request_id`` is
    Google's own correlation ID, captured for support without logging
    anything sensitive (docs/ERROR_HANDLING.md).
    """

    def __init__(
        self,
        *,
        error_class: ErrorClass,
        code: str,
        message: str,
        request_id: str | None,
        field_path: str | None = None,
        retry_delay_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.code = code
        self.message = message
        self.request_id = request_id
        self.field_path = field_path
        self.retry_delay_seconds = retry_delay_seconds

    @property
    def retryable(self) -> bool:
        return self.error_class in RETRYABLE_CLASSES


def _field_path(location: object) -> str | None:
    elements = getattr(location, "field_path_elements", None)
    if not elements:
        return None
    parts: list[str] = []
    for element in elements:
        name = element.field_name
        if "index" in element:
            name = f"{name}[{element.index}]"
        parts.append(name)
    return ".".join(parts)


def _retry_delay_seconds(details: object) -> float | None:
    quota_details = getattr(details, "quota_error_details", None)
    duration = getattr(quota_details, "retry_delay", None)
    if duration is None:
        return None
    seconds = getattr(duration, "seconds", 0) or 0
    nanos = getattr(duration, "nanos", 0) or 0
    total = seconds + nanos / 1_000_000_000
    return total if total > 0 else None


def classify_google_ads_exception(exc: GoogleAdsException) -> AdsApiError:
    """Classify a structured API failure using its first reported error.

    Google Ads may report several errors per failed request; the first one
    is treated as authoritative for classification, matching how the
    official client surfaces it (``exc.failure.errors[0]``).
    """
    errors = list(exc.failure.errors)
    if not errors:
        return AdsApiError(
            error_class=ErrorClass.TRANSIENT,
            code="empty_failure",
            message="Google Ads bos bir hata govdesi dondurdu.",
            request_id=exc.request_id,
        )

    error = errors[0]
    error_code = error.error_code
    field_name = type(error_code).pb(error_code).WhichOneof("error_code") or "unknown_error"
    value = getattr(error_code, field_name, None)
    value_name = value.name if value is not None else "UNKNOWN"

    if field_name in _AUTH_FIELDS:
        error_class = ErrorClass.AUTH
    elif field_name in _RATE_LIMIT_FIELDS:
        error_class = ErrorClass.RATE_LIMIT
    elif value_name in _SYNC_STALE_VALUE_NAMES:
        error_class = ErrorClass.SYNC_STALE
    elif field_name == "internal_error" and value_name in _TRANSIENT_VALUE_NAMES:
        error_class = ErrorClass.TRANSIENT
    else:
        error_class = ErrorClass.VALIDATION

    return AdsApiError(
        error_class=error_class,
        code=f"{field_name}.{value_name}".lower(),
        message=error.message or "Google Ads istegi basarisiz oldu.",
        request_id=exc.request_id,
        field_path=_field_path(error.location),
        retry_delay_seconds=_retry_delay_seconds(error.details),
    )


def classify_transport_error(exc: Exception) -> AdsApiError:
    """Classify a failure that never produced a structured ``GoogleAdsFailure``.

    Covers a bare ``grpc.RpcError`` (network drop, unavailable, deadline) and
    the ``google.api_core.exceptions`` hierarchy the underlying gRPC/REST
    transport can also raise. Anything unrecognised fails closed as
    non-retryable rather than risking a blind retry loop.
    """
    if isinstance(exc, RefreshError):
        return AdsApiError(
            error_class=ErrorClass.AUTH,
            code="transport.refresh_error",
            message="Google OAuth refresh token gecersiz veya iptal edilmis.",
            request_id=None,
        )
    if isinstance(exc, (core_exceptions.Unauthenticated, core_exceptions.PermissionDenied)):
        return AdsApiError(
            error_class=ErrorClass.AUTH,
            code="transport.unauthenticated",
            message="Google kimlik dogrulamasi basarisiz oldu.",
            request_id=None,
        )
    if isinstance(exc, core_exceptions.ResourceExhausted):
        return AdsApiError(
            error_class=ErrorClass.RATE_LIMIT,
            code="transport.resource_exhausted",
            message="Google Ads kota/rate limit asildi.",
            request_id=None,
        )
    if isinstance(
        exc,
        (
            core_exceptions.ServiceUnavailable,
            core_exceptions.DeadlineExceeded,
            core_exceptions.InternalServerError,
            core_exceptions.Aborted,
        ),
    ):
        return AdsApiError(
            error_class=ErrorClass.TRANSIENT,
            code="transport.unavailable",
            message="Google Ads gecici olarak yanit vermedi.",
            request_id=None,
        )
    if isinstance(exc, grpc.RpcError):
        code = exc.code() if callable(getattr(exc, "code", None)) else None
        if code in _TRANSIENT_GRPC_CODES:
            return AdsApiError(
                error_class=ErrorClass.TRANSIENT,
                code=f"transport.{(code.name if code else 'unknown').lower()}",
                message="Google Ads gecici olarak yanit vermedi.",
                request_id=None,
            )
        if code is grpc.StatusCode.UNAUTHENTICATED or code is grpc.StatusCode.PERMISSION_DENIED:
            return AdsApiError(
                error_class=ErrorClass.AUTH,
                code=f"transport.{code.name.lower()}",
                message="Google kimlik dogrulamasi basarisiz oldu.",
                request_id=None,
            )
        if code is grpc.StatusCode.RESOURCE_EXHAUSTED:
            return AdsApiError(
                error_class=ErrorClass.RATE_LIMIT,
                code="transport.resource_exhausted",
                message="Google Ads kota/rate limit asildi.",
                request_id=None,
            )
    return AdsApiError(
        error_class=ErrorClass.VALIDATION,
        code="transport.unclassified",
        message="Google Ads istegi siniflandirilamayan bir hatayla basarisiz oldu.",
        request_id=None,
    )
