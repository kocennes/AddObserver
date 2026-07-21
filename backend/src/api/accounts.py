"""Read-only Google Ads account discovery and principal-scoped synchronization."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

from ..db.models import AdsAccount
from .errors import AdsApiError, ErrorClass, classify_google_ads_exception, classify_transport_error
from .queries import validate_customer_id
from .reporting import API_VERSION, GoogleAdsCredentials
from .retry import RetryPolicy, execute_with_retry

_CUSTOMER_RESOURCE_PREFIX = "customers/"
_HIERARCHY_QUERY = """
SELECT
  customer_client.id,
  customer_client.manager,
  customer_client.level,
  customer_client.hidden,
  customer_client.status
FROM customer_client
WHERE customer_client.status != 'CANCELED'
""".strip()


@dataclass(frozen=True, slots=True)
class DiscoveredAccount:
    """One validated Google Ads account and its request manager context."""

    customer_id: str
    login_customer_id: str | None


class AccountDiscoveryGateway(Protocol):
    """Mockable boundary around Google Ads account discovery RPCs."""

    def list_accessible_customer_resource_names(
        self, credentials: GoogleAdsCredentials
    ) -> Iterable[str]:
        """Return direct-access customer resource names."""
        ...

    def list_customer_client_ids(
        self, credentials: GoogleAdsCredentials, *, manager_customer_id: str
    ) -> Iterable[str]:
        """Return the manager and all visible descendants in its hierarchy."""
        ...


class AccountStore(Protocol):
    """Principal-scoped persistence contract shared by SQLite/PostgreSQL."""

    def link_account(
        self, principal_id: str, customer_id: str, login_customer_id: str | None
    ) -> AdsAccount:
        """Create or reactivate one principal-owned account link."""
        ...


def _client_config(credentials: GoogleAdsCredentials, *, login_customer_id: str | None) -> dict:
    config: dict[str, Any] = {
        "developer_token": credentials.developer_token,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "use_proto_plus": True,
    }
    if login_customer_id is not None:
        config["login_customer_id"] = login_customer_id
    return config


class RealAccountDiscoveryGateway:
    """Official Google Ads Python client implementation of account discovery."""

    def list_accessible_customer_resource_names(
        self, credentials: GoogleAdsCredentials
    ) -> Iterable[str]:
        """Call CustomerService.ListAccessibleCustomers without a login header."""
        client = GoogleAdsClient.load_from_dict(
            _client_config(credentials, login_customer_id=None), version=API_VERSION
        )
        response = client.get_service("CustomerService").list_accessible_customers()
        return tuple(str(name) for name in response.resource_names)

    def list_customer_client_ids(
        self, credentials: GoogleAdsCredentials, *, manager_customer_id: str
    ) -> Iterable[str]:
        """Query the manager's complete customer_client hierarchy."""
        client = GoogleAdsClient.load_from_dict(
            _client_config(credentials, login_customer_id=manager_customer_id),
            version=API_VERSION,
        )
        pager = client.get_service("GoogleAdsService").search(
            customer_id=manager_customer_id,
            query=_HIERARCHY_QUERY,
        )
        return tuple(str(row.customer_client.id) for row in pager)


class GoogleAdsAccountDiscoveryClient:
    """Discover direct and manager-child accounts without exposing credentials."""

    def __init__(
        self,
        *,
        gateway: AccountDiscoveryGateway | None = None,
        retry_policy: RetryPolicy = RetryPolicy(),
    ) -> None:
        self._gateway = gateway or RealAccountDiscoveryGateway()
        self._retry_policy = retry_policy

    def discover(self, *, credentials: GoogleAdsCredentials) -> tuple[DiscoveredAccount, ...]:
        """Return a deduplicated, validated snapshot of all accessible accounts."""
        roots = self._call(
            lambda: self._gateway.list_accessible_customer_resource_names(credentials)
        )
        discovered: dict[str, DiscoveredAccount] = {}
        for resource_name in roots:
            root_id = self._parse_customer_resource_name(str(resource_name))
            children = tuple(
                self._call(
                    lambda root_id=root_id: self._gateway.list_customer_client_ids(
                        credentials, manager_customer_id=root_id
                    )
                )
            )
            if not children:
                discovered.setdefault(root_id, DiscoveredAccount(root_id, None))
                continue
            for child_id in children:
                validated_child = validate_customer_id(str(child_id))
                discovered.setdefault(
                    validated_child,
                    DiscoveredAccount(validated_child, root_id),
                )
        return tuple(discovered[customer_id] for customer_id in sorted(discovered))

    def _call(self, operation: Callable[[], Iterable[str]]) -> Iterable[str]:
        def call() -> Iterable[str]:
            try:
                return operation()
            except GoogleAdsException as error:
                raise classify_google_ads_exception(error) from error
            except AdsApiError:
                raise
            except Exception as error:  # noqa: BLE001 -- normalized at adapter boundary
                raise classify_transport_error(error) from error

        return execute_with_retry(
            call,
            classify=lambda error: (
                error if isinstance(error, AdsApiError) else classify_transport_error(error)
            ),
            policy=self._retry_policy,
        )

    @staticmethod
    def _parse_customer_resource_name(resource_name: str) -> str:
        if not resource_name.startswith(_CUSTOMER_RESOURCE_PREFIX):
            raise AdsApiError(
                error_class=ErrorClass.VALIDATION,
                code="invalid_accessible_customer_resource",
                message="Google Ads erisilebilir hesap cevabi gecersiz.",
                request_id=None,
            )
        customer_id = resource_name.removeprefix(_CUSTOMER_RESOURCE_PREFIX)
        if "/" in customer_id:
            raise AdsApiError(
                error_class=ErrorClass.VALIDATION,
                code="invalid_accessible_customer_resource",
                message="Google Ads erisilebilir hesap cevabi gecersiz.",
                request_id=None,
            )
        try:
            return validate_customer_id(customer_id)
        except AdsApiError as error:
            raise AdsApiError(
                error_class=ErrorClass.VALIDATION,
                code="invalid_accessible_customer_resource",
                message="Google Ads erisilebilir hesap cevabi gecersiz.",
                request_id=error.request_id,
            ) from error


def sync_discovered_accounts(
    *,
    principal_id: str,
    discovered: Iterable[DiscoveredAccount],
    accounts: AccountStore,
) -> tuple[AdsAccount, ...]:
    """Persist discovery only inside the authenticated principal's namespace."""
    linked = []
    for account in discovered:
        customer_id = validate_customer_id(account.customer_id)
        login_customer_id = (
            None
            if account.login_customer_id is None
            else validate_customer_id(account.login_customer_id)
        )
        linked.append(accounts.link_account(principal_id, customer_id, login_customer_id))
    return tuple(linked)
