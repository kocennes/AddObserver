"""Faz 1 Google Ads reporting adapter (docs/PRODUCT.md, docs/API_CONTRACTS.md).

Read-only by construction: this module has no mutate method, matching
docs/PRODUCT.md Faz 1 ("Ucret, abonelik ... yoktur" / write is Faz 1.1 and
blocked on ``docs/GOOGLE_API_ACCESS.md`` still being ``Taslak``). Every
public method takes an already-verified ``customer_id`` and a caller-supplied
``refresh_token`` -- resolving *which* token belongs to *which* principal is
the caller's job (``backend.src.db``/``backend.src.auth``), never this
adapter's, so an adapter bug can never cross principal boundaries
(docs/SECURITY.md).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.ads.googleads.v24.services.types.google_ads_service import (
    GoogleAdsRow,
    SearchGoogleAdsResponse,
)

from .errors import AdsApiError, classify_google_ads_exception, classify_transport_error
from .queries import (
    DEFAULT_PAGE_SIZE,
    DateRange,
    build_ad_group_performance_query,
    build_campaign_performance_query,
    build_keyword_performance_query,
    validate_customer_id,
    validate_page_size,
)
from .retry import RetryPolicy, execute_with_retry

#: Pinned per docs/API_CONTRACTS.md -- "Google Ads API surumu ... lockfile'da
#: sabitlenir". Bumping this is a deliberate, reviewed decision, not a
#: side effect of a library upgrade.
API_VERSION = "v24"


@dataclass(frozen=True, slots=True)
class GoogleAdsCredentials:
    """Everything one reporting call needs, resolved by the caller beforehand.

    ``refresh_token`` must never be logged, cached beyond this call's scope,
    or returned to any MCP/Claude-facing response (docs/SECURITY.md --
    "Access token kaliciliastirilmaz"). ``developer_token``/``client_secret``/
    ``refresh_token`` carry ``repr=False`` so an accidental ``repr()``/``str()``
    of this object (log line, exception message, debugger) never prints the
    raw secret (backend/tests/test_secret_redaction.py).
    """

    developer_token: str = field(repr=False)
    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    login_customer_id: str | None = None


@dataclass(frozen=True, slots=True)
class ReportPage:
    """One narrow, already-mapped page of reporting rows -- never a raw proto."""

    rows: tuple[Mapping[str, Any], ...]
    next_page_token: str | None


class GoogleAdsSearchService(Protocol):
    """Boundary around exactly the one RPC this adapter needs, one page at a time."""

    def search(
        self, *, customer_id: str, query: str, page_token: str | None, page_size: int
    ) -> SearchGoogleAdsResponse: ...


GoogleAdsSearchServiceFactory = Callable[[GoogleAdsCredentials], GoogleAdsSearchService]


class _RealSearchService:
    """Wraps ``GoogleAdsServiceClient.search`` to always return a single page.

    The official client's ``SearchPager`` fetches subsequent pages lazily as
    it is iterated; taking only ``next(pager.pages)`` gets exactly the page
    for the ``page_token`` we asked for and nothing more, keeping every
    response small and caller-paced (docs/RATE_LIMITS.md -- "Buyuk GAQL
    secimi alan/tarih/pagination ile kucultulur").
    """

    def __init__(self, ga_service: Any) -> None:
        self._ga_service = ga_service

    def search(
        self, *, customer_id: str, query: str, page_token: str | None, page_size: int
    ) -> SearchGoogleAdsResponse:
        pager = self._ga_service.search(
            customer_id=customer_id,
            query=query,
            page_token=page_token or "",
            page_size=page_size,
        )
        return next(pager.pages)


def real_search_service_factory(credentials: GoogleAdsCredentials) -> GoogleAdsSearchService:
    """Build a live ``GoogleAdsSearchService`` from resolved OAuth credentials.

    A fresh ``GoogleAdsClient`` is built per call rather than cached, so one
    principal's credentials can never leak into another principal's request
    through a shared client instance (docs/SECURITY.md -- account isolation).
    """
    config: dict[str, Any] = {
        "developer_token": credentials.developer_token,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "use_proto_plus": True,
    }
    if credentials.login_customer_id is not None:
        config["login_customer_id"] = credentials.login_customer_id
    client = GoogleAdsClient.load_from_dict(config, version=API_VERSION)
    return _RealSearchService(client.get_service("GoogleAdsService"))


def _row_to_mapping(
    field_names: Iterable[str],
    getters: Mapping[str, Callable[[GoogleAdsRow], Any]],
    row: GoogleAdsRow,
) -> Mapping[str, Any]:
    return {name: getters[name](row) for name in field_names}


_CAMPAIGN_ROW_GETTERS: dict[str, Callable[[GoogleAdsRow], Any]] = {
    "date": lambda row: row.segments.date,
    "campaign_id": lambda row: str(row.campaign.id),
    "campaign_name": lambda row: row.campaign.name,
    "campaign_status": lambda row: row.campaign.status.name,
    "impressions": lambda row: row.metrics.impressions,
    "clicks": lambda row: row.metrics.clicks,
    "cost_micros": lambda row: row.metrics.cost_micros,
    "conversions": lambda row: row.metrics.conversions,
}

_AD_GROUP_ROW_GETTERS: dict[str, Callable[[GoogleAdsRow], Any]] = {
    "date": lambda row: row.segments.date,
    "campaign_id": lambda row: str(row.campaign.id),
    "ad_group_id": lambda row: str(row.ad_group.id),
    "ad_group_name": lambda row: row.ad_group.name,
    "ad_group_status": lambda row: row.ad_group.status.name,
    "impressions": lambda row: row.metrics.impressions,
    "clicks": lambda row: row.metrics.clicks,
    "cost_micros": lambda row: row.metrics.cost_micros,
    "conversions": lambda row: row.metrics.conversions,
}

_KEYWORD_ROW_GETTERS: dict[str, Callable[[GoogleAdsRow], Any]] = {
    "date": lambda row: row.segments.date,
    "campaign_id": lambda row: str(row.campaign.id),
    "ad_group_id": lambda row: str(row.ad_group.id),
    "criterion_id": lambda row: str(row.ad_group_criterion.criterion_id),
    "keyword_text": lambda row: row.ad_group_criterion.keyword.text,
    "keyword_match_type": lambda row: row.ad_group_criterion.keyword.match_type.name,
    "keyword_status": lambda row: row.ad_group_criterion.status.name,
    "impressions": lambda row: row.metrics.impressions,
    "clicks": lambda row: row.metrics.clicks,
    "cost_micros": lambda row: row.metrics.cost_micros,
    "conversions": lambda row: row.metrics.conversions,
}


class GoogleAdsReportingClient:
    """Faz 1 read-only Google Ads adapter: campaign/ad group/keyword performance."""

    def __init__(
        self,
        *,
        search_service_factory: GoogleAdsSearchServiceFactory = real_search_service_factory,
        retry_policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        self._search_service_factory = search_service_factory
        self._retry_policy = retry_policy

    def get_campaign_performance(
        self,
        *,
        customer_id: str,
        credentials: GoogleAdsCredentials,
        date_range: DateRange,
        page_token: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ReportPage:
        return self._run_report(
            customer_id=customer_id,
            credentials=credentials,
            query=build_campaign_performance_query(date_range),
            row_getters=_CAMPAIGN_ROW_GETTERS,
            page_token=page_token,
            page_size=page_size,
        )

    def get_ad_group_performance(
        self,
        *,
        customer_id: str,
        credentials: GoogleAdsCredentials,
        date_range: DateRange,
        page_token: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ReportPage:
        return self._run_report(
            customer_id=customer_id,
            credentials=credentials,
            query=build_ad_group_performance_query(date_range),
            row_getters=_AD_GROUP_ROW_GETTERS,
            page_token=page_token,
            page_size=page_size,
        )

    def get_keyword_performance(
        self,
        *,
        customer_id: str,
        credentials: GoogleAdsCredentials,
        date_range: DateRange,
        page_token: str | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ReportPage:
        return self._run_report(
            customer_id=customer_id,
            credentials=credentials,
            query=build_keyword_performance_query(date_range),
            row_getters=_KEYWORD_ROW_GETTERS,
            page_token=page_token,
            page_size=page_size,
        )

    def _run_report(
        self,
        *,
        customer_id: str,
        credentials: GoogleAdsCredentials,
        query: str,
        row_getters: Mapping[str, Callable[[GoogleAdsRow], Any]],
        page_token: str | None,
        page_size: int,
    ) -> ReportPage:
        validated_customer_id = validate_customer_id(customer_id)
        validated_page_size = validate_page_size(page_size)

        # The official client eagerly refreshes the OAuth access token while
        # constructing the service (a real network round-trip to Google), so
        # an expired/revoked refresh token surfaces right here -- it must be
        # classified like any other adapter failure, never left as a raw
        # ``google.auth`` exception. Built once, outside the retry loop: this
        # failure is essentially always AUTH-class (non-retryable), so
        # retrying it would only spend an extra token-refresh round-trip
        # per attempt for no benefit.
        try:
            service = self._search_service_factory(credentials)
        except GoogleAdsException as exc:
            raise classify_google_ads_exception(exc) from exc
        except AdsApiError:
            raise
        except Exception as exc:  # noqa: BLE001 -- reclassified below
            raise classify_transport_error(exc) from exc

        def _call() -> SearchGoogleAdsResponse:
            try:
                return service.search(
                    customer_id=validated_customer_id,
                    query=query,
                    page_token=page_token,
                    page_size=validated_page_size,
                )
            except GoogleAdsException as exc:
                raise classify_google_ads_exception(exc) from exc
            except AdsApiError:
                raise
            except Exception as exc:  # noqa: BLE001 -- reclassified below
                raise classify_transport_error(exc) from exc

        def _classify(exc: Exception) -> AdsApiError:
            return exc if isinstance(exc, AdsApiError) else classify_transport_error(exc)

        response = execute_with_retry(_call, classify=_classify, policy=self._retry_policy)
        field_names = row_getters.keys()
        rows = tuple(_row_to_mapping(field_names, row_getters, row) for row in response.results)
        next_token = response.next_page_token or None
        return ReportPage(rows=rows, next_page_token=next_token)


class FakeGoogleAdsSearchService:
    """Deterministic test double matching ``GoogleAdsSearchService`` exactly.

    Configured with real ``SearchGoogleAdsResponse`` proto objects (or an
    exception to raise), never an invented simplified shape
    (docs/TESTING.md -- "Mock, resmi client'in cagri imzasina yakin tutulur").
    """

    def __init__(
        self,
        *,
        pages_by_token: Mapping[str | None, SearchGoogleAdsResponse] | None = None,
        raises: Exception | None = None,
        fail_first_n_calls: int = 0,
    ) -> None:
        self._pages_by_token = dict(pages_by_token or {})
        self._raises = raises
        self._fail_first_n_calls = fail_first_n_calls
        self.calls: list[dict[str, Any]] = []

    def search(
        self, *, customer_id: str, query: str, page_token: str | None, page_size: int
    ) -> SearchGoogleAdsResponse:
        self.calls.append(
            {
                "customer_id": customer_id,
                "query": query,
                "page_token": page_token,
                "page_size": page_size,
            }
        )
        if len(self.calls) <= self._fail_first_n_calls and self._raises is not None:
            raise self._raises
        if self._raises is not None and not self._pages_by_token:
            raise self._raises
        return self._pages_by_token[page_token]
