"""Request-scoped PostgreSQL unit-of-work contract tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.db.postgres_uow import PostgresUnitOfWorkFactory  # noqa: E402


class RecordingTransaction:
    def __init__(self, events: list[str]):
        self.events = events

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")


class RecordingConnection:
    def __init__(self):
        self.events: list[str] = []

    def __enter__(self):  # noqa: ANN204
        self.events.append("connect.enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.events.append("connect.exit")

    def begin(self) -> RecordingTransaction:
        self.events.append("begin")
        return RecordingTransaction(self.events)

    def execute(self, statement, parameters=None):  # noqa: ANN001, ANN202
        self.events.append(f"execute:{statement}")
        return None


class RecordingEngine:
    def __init__(self):
        self.connection = RecordingConnection()

    def connect(self) -> RecordingConnection:
        return self.connection


class PostgresUnitOfWorkTests(unittest.TestCase):
    def test_all_repositories_share_one_connection_and_commit(self) -> None:
        engine = RecordingEngine()
        factory = PostgresUnitOfWorkFactory(engine)  # pyright: ignore[reportArgumentType]

        with factory.request() as work:
            assert work.repositories is not None
            work.bind_principal("753e587a-bcad-46c5-9ed0-169a051adb7b")
            work.repositories.principals._connection.execute(text("SELECT 1"))

        self.assertEqual(
            engine.connection.events,
            [
                "connect.enter",
                "begin",
                "execute:SELECT set_config(:setting_name, :principal_id, true)",
                "execute:SELECT 1",
                "execute:SELECT set_config(:setting_name, '', true)",
                "commit",
                "connect.exit",
            ],
        )
        self.assertIsNone(work.repositories)

    def test_exception_rolls_back_without_context_cleanup_query(self) -> None:
        engine = RecordingEngine()
        factory = PostgresUnitOfWorkFactory(engine)  # pyright: ignore[reportArgumentType]

        with self.assertRaises(RuntimeError), factory.request() as work:
            work.bind_principal("753e587a-bcad-46c5-9ed0-169a051adb7b")
            raise RuntimeError("boom")

        self.assertEqual(
            engine.connection.events,
            [
                "connect.enter",
                "begin",
                "execute:SELECT set_config(:setting_name, :principal_id, true)",
                "rollback",
                "connect.exit",
            ],
        )

    def test_authorization_code_bootstrap_marks_context_for_cleanup(self) -> None:
        engine = RecordingEngine()
        factory = PostgresUnitOfWorkFactory(engine)  # pyright: ignore[reportArgumentType]
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"

        with (
            patch(
                "backend.src.db.postgres_uow.bootstrap_authorization_code_principal",
                return_value=principal_id,
            ),
            factory.request() as work,
        ):
            self.assertEqual(work.bootstrap_authorization_code("raw-code"), principal_id)

        self.assertIn(
            "execute:SELECT set_config(:setting_name, '', true)",
            engine.connection.events,
        )

    def test_access_and_refresh_bootstrap_mark_context_for_cleanup(self) -> None:
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"
        for method_name, helper_name in (
            ("bootstrap_access_token", "bootstrap_access_token_principal"),
            ("bootstrap_refresh_token", "bootstrap_refresh_token_principal"),
            ("bootstrap_web_session", "bootstrap_web_session_principal"),
        ):
            with self.subTest(method=method_name):
                engine = RecordingEngine()
                factory = PostgresUnitOfWorkFactory(  # pyright: ignore[reportArgumentType]
                    engine
                )
                with (
                    patch(
                        f"backend.src.db.postgres_uow.{helper_name}",
                        return_value=principal_id,
                    ),
                    factory.request() as work,
                ):
                    self.assertEqual(getattr(work, method_name)("raw-token"), principal_id)
                self.assertIn(
                    "execute:SELECT set_config(:setting_name, '', true)",
                    engine.connection.events,
                )

    def test_use_before_enter_fails_closed(self) -> None:
        engine = RecordingEngine()
        work = PostgresUnitOfWorkFactory(  # pyright: ignore[reportArgumentType]
            engine
        ).request()

        with self.assertRaisesRegex(RuntimeError, "must be entered"):
            work.bind_principal("753e587a-bcad-46c5-9ed0-169a051adb7b")


if __name__ == "__main__":
    unittest.main()
