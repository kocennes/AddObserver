"""Tests for the PostgreSQL runtime transaction helper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.domain import AuthError  # noqa: E402
from backend.src.config import Settings  # noqa: E402
from backend.src.db.postgres import (  # noqa: E402
    authorization_code_transaction,
    create_postgres_engine,
    principal_transaction,
)

SECRET_DSN = "postgresql+psycopg://user:secret-password@db.example.test:5432/addobserver"


class PostgreSQLRuntimeTests(unittest.TestCase):
    def test_settings_hides_database_url_from_repr(self) -> None:
        settings = Settings(
            sqlite_db_path="backend/.data/local.db",
            environment="local",
            public_base_url="https://mcp.example.com",
            mcp_resource_path="/mcp",
            local_vault_key="vault-key",
            google_client_id="visible-client-id",
            google_client_secret="google-secret",
            google_ads_developer_token="developer-token",
            allowed_hosts=("mcp.example.com",),
            cors_allowed_origins=(),
            database_url=SECRET_DSN,
        )

        self.assertNotIn("secret-password", repr(settings))
        self.assertNotIn(SECRET_DSN, str(settings))

    def test_create_postgres_engine_rejects_missing_or_non_postgres_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "required"):
            create_postgres_engine("")

        with self.assertRaisesRegex(ValueError, "postgresql"):
            create_postgres_engine("sqlite:///local.db")

    def test_create_postgres_engine_redacts_invalid_url(self) -> None:
        secret = "do-not-leak"

        with self.assertRaises(ValueError) as raised:
            create_postgres_engine(f"not a url {secret}")

        self.assertNotIn(secret, str(raised.exception))

    def test_create_postgres_engine_uses_pool_pre_ping_without_leaking_dsn(self) -> None:
        captured: dict[str, object] = {}

        def fake_create_engine(url, **kwargs):  # noqa: ANN001, ANN202
            captured["url"] = url
            captured["kwargs"] = kwargs
            return object()

        with patch("backend.src.db.postgres.create_engine", fake_create_engine):
            engine = create_postgres_engine(SECRET_DSN)

        self.assertIsInstance(engine, object)
        self.assertEqual(captured["kwargs"], {"pool_pre_ping": True})
        self.assertEqual(captured["url"].get_backend_name(), "postgresql")

    def test_create_postgres_engine_defaults_to_psycopg_three(self) -> None:
        captured: dict[str, object] = {}

        def fake_create_engine(url, **kwargs):  # noqa: ANN001, ANN202
            captured["url"] = url
            return object()

        with patch("backend.src.db.postgres.create_engine", fake_create_engine):
            create_postgres_engine("postgresql://user:password@db.example.test/addobserver")

        self.assertEqual(captured["url"].drivername, "postgresql+psycopg")

    def test_principal_transaction_sets_clears_and_commits_in_order(self) -> None:
        engine = RecordingEngine()

        with principal_transaction(
            engine,
            "753e587a-bcad-46c5-9ed0-169a051adb7b",  # pyright: ignore[reportArgumentType]
        ) as connection:
            connection.execute(text("SELECT 1"), {})

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

    def test_principal_transaction_rolls_back_without_commit_on_error(self) -> None:
        engine = RecordingEngine()

        with (
            self.assertRaises(RuntimeError),
            principal_transaction(
                engine,
                "753e587a-bcad-46c5-9ed0-169a051adb7b",  # pyright: ignore[reportArgumentType]
            ),
        ):
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

    def test_authorization_code_transaction_commits_after_bootstrap(self) -> None:
        engine = RecordingEngine()
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"

        with (
            patch(
                "backend.src.db.postgres.bootstrap_authorization_code_principal",
                return_value=principal_id,
            ),
            authorization_code_transaction(
                engine,
                "raw-code",  # pyright: ignore[reportArgumentType]
            ) as connection,
        ):
            connection.execute(text("SELECT claim"), {})

        self.assertEqual(
            engine.connection.events,
            [
                "connect.enter",
                "begin",
                "execute:SELECT claim",
                "execute:SELECT set_config(:setting_name, '', true)",
                "commit",
                "connect.exit",
            ],
        )

    def test_authorization_code_transaction_unknown_code_rolls_back(self) -> None:
        engine = RecordingEngine()

        with (
            patch(
                "backend.src.db.postgres.bootstrap_authorization_code_principal",
                return_value=None,
            ),
            self.assertRaises(AuthError),
            authorization_code_transaction(
                engine,
                "unknown-code",  # pyright: ignore[reportArgumentType]
            ),
        ):
            pass

        self.assertEqual(
            engine.connection.events,
            ["connect.enter", "begin", "rollback", "connect.exit"],
        )


class RecordingTransaction:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def commit(self) -> None:
        self._events.append("commit")

    def rollback(self) -> None:
        self._events.append("rollback")


class RecordingConnection:
    def __init__(self) -> None:
        self.events: list[str] = []

    def __enter__(self) -> RecordingConnection:
        self.events.append("connect.enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.events.append("connect.exit")

    def begin(self) -> RecordingTransaction:
        self.events.append("begin")
        return RecordingTransaction(self.events)

    def execute(self, statement, parameters):  # noqa: ANN001, ANN202
        self.events.append(f"execute:{statement}")
        return None


class RecordingEngine:
    def __init__(self) -> None:
        self.connection = RecordingConnection()

    def connect(self) -> RecordingConnection:
        return self.connection


if __name__ == "__main__":
    unittest.main()
