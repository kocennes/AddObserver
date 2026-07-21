"""Contract tests for GoogleAdsReportingClient (docs/TESTING.md -- "Contract" tier).

Fakes return genuine ``SearchGoogleAdsResponse``/``GoogleAdsRow`` proto
objects (docs/TESTING.md mock policy), never an invented row shape.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.errors import AdsApiError, ErrorClass
from backend.src.api.queries import DateRange
from backend.src.api.reporting import (
    FakeGoogleAdsSearchService,
    GoogleAdsCredentials,
    GoogleAdsReportingClient,
    _RealSearchService,
)
from backend.src.api.retry import RetryPolicy
from google.ads.googleads.v24.common.types import (
    criteria,
)
from google.ads.googleads.v24.common.types import (
    metrics as metrics_types,
)
from google.ads.googleads.v24.common.types import (
    segments as segments_types,
)
from google.ads.googleads.v24.enums.types import (
    ad_group_criterion_status as ad_group_criterion_status_enum,
)
from google.ads.googleads.v24.enums.types import (
    ad_group_status as ad_group_status_enum,
)
from google.ads.googleads.v24.enums.types import (
    campaign_status as campaign_status_enum,
)
from google.ads.googleads.v24.enums.types import (
    keyword_match_type as keyword_match_type_enum,
)
from google.ads.googleads.v24.resources.types import ad_group, ad_group_criterion, campaign
from google.ads.googleads.v24.services.types.google_ads_service import (
    GoogleAdsRow,
    SearchGoogleAdsResponse,
)
from google.api_core import exceptions as core_exceptions


def _credentials() -> GoogleAdsCredentials:
    return GoogleAdsCredentials(
        developer_token="dev-token",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-token",
        login_customer_id="1112223333",
    )


def _campaign_row(*, campaign_id: int, name: str, clicks: int) -> GoogleAdsRow:
    return GoogleAdsRow(
        campaign=campaign.Campaign(
            id=campaign_id,
            name=name,
            status=campaign_status_enum.CampaignStatusEnum.CampaignStatus.ENABLED,
        ),
        segments=segments_types.Segments(date="2026-07-01"),
        metrics=metrics_types.Metrics(
            impressions=1000, clicks=clicks, cost_micros=250_000, conversions=3.0
        ),
    )


class GoogleAdsReportingClientCampaignTests(unittest.TestCase):
    def setUp(self) -> None:
        self.date_range = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 10))

    def test_maps_single_page_into_narrow_rows(self) -> None:
        page = SearchGoogleAdsResponse(
            results=[_campaign_row(campaign_id=111, name="Yaz Kampanyasi", clicks=42)],
            next_page_token="",
        )
        service = FakeGoogleAdsSearchService(pages_by_token={None: page})
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        result = client.get_campaign_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )

        self.assertEqual(len(result.rows), 1)
        row = result.rows[0]
        self.assertEqual(row["campaign_id"], "111")
        self.assertEqual(row["campaign_name"], "Yaz Kampanyasi")
        self.assertEqual(row["campaign_status"], "ENABLED")
        self.assertEqual(row["clicks"], 42)
        self.assertIsNone(result.next_page_token)
        # Data minimisation: nothing beyond the allowlisted fields leaks through.
        self.assertEqual(
            set(row.keys()),
            {
                "date",
                "campaign_id",
                "campaign_name",
                "campaign_status",
                "impressions",
                "clicks",
                "cost_micros",
                "conversions",
            },
        )

    def test_returns_next_page_token_when_more_rows_exist(self) -> None:
        page = SearchGoogleAdsResponse(
            results=[_campaign_row(campaign_id=111, name="A", clicks=1)],
            next_page_token="page-2",
        )
        service = FakeGoogleAdsSearchService(pages_by_token={None: page})
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        result = client.get_campaign_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )
        self.assertEqual(result.next_page_token, "page-2")

    def test_empty_page_has_stable_shape(self) -> None:
        service = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[], next_page_token="")}
        )
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        result = client.get_campaign_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )

        self.assertEqual(result.rows, ())
        self.assertIsNone(result.next_page_token)

    def test_pages_are_fetched_only_when_the_caller_supplies_continuation(self) -> None:
        service = FakeGoogleAdsSearchService(
            pages_by_token={
                None: SearchGoogleAdsResponse(
                    results=[_campaign_row(campaign_id=111, name="A", clicks=1)],
                    next_page_token="page-2",
                ),
                "page-2": SearchGoogleAdsResponse(
                    results=[_campaign_row(campaign_id=222, name="B", clicks=2)],
                    next_page_token="",
                ),
            }
        )
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        first = client.get_campaign_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )
        self.assertEqual(len(service.calls), 1)
        second = client.get_campaign_performance(
            customer_id="1234567890",
            credentials=_credentials(),
            date_range=self.date_range,
            page_token=first.next_page_token,
        )

        self.assertEqual([row["campaign_id"] for row in first.rows], ["111"])
        self.assertEqual([row["campaign_id"] for row in second.rows], ["222"])
        self.assertEqual([call["page_token"] for call in service.calls], [None, "page-2"])

    def test_preserves_micros_enum_unknown_and_normalizes_missing_strings(self) -> None:
        row = GoogleAdsRow(
            campaign=campaign.Campaign(
                id=111,
                status=campaign_status_enum.CampaignStatusEnum.CampaignStatus.UNKNOWN,
            ),
            metrics=metrics_types.Metrics(cost_micros=9_007_199_254_740_991),
        )
        service = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[row])}
        )
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        result = client.get_campaign_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )

        mapped = result.rows[0]
        self.assertEqual(mapped["cost_micros"], 9_007_199_254_740_991)
        self.assertEqual(mapped["campaign_status"], "UNKNOWN")
        self.assertIsNone(mapped["campaign_name"])
        self.assertIsNone(mapped["date"])

    def test_requests_the_exact_page_token_it_was_given(self) -> None:
        page = SearchGoogleAdsResponse(results=[], next_page_token="")
        service = FakeGoogleAdsSearchService(pages_by_token={"page-2": page})
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        client.get_campaign_performance(
            customer_id="1234567890",
            credentials=_credentials(),
            date_range=self.date_range,
            page_token="page-2",
        )
        self.assertEqual(service.calls[0]["page_token"], "page-2")
        self.assertEqual(service.calls[0]["customer_id"], "1234567890")

    def test_invalid_customer_id_is_rejected_before_any_network_call(self) -> None:
        service = FakeGoogleAdsSearchService(pages_by_token={None: SearchGoogleAdsResponse()})
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        with self.assertRaises(AdsApiError) as ctx:
            client.get_campaign_performance(
                customer_id="not-a-customer-id",
                credentials=_credentials(),
                date_range=self.date_range,
            )
        self.assertEqual(ctx.exception.code, "invalid_customer_id")
        self.assertEqual(service.calls, [])

    def test_transient_failure_is_retried_then_succeeds(self) -> None:
        page = SearchGoogleAdsResponse(
            results=[_campaign_row(campaign_id=111, name="A", clicks=1)], next_page_token=""
        )
        service = FakeGoogleAdsSearchService(
            pages_by_token={None: page},
            raises=core_exceptions.ServiceUnavailable("gecici"),
            fail_first_n_calls=1,
        )
        client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: service,
            retry_policy=RetryPolicy(
                max_attempts=3,
                max_elapsed_seconds=5,
                base_delay_seconds=0.01,
                max_delay_seconds=0.01,
            ),
        )

        result = client.get_campaign_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(service.calls), 2)

    def test_permanent_failure_raises_ads_api_error_without_leaking_credentials(self) -> None:
        service = FakeGoogleAdsSearchService(raises=RuntimeError("boom"))
        client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: service,
            retry_policy=RetryPolicy(max_attempts=1),
        )

        with self.assertRaises(AdsApiError) as ctx:
            client.get_campaign_performance(
                customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
            )
        self.assertNotIn("refresh-token", str(ctx.exception))
        self.assertNotIn("client-secret", str(ctx.exception))

    def test_quota_failure_is_classified_after_retry_budget_is_exhausted(self) -> None:
        service = FakeGoogleAdsSearchService(raises=core_exceptions.ResourceExhausted("quota"))
        client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: service,
            retry_policy=RetryPolicy(max_attempts=1),
        )

        with self.assertRaises(AdsApiError) as ctx:
            client.get_campaign_performance(
                customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
            )
        self.assertEqual(ctx.exception.error_class, ErrorClass.RATE_LIMIT)
        self.assertEqual(ctx.exception.code, "transport.resource_exhausted")

    def test_timeout_is_classified_after_retry_budget_is_exhausted(self) -> None:
        service = FakeGoogleAdsSearchService(raises=core_exceptions.DeadlineExceeded("timeout"))
        client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: service,
            retry_policy=RetryPolicy(max_attempts=1),
        )

        with self.assertRaises(AdsApiError) as ctx:
            client.get_campaign_performance(
                customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
            )
        self.assertEqual(ctx.exception.error_class, ErrorClass.TRANSIENT)
        self.assertEqual(ctx.exception.code, "transport.unavailable")

    def test_search_auth_failure_is_not_retried(self) -> None:
        service = FakeGoogleAdsSearchService(raises=core_exceptions.Unauthenticated("revoked"))
        client = GoogleAdsReportingClient(
            search_service_factory=lambda creds: service,
            retry_policy=RetryPolicy(max_attempts=3),
        )

        with self.assertRaises(AdsApiError) as ctx:
            client.get_campaign_performance(
                customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
            )
        self.assertEqual(ctx.exception.error_class, ErrorClass.AUTH)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(len(service.calls), 1)

    def test_credential_refresh_failure_during_service_construction_is_classified(self) -> None:
        """The official client refreshes the OAuth token while *building* the
        service (a real network call), not on the first search -- a revoked
        refresh token must still surface as a classified AdsApiError, not a
        raw ``google.auth`` exception escaping the adapter."""
        from google.auth.exceptions import RefreshError

        def _raising_factory(credentials: GoogleAdsCredentials):
            raise RefreshError("invalid_grant: token has been revoked")

        client = GoogleAdsReportingClient(search_service_factory=_raising_factory)

        with self.assertRaises(AdsApiError) as ctx:
            client.get_campaign_performance(
                customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
            )
        self.assertEqual(ctx.exception.error_class, ErrorClass.AUTH)
        self.assertFalse(ctx.exception.retryable)


class RealSearchServiceV24ContractTests(unittest.TestCase):
    def test_does_not_send_removed_page_size_parameter(self) -> None:
        response = SearchGoogleAdsResponse()

        class _Pager:
            pages = iter((response,))

        class _GoogleAdsService:
            def __init__(self) -> None:
                self.kwargs = None

            def search(self, **kwargs):
                self.kwargs = kwargs
                return _Pager()

        ga_service = _GoogleAdsService()
        service = _RealSearchService(ga_service)

        self.assertIs(
            service.search(
                customer_id="1234567890",
                query="SELECT campaign.id FROM campaign",
                page_token=None,
                page_size=100,
            ),
            response,
        )
        self.assertEqual(
            ga_service.kwargs,
            {
                "customer_id": "1234567890",
                "query": "SELECT campaign.id FROM campaign",
                "page_token": "",
            },
        )


class GoogleAdsReportingClientAdGroupAndKeywordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.date_range = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 10))

    def test_ad_group_performance_maps_narrow_rows(self) -> None:
        row = GoogleAdsRow(
            campaign=campaign.Campaign(id=111),
            ad_group=ad_group.AdGroup(
                id=222,
                name="Ana Grup",
                status=ad_group_status_enum.AdGroupStatusEnum.AdGroupStatus.ENABLED,
            ),
            segments=segments_types.Segments(date="2026-07-01"),
            metrics=metrics_types.Metrics(
                impressions=10, clicks=2, cost_micros=1000, conversions=0.0
            ),
        )
        service = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[row], next_page_token="")}
        )
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        result = client.get_ad_group_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )
        self.assertEqual(result.rows[0]["ad_group_id"], "222")
        self.assertEqual(result.rows[0]["ad_group_name"], "Ana Grup")
        self.assertEqual(result.rows[0]["ad_group_status"], "ENABLED")

    def test_keyword_performance_maps_narrow_rows(self) -> None:
        row = GoogleAdsRow(
            campaign=campaign.Campaign(id=111),
            ad_group=ad_group.AdGroup(id=222),
            ad_group_criterion=ad_group_criterion.AdGroupCriterion(
                criterion_id=333,
                status=ad_group_criterion_status_enum.AdGroupCriterionStatusEnum.AdGroupCriterionStatus.ENABLED,
                keyword=criteria.KeywordInfo(
                    text="google ads danismanligi",
                    match_type=keyword_match_type_enum.KeywordMatchTypeEnum.KeywordMatchType.EXACT,
                ),
            ),
            segments=segments_types.Segments(date="2026-07-01"),
            metrics=metrics_types.Metrics(
                impressions=5, clicks=1, cost_micros=500, conversions=1.0
            ),
        )
        service = FakeGoogleAdsSearchService(
            pages_by_token={None: SearchGoogleAdsResponse(results=[row], next_page_token="")}
        )
        client = GoogleAdsReportingClient(search_service_factory=lambda creds: service)

        result = client.get_keyword_performance(
            customer_id="1234567890", credentials=_credentials(), date_range=self.date_range
        )
        self.assertEqual(result.rows[0]["keyword_text"], "google ads danismanligi")
        self.assertEqual(result.rows[0]["keyword_match_type"], "EXACT")
        self.assertEqual(result.rows[0]["criterion_id"], "333")


if __name__ == "__main__":
    unittest.main()
