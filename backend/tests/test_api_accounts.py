"""Contract tests for direct Google Ads account discovery."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.accounts import (
    DiscoveredAccount,
    FakeAccessibleCustomerService,
    FakeCustomerHierarchyService,
    GoogleAdsAccountDiscoveryClient,
)
from backend.src.api.errors import AdsApiError, ErrorClass
from backend.src.api.reporting import GoogleAdsCredentials
from backend.src.api.retry import RetryPolicy
from google.api_core.exceptions import ServiceUnavailable


def _credentials() -> GoogleAdsCredentials:
    return GoogleAdsCredentials(
        developer_token="developer-secret",
        client_id="client-id",
        client_secret="client-secret",
        refresh_token="refresh-secret",
        login_customer_id="9999999999",
    )


class GoogleAdsAccountDiscoveryTests(unittest.TestCase):
    """The adapter exposes only validated IDs and normalized failures."""

    def test_maps_deduplicates_and_sorts_direct_customer_resource_names(self) -> None:
        service = FakeAccessibleCustomerService(
            ["customers/2222222222", "customers/1111111111", "customers/2222222222"]
        )
        client = GoogleAdsAccountDiscoveryClient(service_factory=lambda _credentials: service)

        result = client.list_direct_customer_ids(_credentials())

        self.assertEqual(result, ("1111111111", "2222222222"))
        self.assertEqual(service.calls, 1)

    def test_empty_direct_access_list_is_valid(self) -> None:
        service = FakeAccessibleCustomerService()
        client = GoogleAdsAccountDiscoveryClient(service_factory=lambda _credentials: service)

        self.assertEqual(client.list_direct_customer_ids(_credentials()), ())

    def test_rejects_malformed_or_nested_provider_resource_names(self) -> None:
        for resource_name in (
            "customer/1234567890",
            "customers/123",
            "customers/1234567890/customerClients/9999999999",
            "customers/12345abcde",
        ):
            with self.subTest(resource_name=resource_name):
                service = FakeAccessibleCustomerService([resource_name])
                client = GoogleAdsAccountDiscoveryClient(
                    service_factory=lambda _credentials, service=service: service
                )
                with self.assertRaises(AdsApiError) as caught:
                    client.list_direct_customer_ids(_credentials())
                self.assertEqual(caught.exception.code, "invalid_accessible_customer_resource")
                self.assertNotIn(resource_name, caught.exception.message)

    def test_transient_rpc_failure_uses_the_central_retry_policy(self) -> None:
        service = FakeAccessibleCustomerService(raises=ServiceUnavailable("temporary"))
        client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: service,
            retry_policy=RetryPolicy(
                max_attempts=2, base_delay_seconds=0.001, max_delay_seconds=0.001
            ),
        )

        with self.assertRaises(AdsApiError) as caught:
            client.list_direct_customer_ids(_credentials())

        self.assertEqual(caught.exception.error_class, ErrorClass.TRANSIENT)
        self.assertEqual(service.calls, 2)

    def test_service_factory_failure_is_normalized_without_secret_leakage(self) -> None:
        def broken_factory(_credentials: GoogleAdsCredentials) -> FakeAccessibleCustomerService:
            raise RuntimeError("refresh-secret")

        client = GoogleAdsAccountDiscoveryClient(service_factory=broken_factory)
        with self.assertRaises(AdsApiError) as caught:
            client.list_direct_customer_ids(_credentials())

        self.assertEqual(caught.exception.code, "transport.unclassified")
        self.assertNotIn("refresh-secret", str(caught.exception))

    def test_discovers_manager_descendants_and_prefers_direct_access(self) -> None:
        direct = FakeAccessibleCustomerService(["customers/2222222222", "customers/1111111111"])
        hierarchies = {
            "1111111111": FakeCustomerHierarchyService(["1111111111", "3333333333", "4444444444"]),
            "2222222222": FakeCustomerHierarchyService(["2222222222", "3333333333", "1111111111"]),
        }
        client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: direct,
            hierarchy_service_factory=lambda _credentials, manager_id: hierarchies[manager_id],
        )

        result = client.discover_accounts(_credentials())

        self.assertEqual(
            result,
            (
                DiscoveredAccount("1111111111", None),
                DiscoveredAccount("2222222222", None),
                DiscoveredAccount("3333333333", "1111111111"),
                DiscoveredAccount("4444444444", "1111111111"),
            ),
        )
        self.assertEqual(hierarchies["1111111111"].calls, ["1111111111"])
        self.assertEqual(hierarchies["2222222222"].calls, ["2222222222"])

    def test_rejects_malformed_hierarchy_customer_id_without_leaking_it(self) -> None:
        direct = FakeAccessibleCustomerService(["customers/1111111111"])
        hierarchy = FakeCustomerHierarchyService(["not-a-customer"])
        client = GoogleAdsAccountDiscoveryClient(
            service_factory=lambda _credentials: direct,
            hierarchy_service_factory=lambda _credentials, _manager: hierarchy,
        )

        with self.assertRaises(AdsApiError) as caught:
            client.discover_accounts(_credentials())

        self.assertEqual(caught.exception.code, "invalid_customer_id")
        self.assertNotIn("not-a-customer", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
