"""API_CONTRACTS.md query-builder tests: allowlisted fields, validated dates/IDs."""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.errors import AdsApiError, ErrorClass
from backend.src.api.queries import (
    MAX_DATE_RANGE_DAYS,
    MAX_PAGE_SIZE,
    DateRange,
    build_ad_group_performance_query,
    build_campaign_performance_query,
    build_keyword_performance_query,
    validate_customer_id,
    validate_page_size,
)


class ValidateCustomerIdTests(unittest.TestCase):
    def test_accepts_bare_ten_digit_id(self) -> None:
        self.assertEqual(validate_customer_id("1234567890"), "1234567890")

    def test_rejects_hyphenated_display_form(self) -> None:
        with self.assertRaises(AdsApiError) as ctx:
            validate_customer_id("123-456-7890")
        self.assertEqual(ctx.exception.error_class, ErrorClass.VALIDATION)
        self.assertEqual(ctx.exception.code, "invalid_customer_id")

    def test_rejects_gaql_injection_attempt(self) -> None:
        with self.assertRaises(AdsApiError):
            validate_customer_id("1234567890' OR '1'='1")

    def test_rejects_wrong_length(self) -> None:
        with self.assertRaises(AdsApiError):
            validate_customer_id("123")


class ValidatePageSizeTests(unittest.TestCase):
    def test_accepts_in_range_value(self) -> None:
        self.assertEqual(validate_page_size(50), 50)

    def test_rejects_zero_and_negative(self) -> None:
        with self.assertRaises(AdsApiError):
            validate_page_size(0)

    def test_rejects_above_max(self) -> None:
        with self.assertRaises(AdsApiError):
            validate_page_size(MAX_PAGE_SIZE + 1)


class DateRangeTests(unittest.TestCase):
    def test_rejects_start_after_end(self) -> None:
        with self.assertRaises(AdsApiError) as ctx:
            DateRange(start=date(2026, 7, 10), end=date(2026, 7, 1))
        self.assertEqual(ctx.exception.code, "invalid_date_range")

    def test_rejects_window_wider_than_max(self) -> None:
        start = date(2026, 1, 1)
        with self.assertRaises(AdsApiError) as ctx:
            DateRange(start=start, end=start + timedelta(days=MAX_DATE_RANGE_DAYS))
        self.assertEqual(ctx.exception.code, "date_range_too_wide")

    def test_accepts_window_at_max(self) -> None:
        start = date(2026, 1, 1)
        end = start + timedelta(days=MAX_DATE_RANGE_DAYS - 1)
        range_ = DateRange(start=start, end=end)
        self.assertEqual(range_.end, end)

    def test_gaql_between_uses_iso_dates_only(self) -> None:
        range_ = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 10))
        self.assertEqual(
            range_.as_gaql_between(),
            "segments.date BETWEEN '2026-07-01' AND '2026-07-10'",
        )


class QueryBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.date_range = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 10))

    def test_campaign_query_selects_only_allowlisted_fields(self) -> None:
        query = build_campaign_performance_query(self.date_range)
        self.assertTrue(query.startswith("SELECT "))
        self.assertIn("FROM campaign ", query)
        self.assertIn("metrics.cost_micros", query)
        self.assertIn("segments.date BETWEEN '2026-07-01' AND '2026-07-10'", query)
        self.assertNotIn(";", query)

    def test_ad_group_query_targets_ad_group_resource(self) -> None:
        query = build_ad_group_performance_query(self.date_range)
        self.assertIn("FROM ad_group ", query)
        self.assertIn("ad_group.status", query)

    def test_keyword_query_targets_keyword_view_resource(self) -> None:
        query = build_keyword_performance_query(self.date_range)
        self.assertIn("FROM keyword_view ", query)
        self.assertIn("ad_group_criterion.keyword.text", query)


if __name__ == "__main__":
    unittest.main()
