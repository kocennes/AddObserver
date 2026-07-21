"""Google Ads reporting adapter (Faz 1 -- read-only, per docs/PRODUCT.md).

Every public entry point takes an already-validated ``customer_id`` explicitly
(AGENTS.md -- "her cagri customer_id parametresi alir"); this package never
resolves a principal's account mapping or credential itself -- that stays in
``backend.src.db``/``backend.src.auth`` so a Google Ads adapter bug can never
become a cross-principal access bug (docs/SECURITY.md -- "Kullanici ve Google
Ads hesap izolasyonu"). Mutations (docs/PRODUCT.md Faz 1.1) are intentionally
absent until Google's RMF/Compliance classification closes
``docs/GOOGLE_API_ACCESS.md`` (still ``Taslak``).
"""

from .accounts import (
    DiscoveredAccount,
    GoogleAdsAccountDiscoveryClient,
    sync_discovered_accounts,
)
from .errors import AdsApiError, ErrorClass, classify_google_ads_exception, classify_transport_error
from .problems import PROBLEM_JSON, problem_body, problem_response
from .queries import DateRange
from .reporting import (
    FakeGoogleAdsSearchService,
    GoogleAdsCredentials,
    GoogleAdsReportingClient,
    GoogleAdsSearchService,
    ReportPage,
    real_search_service_factory,
)
from .retry import RetryPolicy, execute_with_retry

__all__ = [
    "AdsApiError",
    "DiscoveredAccount",
    "ErrorClass",
    "GoogleAdsAccountDiscoveryClient",
    "classify_google_ads_exception",
    "classify_transport_error",
    "PROBLEM_JSON",
    "problem_body",
    "problem_response",
    "DateRange",
    "FakeGoogleAdsSearchService",
    "GoogleAdsCredentials",
    "GoogleAdsReportingClient",
    "GoogleAdsSearchService",
    "ReportPage",
    "real_search_service_factory",
    "RetryPolicy",
    "execute_with_retry",
    "sync_discovered_accounts",
]
