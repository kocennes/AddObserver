"""Contract tests for bounded, context-bound Google report cursors."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.errors import AdsApiError
from backend.src.api.report_cursor import (
    REPORT_MAX_BYTES,
    REPORT_MAX_ROWS,
    ReportCursorPosition,
    bound_report_page,
    decode_report_cursor,
    encode_report_cursor,
)
from backend.src.api.reporting import ReportPage

NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
START = date(2026, 7, 1)
END = date(2026, 7, 10)
KEY = "test-vault-key"


def _encode(position: ReportCursorPosition, **overrides) -> str:
    values = {
        "principal_id": "principal-1",
        "customer_id": "1234567890",
        "report_type": "campaign",
        "start_date": START,
        "end_date": END,
        "position": position,
        "now": NOW,
    }
    values.update(overrides)
    return encode_report_cursor(KEY, **values)


def _decode(cursor: str, **overrides) -> ReportCursorPosition:
    values = {
        "principal_id": "principal-1",
        "customer_id": "1234567890",
        "report_type": "campaign",
        "start_date": START,
        "end_date": END,
        "now": NOW,
    }
    values.update(overrides)
    return decode_report_cursor(KEY, cursor, **values)


class ReportCursorSecurityTests(unittest.TestCase):
    def test_round_trip_preserves_provider_token_and_row_offset(self) -> None:
        position = ReportCursorPosition("provider-page-2", 37)
        cursor = _encode(position)
        self.assertEqual(_decode(cursor), position)
        self.assertNotIn("provider-page-2", cursor)
        decoded_envelope = base64.urlsafe_b64decode(cursor).decode(errors="ignore")
        self.assertNotIn("provider-page-2", decoded_envelope)

    def test_cursor_is_bound_to_principal_customer_report_and_dates(self) -> None:
        cursor = _encode(ReportCursorPosition(None, 1))
        mismatches = (
            {"principal_id": "principal-2"},
            {"customer_id": "9999999999"},
            {"report_type": "keyword"},
            {"start_date": date(2026, 7, 2)},
            {"end_date": date(2026, 7, 11)},
        )
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch), self.assertRaises(AdsApiError) as caught:
                _decode(cursor, **mismatch)
            self.assertEqual(caught.exception.code, "invalid_report_cursor")

    def test_tampered_expired_future_and_oversized_cursors_share_safe_error(self) -> None:
        cursor = _encode(ReportCursorPosition(None, 1))
        invalid = (
            cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
            _encode(ReportCursorPosition(None, 1), now=NOW - timedelta(minutes=16)),
            _encode(ReportCursorPosition(None, 1), now=NOW + timedelta(seconds=1)),
            "x" * 2049,
        )
        for value in invalid:
            with self.subTest(value_length=len(value)), self.assertRaises(AdsApiError) as caught:
                _decode(value)
            self.assertEqual(caught.exception.code, "invalid_report_cursor")


class BoundReportPageTests(unittest.TestCase):
    @staticmethod
    def _mint(provider_page_token: str | None, row_offset: int) -> str:
        return f"cursor:{provider_page_token or 'first'}:{row_offset}"

    def test_row_limit_returns_cursor_without_exposing_provider_token(self) -> None:
        page = ReportPage(
            rows=tuple({"id": str(index)} for index in range(REPORT_MAX_ROWS + 1)),
            next_page_token="raw-provider-secret-token",
        )

        result = bound_report_page(
            page, provider_page_token=None, row_offset=0, mint_cursor=self._mint
        )

        self.assertEqual(len(result["rows"]), REPORT_MAX_ROWS)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["returned_row_count"], REPORT_MAX_ROWS)
        self.assertEqual(result["next_page_token"], f"cursor:first:{REPORT_MAX_ROWS}")
        self.assertNotIn("raw-provider-secret-token", json.dumps(result))
        self.assertEqual(result["quota"], {"google_requests": 1})

    def test_byte_limit_is_enforced_without_dropping_unreturned_rows(self) -> None:
        page = ReportPage(
            rows=tuple({"text": "ğ" * 10_000, "id": str(index)} for index in range(40)),
            next_page_token=None,
        )

        result = bound_report_page(
            page, provider_page_token="provider-page", row_offset=0, mint_cursor=self._mint
        )

        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        self.assertLessEqual(len(encoded), REPORT_MAX_BYTES)
        self.assertEqual(result["response_bytes"], len(encoded))
        self.assertGreater(result["returned_row_count"], 0)
        self.assertLess(result["returned_row_count"], 40)
        self.assertEqual(
            result["next_page_token"],
            f"cursor:provider-page:{result['returned_row_count']}",
        )

    def test_next_provider_page_is_wrapped_when_current_page_is_consumed(self) -> None:
        result = bound_report_page(
            ReportPage(rows=({"id": "1"},), next_page_token="provider-page-2"),
            provider_page_token=None,
            row_offset=0,
            mint_cursor=self._mint,
        )
        self.assertEqual(result["next_page_token"], "cursor:provider-page-2:0")

    def test_single_oversized_row_fails_instead_of_silent_data_loss(self) -> None:
        page = ReportPage(rows=({"text": "x" * REPORT_MAX_BYTES},), next_page_token=None)
        with self.assertRaises(AdsApiError) as caught:
            bound_report_page(page, provider_page_token=None, row_offset=0, mint_cursor=self._mint)
        self.assertEqual(caught.exception.code, "report_row_too_large")

    def test_offset_beyond_provider_page_is_rejected(self) -> None:
        with self.assertRaises(AdsApiError) as caught:
            bound_report_page(
                ReportPage(rows=({"id": "1"},), next_page_token=None),
                provider_page_token=None,
                row_offset=2,
                mint_cursor=self._mint,
            )
        self.assertEqual(caught.exception.code, "invalid_report_cursor")


if __name__ == "__main__":
    unittest.main()
