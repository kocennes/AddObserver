"""Read-only Google Ads account discovery adapter.

``ListAccessibleCustomers`` is intentionally kept separate from reporting: it is
one of the few Google Ads RPCs that needs no customer ID and it ignores any
``login-customer-id``.  The response is reduced to validated 10-digit customer
IDs before it can reach persistence or an MCP response.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from .errors import AdsApiError, ErrorClass, classify_google_ads_exception, classify_transport_error
from .queries import validate_customer_id
from .reporting import API_VERSION, GoogleAdsCredentials
from .retry import RetryPolicy, execute_with_retry


class AccessibleCustomerService(Protocol):
    """Minimal boundary around ``CustomerService.ListAccessibleCustomers``."""

    def list_accessible_customers(self) -> Sequence[str]:
        """Return Google customer resource names visible to the OAuth user."""
        ...


AccessibleCustomerServiceFactory = Callable[[GoogleAdsCredentials], AccessibleCustomerService]

MAX_DISCOVERED_ACCOUNTS = 10_000

_CUSTOMER_CLIENT_QUERY = """
SELECT
  customer_client.id,
  customer_client.manager,
  customer_client.status
FROM customer_client
WHERE customer_client.status = ENABLED
ORDER BY customer_client.id
""".strip()


@dataclass(frozen=True, slots=True)
class DiscoveredAccount:
    """One customer and the manager login needed for subsequent API calls."""

    customer_id: str
    login_customer_id: str | None


class CustomerHierarchyService(Protocol):
    """Minimal boundary around one fixed ``customer_client`` GAQL query."""

    def list_enabled_customer_ids(self, *, manager_customer_id: str) -> Iterable[str]:
        """Return enabled self/descendant customer IDs under one accessible root."""
        ...


CustomerHierarchyServiceFactory = Callable[[GoogleAdsCredentials, str], CustomerHierarchyService]


class _RealAccessibleCustomerService:
    """Adapt the official CustomerService client to the narrow discovery protocol."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_accessible_customers(self) -> Sequence[str]:
        response = self._service.list_accessible_customers()
        return tuple(response.resource_names)


