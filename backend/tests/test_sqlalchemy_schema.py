"""Tests for the PostgreSQL SQLAlchemy/Alembic schema contract."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.db.postgres_context import (
    AUTHORIZATION_CODE_HASH_SETTING,
    PRINCIPAL_CONTEXT_SETTING,
    WEB_SESSION_HASH_SETTING,
    bootstrap_authorization_code_principal,
    clear_transaction_principal,
    normalize_principal_uuid,
    set_transaction_principal,
)
from backend.src.db.sqlalchemy_schema import PRODUCTION_TABLES, RLS_TABLES, metadata


class ProductionSchemaTests(unittest.TestCase):
    def test_alembic_initial_revision_is_discoverable(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260718_0001")

        assert revision is not None
        self.assertIn("20260722_0007", script.get_heads())
        self.assertIsNone(revision.down_revision)
        self.assertTrue(callable(revision.module.upgrade))
        self.assertTrue(callable(revision.module.downgrade))

    def test_rls_revision_enables_force_rls_for_principal_scoped_tables(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260718_0002")

        assert revision is not None
        self.assertEqual(revision.down_revision, "20260718_0001")
        self.assertEqual(
            tuple(revision.module.RLS_TABLES),
            tuple(table for table in RLS_TABLES if table != "credential_revocation_job"),
        )
        self.assertIn(
            "current_setting('app.current_principal_id', true)",
            revision.module.PRINCIPAL_POLICY,
        )

    def test_authorization_code_bootstrap_policy_is_exact_hash_select_only(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260719_0003")

        assert revision is not None
        self.assertEqual(revision.down_revision, "20260718_0002")
        self.assertEqual(
            revision.module.BOOTSTRAP_SETTING,
            AUTHORIZATION_CODE_HASH_SETTING,
        )
        source = Path(revision.path).read_text(encoding="utf-8")
        self.assertIn("ON authorization_code FOR SELECT", source)
        self.assertIn("code_hash = nullif", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_access_and_refresh_bootstrap_policies_are_exact_hash_select_only(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260719_0004")

        assert revision is not None
        self.assertEqual(revision.down_revision, "20260719_0003")
        self.assertEqual(tuple(revision.module.TOKEN_TABLES), ("access_token", "refresh_token"))
        source = Path(revision.path).read_text(encoding="utf-8")
        self.assertIn("FOR SELECT", source)
        self.assertIn("token_hash = nullif", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_web_session_bootstrap_policy_is_exact_hash_select_only(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260719_0005")

        assert revision is not None
        self.assertEqual(revision.down_revision, "20260719_0004")
        self.assertEqual(revision.module.BOOTSTRAP_SETTING, WEB_SESSION_HASH_SETTING)
        source = Path(revision.path).read_text(encoding="utf-8")
        self.assertIn("ON web_session FOR SELECT", source)
        self.assertIn("token_hash = nullif", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_initial_table_inventory_matches_adr_0006(self) -> None:
        self.assertEqual(
            set(PRODUCTION_TABLES),
            {
                "principal",
                "ads_account",
                "oauth_client_grant",
                "oauth_credential",
                "credential_revocation_job",
                "authorization_transaction",
                "authorization_code",
                "access_token",
                "refresh_token",
                "web_login_state",
                "web_session",
                "proposal",
                "approval",
                "execution",
                "audit_event",
            },
        )
        self.assertNotIn("vault_secret", metadata.tables)
        self.assertNotIn("analysis_run", metadata.tables)
        self.assertTrue(set(RLS_TABLES).issubset(PRODUCTION_TABLES))

    def test_credential_revocation_outbox_is_owned_and_rls_protected(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260719_0006")

        assert revision is not None
        self.assertEqual(revision.down_revision, "20260719_0005")
        source = Path(revision.path).read_text(encoding="utf-8")
        self.assertIn("ENABLE ROW LEVEL SECURITY", source)
        self.assertIn("FORCE ROW LEVEL SECURITY", source)
        self.assertIn("WITH CHECK", source)
        table = metadata.tables["credential_revocation_job"]
        composite_fks = [
            constraint
            for constraint in table.constraints
            if isinstance(constraint, ForeignKeyConstraint) and len(constraint.columns) == 2
        ]
        self.assertEqual(len(composite_fks), 1)
        self.assertEqual(
            {column.name for column in composite_fks[0].columns},
            {"credential_id", "principal_id"},
        )

    def test_audit_append_only_migration_rejects_update_and_delete(self) -> None:
        script = ScriptDirectory(str(ROOT / "backend" / "alembic"))
        revision = script.get_revision("20260722_0007")
        assert revision is not None
        self.assertEqual(revision.down_revision, "20260719_0006")
        source = Path(revision.path).read_text(encoding="utf-8")
        self.assertIn("BEFORE UPDATE OR DELETE ON audit_event", source)
        self.assertIn("RAISE EXCEPTION 'audit_event is append-only'", source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_principal_scoped_tables_require_principal_id(self) -> None:
        nullable_exceptions = {"audit_event"}
        scoped_tables = set(PRODUCTION_TABLES) - {
            "principal",
            "authorization_transaction",
            "web_login_state",
        }

        for table_name in scoped_tables:
            with self.subTest(table=table_name):
                column = metadata.tables[table_name].c.principal_id
                self.assertEqual(column.nullable, table_name in nullable_exceptions)

    def test_composite_ownership_constraints_exist_for_child_tables(self) -> None:
        for table_name in ("approval", "execution"):
            table = metadata.tables[table_name]
            composite_fks = [
                constraint
                for constraint in table.constraints
                if isinstance(constraint, ForeignKeyConstraint) and len(constraint.columns) == 2
            ]
            self.assertEqual(len(composite_fks), 1)
            self.assertEqual(
                {column.name for column in composite_fks[0].columns},
                {"proposal_id", "principal_id"},
            )

    def test_idempotency_key_is_scoped_to_principal_and_proposal(self) -> None:
        execution = metadata.tables["execution"]
        unique_constraints = [
            constraint
            for constraint in execution.constraints
            if isinstance(constraint, UniqueConstraint)
        ]
        self.assertIn(
            ("principal_id", "proposal_id", "idempotency_key"),
            {
                tuple(column.name for column in constraint.columns)
                for constraint in unique_constraints
            },
        )

    def test_approval_decision_values_match_domain_enum(self) -> None:
        approval_table = metadata.tables["approval"]
        ddl = str(CreateTable(approval_table).compile(dialect=postgresql.dialect()))

        self.assertIn("decision in ('approve', 'reject')", ddl)

    def test_refresh_family_id_accepts_domain_token_format(self) -> None:
        family_id = metadata.tables["refresh_token"].c.family_id

        self.assertIsInstance(family_id.type, Text)

    def test_authorization_transaction_matches_domain_opaque_id_and_statuses(self) -> None:
        """Production DDL accepts the OAuth domain's opaque IDs and statuses."""
        from backend.src.auth.domain import TransactionStatus

        transaction = metadata.tables["authorization_transaction"]
        code = metadata.tables["authorization_code"]
        self.assertIsInstance(transaction.c.id.type, Text)
        self.assertIsInstance(code.c.transaction_id.type, Text)
        status_constraint = next(
            constraint
            for constraint in transaction.constraints
            if constraint.name is not None and constraint.name.endswith("_status")
        )
        sql = str(status_constraint.sqltext)
        for status in TransactionStatus:
            self.assertIn(f"'{status.value}'", sql)
        self.assertNotIn("google_authorized", sql)
        self.assertNotIn("expired", sql)

    def test_postgresql_ddl_uses_jsonb_uuid_and_timestamptz(self) -> None:
        ddl = "\n".join(
            str(CreateTable(table).compile(dialect=postgresql.dialect()))
            for table in metadata.sorted_tables
        )
        self.assertIn("UUID", ddl)
        self.assertIn("JSONB", ddl)
        self.assertIn("TIMESTAMP WITH TIME ZONE", ddl)

    def test_transaction_principal_context_uses_validated_transaction_local_setting(self) -> None:
        class RecordingConnection:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str]]] = []

            def execute(self, statement, parameters):  # noqa: ANN001
                self.calls.append((str(statement), parameters))

        connection = RecordingConnection()
        set_transaction_principal(
            connection,
            "753e587a-bcad-46c5-9ed0-169a051adb7b",  # pyright: ignore[reportArgumentType]
        )
        clear_transaction_principal(connection)  # pyright: ignore[reportArgumentType]

        self.assertEqual(
            connection.calls[0][0],
            "SELECT set_config(:setting_name, :principal_id, true)",
        )
        self.assertEqual(connection.calls[0][1]["setting_name"], PRINCIPAL_CONTEXT_SETTING)
        self.assertEqual(
            connection.calls[0][1]["principal_id"],
            "753e587a-bcad-46c5-9ed0-169a051adb7b",
        )
        self.assertEqual(connection.calls[1][1], {"setting_name": PRINCIPAL_CONTEXT_SETTING})

    def test_invalid_principal_context_uuid_is_rejected_before_sql_execution(self) -> None:
        with self.assertRaises(ValueError):
            normalize_principal_uuid("not-a-uuid")

    def test_authorization_code_bootstrap_clears_hash_then_sets_principal(self) -> None:
        principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"

        class Result:
            def scalar_one_or_none(self) -> str:
                return principal_id

        class RecordingConnection:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, str] | None]] = []

            def execute(self, statement, parameters=None):  # noqa: ANN001, ANN202
                self.calls.append((str(statement), parameters))
                return Result()

        connection = RecordingConnection()
        resolved = bootstrap_authorization_code_principal(
            connection,  # pyright: ignore[reportArgumentType]
            "raw-authorization-code",
        )

        self.assertEqual(resolved, principal_id)
        assert connection.calls[0][1] is not None
        self.assertEqual(
            connection.calls[0][1]["setting_name"],
            AUTHORIZATION_CODE_HASH_SETTING,
        )
        self.assertNotIn("raw-authorization-code", str(connection.calls))
        self.assertEqual(
            connection.calls[2][1],
            {"setting_name": AUTHORIZATION_CODE_HASH_SETTING},
        )
        self.assertEqual(
            connection.calls[3][1],
            {"setting_name": PRINCIPAL_CONTEXT_SETTING, "principal_id": principal_id},
        )


if __name__ == "__main__":
    unittest.main()
