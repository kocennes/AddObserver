"""Bounded, signed MCP reporting continuation contract tests (todo.md 5.5)."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.reporting_pagination import (
    MAX_REPORT_BYTES,
    REPORT_CURSOR_TTL,
    InvalidReportCursorError,
    ReportCursorPosition,
    bound_report_rows,
    decode_report_cursor,
    encode_report_cursor,
)

_KEY = "unit-test-key-not-a-real-secret"
_NOW = datetime(2026, 7, 22, 12, tzinfo=UTC)
_CONTEXT = {
    "principal_id": "principal-a",
    "customer_id": "1234567890",
    "report_kind": "keyword",
    "start_date": "2026-07-01",
    "end_date": "2026-07-10",
}


class ReportCursorTests(unittest.TestCase):
    def _mint(self, **overrides: object) -> str:
        context: dict[str, object] = {
            **_CONTEXT,
            "position": ReportCursorPosition(provider_page_token="provider-secret", row_offset=7),
            "now": _NOW,
        }
        context.update(overrides)
        return encode_report_cursor(_KEY, **context)  # type: ignore[arg-type]

    def test_round_trips_without_exposing_provider_token(self) -> None:
        cursor = self._mint()
        decoded = decode_report_cursor(_KEY, cursor, **_CONTEXT, now=_NOW)
        self.assertEqual(
            decoded, ReportCursorPosition(provider_page_token="provider-secret", row_offset=7)
        )
        self.assertNotIn("provider-secret", cursor)
        self.assertNotIn("principal-a", cursor)

    def test_rejects_tampering_expiry_and_every_context_change(self) -> None:
        cursor = self._mint()
        cases = (
            {"principal_id": "principal-b"},
            {"customer_id": "9999999999"},
            {"report_kind": "campaign"},
            {"start_date": "2026-07-02"},
            {"end_date": "2026-07-11"},
            {"now": _NOW + REPORT_CURSOR_TTL + timedelta(seconds=1)},
        )
        for change in cases:
            with self.subTest(change=change), self.assertRaises(InvalidReportCursorError):
                decode_report_cursor(_KEY, cursor, **({**_CONTEXT, "now": _NOW, **change}))
        with self.assertRaises(InvalidReportCursorError):
            decode_report_cursor(_KEY, cursor[:-1] + "A", **_CONTEXT, now=_NOW)

    def test_rejects_oversized_or_malformed_input_before_decode(self) -> None:
        for cursor in ("", "!", "A" * 4097):
            with (
                self.subTest(cursor_length=len(cursor)),
                self.assertRaises(InvalidReportCursorError),
            ):
                decode_report_cursor(_KEY, cursor, **_CONTEXT, now=_NOW)


class BoundedRowsTests(unittest.TestCase):
    def test_row_limit_resumes_without_skipping_or_repeating(self) -> None:
        rows = tuple({"id": index} for index in range(503))
        first = bound_report_rows(rows, offset=0)
        second = bound_report_rows(rows, offset=first.next_offset or 0)
        self.assertEqual(len(first.rows), 500)
        self.assertEqual(first.next_offset, 500)
        self.assertEqual([row["id"] for row in second.rows], [500, 501, 502])
        self.assertIsNone(second.next_offset)

    def test_utf8_json_byte_budget_is_enforced(self) -> None:
        rows = ({"text": "ş" * 100}, {"text": "x" * 100})
        bounded = bound_report_rows(rows, offset=0, max_rows=500, max_bytes=220)
        self.assertEqual(len(bounded.rows), 1)
        self.assertLessEqual(bounded.byte_count, 220)
        self.assertEqual(bounded.next_offset, 1)
        self.assertLess(bounded.byte_count, MAX_REPORT_BYTES)

    def test_rejects_impossible_offset_and_single_oversized_row(self) -> None:
        with self.assertRaises(InvalidReportCursorError):
            bound_report_rows(({"id": 1},), offset=2)
        with self.assertRaises(ValueError):
            bound_report_rows(({"text": "x" * 100},), offset=0, max_bytes=10)


if __name__ == "__main__":
    unittest.main()
