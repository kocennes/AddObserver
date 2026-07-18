"""ERROR_HANDLING.md decision-table classification tests.

Uses genuine ``google.ads.googleads`` proto types throughout (docs/TESTING.md
mock policy), never a hand-rolled failure shape.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import grpc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.errors import (
    ErrorClass,
    classify_google_ads_exception,
    classify_transport_error,
)
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v24.errors.types import (
    authentication_error,
    internal_error,
    quota_error,
    request_error,
)
from google.ads.googleads.v24.errors.types import errors as error_types
from google.api_core import exceptions as core_exceptions


class _FakeRpcCall(grpc.Call, grpc.RpcError):
    def __init__(self, code: grpc.StatusCode) -> None:
        self._code = code

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return "fake"


def _exception_for(
    error: error_types.GoogleAdsError, *, request_id: str = "req-1"
) -> GoogleAdsException:
    failure = error_types.GoogleAdsFailure(errors=[error], request_id=request_id)
    call = _FakeRpcCall(grpc.StatusCode.INVALID_ARGUMENT)
    return GoogleAdsException(error=call, call=call, failure=failure, request_id=request_id)


class ClassifyGoogleAdsExceptionTests(unittest.TestCase):
    def test_quota_error_is_rate_limit_and_retryable_with_delay_floor(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                quota_error=quota_error.QuotaErrorEnum.QuotaError.RESOURCE_EXHAUSTED
            ),
            message="Too many requests",
            details=error_types.ErrorDetails(
                quota_error_details=error_types.QuotaErrorDetails(retry_delay={"seconds": 5})
            ),
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.error_class, ErrorClass.RATE_LIMIT)
        self.assertTrue(result.retryable)
        self.assertEqual(result.retry_delay_seconds, 5.0)
        self.assertEqual(result.request_id, "req-1")

    def test_authentication_error_is_auth_and_not_retryable(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                authentication_error=authentication_error.AuthenticationErrorEnum.AuthenticationError.OAUTH_TOKEN_EXPIRED
            ),
            message="OAuth token expired",
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.error_class, ErrorClass.AUTH)
        self.assertFalse(result.retryable)

    def test_internal_transient_error_is_transient_and_retryable(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                internal_error=internal_error.InternalErrorEnum.InternalError.TRANSIENT_ERROR
            ),
            message="Backend hiccup",
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.error_class, ErrorClass.TRANSIENT)
        self.assertTrue(result.retryable)

    def test_resource_not_found_is_sync_stale_and_not_retryable(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                request_error=request_error.RequestErrorEnum.RequestError.RESOURCE_NOT_FOUND
            ),
            message="Resource no longer exists",
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.error_class, ErrorClass.SYNC_STALE)
        self.assertFalse(result.retryable)

    def test_unrecognised_business_error_defaults_to_validation(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                request_error=request_error.RequestErrorEnum.RequestError.INVALID_PAGE_SIZE
            ),
            message="page_size gecersiz",
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.error_class, ErrorClass.VALIDATION)
        self.assertFalse(result.retryable)

    def test_field_path_is_extracted_from_location(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                request_error=request_error.RequestErrorEnum.RequestError.REQUIRED_FIELD_MISSING
            ),
            message="alan eksik",
            location=error_types.ErrorLocation(
                field_path_elements=[
                    error_types.ErrorLocation.FieldPathElement(field_name="operations", index=0),
                    error_types.ErrorLocation.FieldPathElement(field_name="campaign_id"),
                ]
            ),
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.field_path, "operations[0].campaign_id")

    def test_message_never_leaks_beyond_googles_own_text(self) -> None:
        error = error_types.GoogleAdsError(
            error_code=error_types.ErrorCode(
                request_error=request_error.RequestErrorEnum.RequestError.INVALID_PAGE_SIZE
            ),
            message="page_size gecersiz",
        )
        result = classify_google_ads_exception(_exception_for(error))
        self.assertEqual(result.message, "page_size gecersiz")
        self.assertNotIn("token", result.message.lower())


class ClassifyTransportErrorTests(unittest.TestCase):
    def test_service_unavailable_is_transient(self) -> None:
        result = classify_transport_error(core_exceptions.ServiceUnavailable("down"))
        self.assertEqual(result.error_class, ErrorClass.TRANSIENT)
        self.assertTrue(result.retryable)

    def test_resource_exhausted_is_rate_limit(self) -> None:
        result = classify_transport_error(core_exceptions.ResourceExhausted("quota"))
        self.assertEqual(result.error_class, ErrorClass.RATE_LIMIT)
        self.assertTrue(result.retryable)

    def test_unauthenticated_is_auth(self) -> None:
        result = classify_transport_error(core_exceptions.Unauthenticated("bad creds"))
        self.assertEqual(result.error_class, ErrorClass.AUTH)
        self.assertFalse(result.retryable)

    def test_bare_grpc_unavailable_is_transient(self) -> None:
        result = classify_transport_error(_FakeRpcCall(grpc.StatusCode.UNAVAILABLE))
        self.assertEqual(result.error_class, ErrorClass.TRANSIENT)
        self.assertTrue(result.retryable)

    def test_unrecognised_exception_fails_closed_as_non_retryable(self) -> None:
        result = classify_transport_error(RuntimeError("kim bilir"))
        self.assertEqual(result.error_class, ErrorClass.VALIDATION)
        self.assertFalse(result.retryable)

    def test_unrecognised_exception_text_never_reaches_the_public_message(self) -> None:
        """A transport library could embed a request URL, header or credential

        fragment in its own exception message (docs/SECURITY.md -- secrets never
        reach a public response). ``classify_transport_error``'s fallback branch
        must return its own fixed, safe message regardless of what the original
        exception says -- never ``str(exc)``.
        """
        secret_bearing_exc = RuntimeError(
            "token=SECRET-MARKER-do-not-print-9f3a7c leaked in transport layer"
        )
        result = classify_transport_error(secret_bearing_exc)
        self.assertNotIn("SECRET-MARKER-do-not-print-9f3a7c", result.message)
        self.assertNotIn("SECRET-MARKER-do-not-print-9f3a7c", result.code)


if __name__ == "__main__":
    unittest.main()
