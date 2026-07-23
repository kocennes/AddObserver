"""Validated GAQL query builders (docs/API_CONTRACTS.md -- "Google Ads istemci siniri").

"GAQL sorgulari kodda tanimli query object/allowlist ile kurulur. Tarih ve ID
parametreleri dogrulanir." Every SELECT clause here is a fixed, narrow
literal (docs/PRODUCT.md Faz 1 -- "dar ... okuma tool'lari"); the only
caller-controlled values that reach the query text are ``date`` objects
formatted with ``date.isoformat()``, so there is no free-text interpolation
surface for GAQL injection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .errors import AdsApiError, ErrorClass

#: Reporting windows are capped to keep a single page well under the Google
#: Ads 64 MB response ceiling and the "response buyuklugu makul olmali"
#: review criterion (docs/RATE_LIMITS.md, docs/API_CONTRACTS.md).
MAX_DATE_RANGE_DAYS = 90

_CUSTOMER_ID_RE = re.compile(r"^\d{10}$")


def validate_customer_id(customer_id: str) -> str:
    """Return ``customer_id`` unchanged if it is a bare 10-digit Google Ads ID.

    Hyphenated display form (``123-456-7890``) is rejected on purpose: the
    caller is expected to pass the exact value stored in ``ads_account``
    (``backend.src.db.models.AdsAccount.customer_id``), not user-typed input.
    """
    if not _CUSTOMER_ID_RE.match(customer_id):
        raise AdsApiError(
            error_class=ErrorClass.VALIDATION,
            code="invalid_customer_id",
            message="customer_id 10 haneli bir Google Ads kimligi olmalidir.",
            request_id=None,
        )
    return customer_id


@dataclass(frozen=True, slots=True)
class DateRange:
    """An inclusive ``[start, end]`` reporting window, validated at construction."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise AdsApiError(
                error_class=ErrorClass.VALIDATION,
                code="invalid_date_range",
                message="Baslangic tarihi bitis tarihinden sonra olamaz.",
                request_id=None,
            )
        if (self.end - self.start).days + 1 > MAX_DATE_RANGE_DAYS:
            raise AdsApiError(
                error_class=ErrorClass.VALIDATION,
                code="date_range_too_wide",
                message=f"Tarih araligi en fazla {MAX_DATE_RANGE_DAYS} gun olabilir.",
                request_id=None,
            )

    def as_gaql_between(self) -> str:
        return f"segments.date BETWEEN '{self.start.isoformat()}' AND '{self.end.isoformat()}'"


_CAMPAIGN_FIELDS = (
    "campaign.id",
    "campaign.name",
    "campaign.status",
    "segments.date",
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
    "metrics.conversions",
)

_AD_GROUP_FIELDS = (
    "ad_group.id",
    "ad_group.name",
    "ad_group.status",
    "campaign.id",
    "segments.date",
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
    "metrics.conversions",
)

_KEYWORD_FIELDS = (
    "ad_group_criterion.criterion_id",
    "ad_group_criterion.keyword.text",
    "ad_group_criterion.keyword.match_type",
    "ad_group_criterion.status",
    "ad_group.id",
    "campaign.id",
    "segments.date",
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
    "metrics.conversions",
)


def _build_query(*, fields: tuple[str, ...], resource: str, date_range: DateRange) -> str:
    select_clause = ", ".join(fields)
    # GAQL, not SQL; `fields`/`resource` are always code-only allowlist constants (see
    # _CAMPAIGN_FIELDS/_AD_GROUP_FIELDS/_KEYWORD_FIELDS above), never external input.
    return (
        f"SELECT {select_clause} FROM {resource} "  # nosec B608
        f"WHERE {date_range.as_gaql_between()} "
        f"ORDER BY segments.date ASC"
    )


def build_campaign_performance_query(date_range: DateRange) -> str:
    return _build_query(fields=_CAMPAIGN_FIELDS, resource="campaign", date_range=date_range)


def build_ad_group_performance_query(date_range: DateRange) -> str:
    return _build_query(fields=_AD_GROUP_FIELDS, resource="ad_group", date_range=date_range)


def build_keyword_performance_query(date_range: DateRange) -> str:
    return _build_query(fields=_KEYWORD_FIELDS, resource="keyword_view", date_range=date_range)
