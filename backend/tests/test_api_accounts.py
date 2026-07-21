"""Contract tests for Google Ads accessible-account discovery and sync."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.accounts import (  # noqa: E402
    DiscoveredAccount,
    GoogleAdsAccountDiscoveryClient,
    sync_discovered_accounts,
)
from backend.src.api.errors import AdsApiError, ErrorClass  # noqa: E402
from backend.src.api.reporting import GoogleAdsCredentials  # noqa: E402
from backend.src.api.retry import RetryPolicy  # noqa: E402
from backend.src.db.connection import connect  # noqa: E402
from backend.src.db.repository import AdsAccountRepository, PrincipalRepository  # noqa: E402
from google.api_core import exceptions as core_exceptions  # noqa: E402


def _credentials() -> GoogleAdsCredentials:
    return GoogleAdsCredentials(
        developer_token="developer-token",
        client_id="client-id",
        client_secret="client-secret",  # pragma: allowlist secret -- deterministic fake
        refresh_token="refresh-token",
    )


class FakeGateway:
    """Official-RPC-shaped fake that records credential and manager scope."""

    def __init__(self, roots: list[str], hierarchies: dict[str, list[str]]):
        self.roots = roots
        self.hierarchies = hierarchies
        self.list_credentials: list[GoogleAdsCredentials] = []
        self.hierarchy_calls: list[tuple[GoogleAdsCredentials, str]] = []

    def list_accessible_customer_resource_names(
        self, credentials: GoogleAdsCredentials
    ) -> list[str]:
        self.list_credentials.append(credentials)
        return self.roots

    def list_customer_client_ids(
        self, credentials: GoogleAdsCredentials, *, manager_customer_id: str
    ) -> list[str]:
        self.hierarchy_calls.append((credentials, manager_customer_id))
        return self.hierarchies[manager_customer_id]


class GoogleAdsAccountDiscoveryTests(unittest.TestCase):
    def test_direct_and_manager_hierarchy_are_deduplicated_and_scoped(self) -> None:
        gateway = FakeGateway(
            ["customers/1111111111", "customers/2222222222"],
            {
                "1111111111": ["1111111111", "3333333333"],
                "2222222222": ["2222222222", "3333333333", "4444444444"],
            },
        )
        credentials = _credentials()

        discovered = GoogleAdsAccountDiscoveryClient(gateway=gateway).discover(
            credentials=credentials
        )

        self.assertEqual(
            discovered,
            (
                DiscoveredAccount("1111111111", "1111111111"),
                DiscoveredAccount("2222222222", "2222222222"),
                DiscoveredAccount("3333333333", "1111111111"),
                DiscoveredAccount("4444444444", "2222222222"),
            ),
        )
        self.assertEqual(
            [manager for _, manager in gateway.hierarchy_calls],
            ["1111111111", "2222222222"],
        )
        self.assertTrue(all(creds is credentials for creds, _ in gateway.hierarchy_calls))
        self.assertNotIn("refresh-token", repr(discovered))
        self.assertNotIn("client-secret", repr(discovered))

    def test_non_manager_direct_account_uses_no_login_customer_id(self) -> None:
        gateway = FakeGateway(["customers/1234567890"], {"1234567890": []})

        discovered = GoogleAdsAccountDiscoveryClient(gateway=gateway).discover(
            credentials=_credentials()
        )

        self.assertEqual(discovered, (DiscoveredAccount("1234567890", None),))

    def test_malformed_provider_resource_fails_closed_before_hierarchy_call(self) -> None:
        gateway = FakeGateway(["customers/1234/evil"], {})

        with self.assertRaises(AdsApiError) as caught:
            GoogleAdsAccountDiscoveryClient(gateway=gateway).discover(credentials=_credentials())

        self.assertEqual(caught.exception.error_class, ErrorClass.VALIDATION)
        self.assertEqual(caught.exception.code, "invalid_accessible_customer_resource")
        self.assertEqual(gateway.hierarchy_calls, [])
        self.assertNotIn("1234/evil", str(caught.exception))

    def test_transient_list_failure_is_retried_within_budget(self) -> None:
        class TransientGateway(FakeGateway):
            calls = 0

            def list_accessible_customer_resource_names(self, credentials):  # noqa: ANN001, ANN201
                self.calls += 1
                if self.calls == 1:
                    raise core_exceptions.ServiceUnavailable("temporary")
                return ["customers/1234567890"]

        gateway = TransientGateway([], {"1234567890": []})
        client = GoogleAdsAccountDiscoveryClient(
            gateway=gateway,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=0.001,
                max_delay_seconds=0.001,
                max_elapsed_seconds=1,
            ),
        )

        self.assertEqual(
            client.discover(credentials=_credentials()),
            (DiscoveredAccount("1234567890", None),),
        )
        self.assertEqual(gateway.calls, 2)

    def test_auth_failure_is_not_retried_or_leaked(self) -> None:
        class AuthGateway(FakeGateway):
            calls = 0

            def list_accessible_customer_resource_names(self, credentials):  # noqa: ANN001, ANN201
                self.calls += 1
                raise core_exceptions.Unauthenticated("refresh-token secret")

        gateway = AuthGateway([], {})
        with self.assertRaises(AdsApiError) as caught:
            GoogleAdsAccountDiscoveryClient(gateway=gateway).discover(credentials=_credentials())

        self.assertEqual(caught.exception.error_class, ErrorClass.AUTH)
        self.assertEqual(gateway.calls, 1)
        self.assertNotIn("refresh-token", str(caught.exception))


class AccountSynchronizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        self.principals = PrincipalRepository(self.conn)
        self.accounts = AdsAccountRepository(self.conn)
        self.owner = self.principals.get_or_create("issuer", "owner")
        self.other = self.principals.get_or_create("issuer", "other")

    def tearDown(self) -> None:
        self.conn.close()

    def test_sync_links_only_the_authenticated_principal(self) -> None:
        linked = sync_discovered_accounts(
            principal_id=self.owner.id,
            discovered=[DiscoveredAccount("1234567890", "1111111111")],
            accounts=self.accounts,
        )

        self.assertEqual(linked[0].principal_id, self.owner.id)
        self.assertEqual(linked[0].login_customer_id, "1111111111")
        self.assertIsNone(self.accounts.get_account(self.other.id, "1234567890"))

    def test_sync_reactivates_disconnected_row_without_changing_identity(self) -> None:
        original = self.accounts.link_account(self.owner.id, "1234567890", None)
        self.accounts.disconnect_all(self.owner.id)

        linked = sync_discovered_accounts(
            principal_id=self.owner.id,
            discovered=[DiscoveredAccount("1234567890", "1111111111")],
            accounts=self.accounts,
        )

        self.assertEqual(linked[0].id, original.id)
        self.assertEqual(linked[0].status, "active")
        self.assertEqual(linked[0].login_customer_id, "1111111111")

    def test_invalid_discovery_id_never_reaches_repository(self) -> None:
        class RecordingStore:
            calls = 0

            def link_account(self, principal_id, customer_id, login_customer_id):  # noqa: ANN001, ANN201
                self.calls += 1

        store = RecordingStore()
        with self.assertRaises(AdsApiError):
            sync_discovered_accounts(
                principal_id=self.owner.id,
                discovered=[DiscoveredAccount("not-valid", None)],
                accounts=store,  # pyright: ignore[reportArgumentType]
            )
        self.assertEqual(store.calls, 0)


if __name__ == "__main__":
    unittest.main()