class _RealCustomerHierarchyService:
    """Adapt ``GoogleAdsService.Search`` without exposing raw rows downstream."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_enabled_customer_ids(self, *, manager_customer_id: str) -> Iterable[str]:
        pager = self._service.search(
            customer_id=manager_customer_id,
            query=_CUSTOMER_CLIENT_QUERY,
        )
        for row in pager:
            yield str(row.customer_client.id)


def real_accessible_customer_service_factory(
    credentials: GoogleAdsCredentials,
) -> AccessibleCustomerService:
    """Build a fresh official client without applying ``login_customer_id``.

    Google documents that this RPC ignores that header. Omitting it also makes
    the credential boundary explicit and prevents a stale manager selection from
    appearing to influence direct-access discovery.
    """
    config: dict[str, Any] = {
        "developer_token": credentials.developer_token,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "use_proto_plus": True,
    }
    client = GoogleAdsClient.load_from_dict(config, version=API_VERSION)
    return _RealAccessibleCustomerService(client.get_service("CustomerService"))


def real_customer_hierarchy_service_factory(
    credentials: GoogleAdsCredentials, manager_customer_id: str
) -> CustomerHierarchyService:
    """Build an isolated client whose login header is the direct-access manager root."""
    validated_manager_id = validate_customer_id(manager_customer_id)
    config: dict[str, Any] = {
        "developer_token": credentials.developer_token,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "login_customer_id": validated_manager_id,
        "use_proto_plus": True,
    }
    client = GoogleAdsClient.load_from_dict(config, version=API_VERSION)
    return _RealCustomerHierarchyService(client.get_service("GoogleAdsService"))


class GoogleAdsAccountDiscoveryClient:
    """Discover and validate accounts directly accessible to one OAuth credential."""

    def __init__(
        self,
        *,
        service_factory: AccessibleCustomerServiceFactory = (
            real_accessible_customer_service_factory
        ),
        hierarchy_service_factory: CustomerHierarchyServiceFactory = (
            real_customer_hierarchy_service_factory
        ),
        retry_policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        self._service_factory = service_factory
        self._hierarchy_service_factory = hierarchy_service_factory
        self._retry_policy = retry_policy

    def list_direct_customer_ids(self, credentials: GoogleAdsCredentials) -> tuple[str, ...]:
        """Return unique, sorted 10-digit IDs; reject malformed provider output."""
        try:
            service = self._service_factory(credentials)
        except GoogleAdsException as exc:
            raise classify_google_ads_exception(exc) from exc
        except AdsApiError:
            raise
        except Exception as exc:  # noqa: BLE001 -- normalized at the adapter boundary
            raise classify_transport_error(exc) from exc

        def _call() -> Sequence[str]:
            try:
                return service.list_accessible_customers()
            except GoogleAdsException as exc:
                raise classify_google_ads_exception(exc) from exc
            except AdsApiError:
                raise
            except Exception as exc:  # noqa: BLE001 -- normalized at the adapter boundary
                raise classify_transport_error(exc) from exc

        resource_names = execute_with_retry(
            _call,
            classify=lambda exc: (
                exc if isinstance(exc, AdsApiError) else classify_transport_error(exc)
            ),
            policy=self._retry_policy,
        )
        customer_ids: set[str] = set()
        for resource_name in resource_names:
            prefix = "customers/"
            if not isinstance(resource_name, str) or not resource_name.startswith(prefix):
                raise _invalid_resource_name()
            customer_id = resource_name[len(prefix) :]
            if "/" in customer_id:
                raise _invalid_resource_name()
            try:
                customer_ids.add(validate_customer_id(customer_id))
            except AdsApiError as exc:
                raise _invalid_resource_name() from exc
        return tuple(sorted(customer_ids))

    def discover_accounts(self, credentials: GoogleAdsCredentials) -> tuple[DiscoveredAccount, ...]:
        """Discover direct roots and enabled descendants with deterministic login routing.

        A customer directly accessible to the OAuth user always wins over a manager-derived
        path and therefore stores no login customer. If multiple managers expose the same
        descendant, the numerically smallest direct manager wins so repeated syncs are stable.
        """
        direct_ids = self.list_direct_customer_ids(credentials)
        discovered = {
            customer_id: DiscoveredAccount(customer_id=customer_id, login_customer_id=None)
            for customer_id in direct_ids
        }
        for manager_id in direct_ids:
            try:
                service = self._hierarchy_service_factory(credentials, manager_id)
                descendants = execute_with_retry(
                    lambda service=service, manager_id=manager_id: tuple(
                        service.list_enabled_customer_ids(manager_customer_id=manager_id)
                    ),
                    classify=lambda exc: (
                        exc if isinstance(exc, AdsApiError) else classify_transport_error(exc)
                    ),
                    policy=self._retry_policy,
                )
                for raw_customer_id in descendants:
                    customer_id = validate_customer_id(raw_customer_id)
                    if customer_id not in discovered:
                        discovered[customer_id] = DiscoveredAccount(
                            customer_id=customer_id,
                            login_customer_id=manager_id,
                        )
                    if len(discovered) > MAX_DISCOVERED_ACCOUNTS:
                        raise AdsApiError(
                            error_class=ErrorClass.VALIDATION,
                            code="accessible_account_limit_exceeded",
                            message="Google Ads hesap hiyerarsisi guvenli sonuc sinirini asti.",
                            request_id=None,
                        )
            except GoogleAdsException as exc:
                raise classify_google_ads_exception(exc) from exc
            except AdsApiError:
                raise
            except Exception as exc:  # noqa: BLE001 -- normalized at adapter boundary
                raise classify_transport_error(exc) from exc
        return tuple(discovered[customer_id] for customer_id in sorted(discovered))


def _invalid_resource_name() -> AdsApiError:
    return AdsApiError(
        error_class=ErrorClass.VALIDATION,
        code="invalid_accessible_customer_resource",
        message="Google Ads hesap listesi beklenmeyen bir kaynak kimligi dondurdu.",
        request_id=None,
    )


class FakeAccessibleCustomerService:
    """Deterministic no-network test double for direct account discovery."""

    def __init__(
        self, resource_names: Sequence[str] = (), *, raises: Exception | None = None
    ) -> None:
        self._resource_names = tuple(resource_names)
        self._raises = raises
        self.calls = 0

    def list_accessible_customers(self) -> Sequence[str]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._resource_names


class FakeCustomerHierarchyService:
    """No-network hierarchy test double keyed by manager customer ID."""

    def __init__(
        self, customer_ids: Sequence[str] = (), *, raises: Exception | None = None
    ) -> None:
        self._customer_ids = tuple(customer_ids)
        self._raises = raises
        self.calls: list[str] = []

    def list_enabled_customer_ids(self, *, manager_customer_id: str) -> Iterable[str]:
        self.calls.append(manager_customer_id)
        if self._raises is not None:
            raise self._raises
        return self._customer_ids
