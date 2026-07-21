"""PostgreSQL transaction-boundary tests for connector authorization routes."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.server import _authorization_transactions  # noqa: E402


class FakeTransactions:
    pass


class FakeWork:
    def __init__(self, transactions: FakeTransactions):
        self.repositories = SimpleNamespace(authorization_transactions=transactions)
        self.entered = False
        self.exited = False
        self.exit_exception_type: type[BaseException] | None = None

    def __enter__(self):  # noqa: ANN204
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.exited = True
        self.exit_exception_type = exc_type


class FakeFactory:
    def __init__(self, work: FakeWork):
        self.work = work

    def request(self) -> FakeWork:
        return self.work


class PostgresAuthorizeRouteTests(unittest.TestCase):
    def test_authorization_transaction_store_uses_short_postgres_work(self) -> None:
        transactions = FakeTransactions()
        work = FakeWork(transactions)
        context = SimpleNamespace(postgres_uow_factory=FakeFactory(work))

        with _authorization_transactions(context) as store:  # pyright: ignore[reportArgumentType]
            self.assertIs(store, transactions)
            self.assertTrue(work.entered)
            self.assertFalse(work.exited)

        self.assertTrue(work.exited)
        self.assertIsNone(work.exit_exception_type)

    def test_authorization_transaction_store_rolls_back_on_route_error(self) -> None:
        work = FakeWork(FakeTransactions())
        context = SimpleNamespace(postgres_uow_factory=FakeFactory(work))

        with (
            self.assertRaisesRegex(RuntimeError, "route failed"),
            _authorization_transactions(context),  # pyright: ignore[reportArgumentType]
        ):
            raise RuntimeError("route failed")

        self.assertTrue(work.exited)
        self.assertIs(work.exit_exception_type, RuntimeError)


if __name__ == "__main__":
    unittest.main()
