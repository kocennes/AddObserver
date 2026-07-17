"""Integration-style tests for the fail-closed mutation application boundary."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.approval import MutationOutcome, execute_reserved_mutation
from backend.src.approval.domain import ExecutionReservation
from backend.src.db.connection import connect
from backend.src.db.models import ExecutionStatus
from backend.src.db.proposals import AuditRepository, ExecutionRepository


class FakeAdapter:
    def __init__(self, outcome: MutationOutcome | None = None, error: Exception | None = None):
        self.outcome = outcome
        self.error = error
        self.calls = 0

    def apply(self, reservation, payload):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.outcome


class FailingAudit:
    def insert(self, event):
        raise RuntimeError("audit unavailable")


class FailingCompletionAudit:
    def __init__(self):
        self.calls = 0

    def insert(self, event):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("completion audit unavailable")


class ApprovalApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        now = datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        self.conn.execute(
            "INSERT INTO principal (id, issuer, subject, status, created_at) VALUES (?, ?, ?, ?, ?)",
            ("principal-a", "issuer", "subject", "active", now.isoformat()),
        )
        self.conn.execute(
            "INSERT INTO proposal (id, principal_id, customer_id, payload, proposal_hash, status, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("proposal-1", "principal-a", "1234567890", "{}", "hash", "executing", now.isoformat(), now.isoformat()),
        )
        self.conn.commit()
        self.executions = ExecutionRepository(self.conn)
        self.reservation = ExecutionReservation(
            proposal_id="proposal-1", principal_id="principal-a", customer_id="1234567890",
            proposal_hash="hash", idempotency_key="idem-1", reserved_at=now,
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _execute(self, adapter, audit=None):
        return execute_reserved_mutation(
            self.reservation, payload={"type": "budget"}, before_json="{}", after_json="{}",
            actor="principal-a", correlation_id="corr-1", executions=self.executions,
            audit=audit or AuditRepository(self.conn), adapter=adapter,
        )

    def test_audit_failure_prevents_mutation(self) -> None:
        adapter = FakeAdapter(MutationOutcome(ExecutionStatus.APPLIED, "request-1"))
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            self._execute(adapter, FailingAudit())
        self.assertEqual(0, adapter.calls)
        row = self.conn.execute("SELECT status FROM execution").fetchone()
        self.assertEqual(ExecutionStatus.FAILED.value, row["status"])

        replay_adapter = FakeAdapter(MutationOutcome(ExecutionStatus.APPLIED, "request-2"))
        replay = self._execute(replay_adapter)
        self.assertEqual(ExecutionStatus.FAILED, replay.status)
        self.assertEqual("idempotent_replay", replay.reason_code)
        self.assertEqual(0, replay_adapter.calls)

    def test_success_records_start_and_completion_audit(self) -> None:
        adapter = FakeAdapter(MutationOutcome(ExecutionStatus.APPLIED, "request-1"))
        outcome = self._execute(adapter)
        self.assertEqual(ExecutionStatus.APPLIED, outcome.status)
        rows = self.conn.execute(
            "SELECT event_type, outcome, google_request_id, execution_id "
            "FROM audit_event ORDER BY occurred_at"
        ).fetchall()
        self.assertEqual(["execution.started", "execution.completed"], [row["event_type"] for row in rows])
        self.assertEqual("request-1", rows[-1]["google_request_id"])
        self.assertIsNotNone(rows[0]["execution_id"])
        self.assertEqual(rows[0]["execution_id"], rows[1]["execution_id"])

    def test_duplicate_request_returns_stored_result_without_second_mutation(self) -> None:
        first_adapter = FakeAdapter(MutationOutcome(ExecutionStatus.APPLIED, "request-1"))
        self._execute(first_adapter)
        replay_adapter = FakeAdapter(MutationOutcome(ExecutionStatus.APPLIED, "request-2"))

        outcome = self._execute(replay_adapter)

        self.assertEqual(ExecutionStatus.APPLIED, outcome.status)
        self.assertEqual("request-1", outcome.google_request_id)
        self.assertEqual("idempotent_replay", outcome.reason_code)
        self.assertEqual(0, replay_adapter.calls)
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0])

    def test_unexpected_adapter_error_is_unknown_and_not_retried(self) -> None:
        adapter = FakeAdapter(error=TimeoutError("ambiguous result"))
        with self.assertRaises(TimeoutError):
            self._execute(adapter)
        row = self.conn.execute("SELECT status FROM execution").fetchone()
        self.assertEqual(ExecutionStatus.UNKNOWN.value, row["status"])
        self.assertEqual(1, adapter.calls)
        events = self.conn.execute(
            "SELECT event_type, outcome, reason_code FROM audit_event ORDER BY occurred_at"
        ).fetchall()
        self.assertEqual(
            [("execution.started", "pending"), ("execution.completed", "unknown")],
            [(row["event_type"], row["outcome"]) for row in events],
        )
        self.assertEqual("adapter_exception", events[-1]["reason_code"])

    def test_adapter_and_completion_audit_failure_preserves_audit_error(self) -> None:
        adapter = FakeAdapter(error=TimeoutError("ambiguous result"))
        with self.assertRaisesRegex(RuntimeError, "completion audit unavailable") as raised:
            self._execute(adapter, FailingCompletionAudit())
        self.assertIsInstance(raised.exception.__cause__, TimeoutError)
        row = self.conn.execute("SELECT status FROM execution").fetchone()
        self.assertEqual(ExecutionStatus.UNKNOWN.value, row["status"])
        self.assertEqual(1, adapter.calls)

    def test_completion_audit_failure_marks_result_unknown(self) -> None:
        adapter = FakeAdapter(MutationOutcome(ExecutionStatus.APPLIED, "request-2"))
        with self.assertRaisesRegex(RuntimeError, "completion audit unavailable"):
            self._execute(adapter, FailingCompletionAudit())
        row = self.conn.execute("SELECT status, google_request_id FROM execution").fetchone()
        self.assertEqual(ExecutionStatus.UNKNOWN.value, row["status"])
        self.assertEqual("request-2", row["google_request_id"])

    def test_non_terminal_adapter_outcome_is_unknown_and_audited(self) -> None:
        adapter = FakeAdapter(MutationOutcome(ExecutionStatus.PENDING, "request-pending"))
        with self.assertRaisesRegex(ValueError, "terminal bir mutation sonucu"):
            self._execute(adapter)
        execution = self.conn.execute(
            "SELECT status, google_request_id FROM execution"
        ).fetchone()
        self.assertEqual(ExecutionStatus.UNKNOWN.value, execution["status"])
        self.assertEqual("request-pending", execution["google_request_id"])
        completion = self.conn.execute(
            "SELECT outcome, reason_code FROM audit_event "
            "WHERE event_type = 'execution.completed'"
        ).fetchone()
        self.assertEqual(ExecutionStatus.UNKNOWN.value, completion["outcome"])
        self.assertEqual("invalid_adapter_outcome", completion["reason_code"])


if __name__ == "__main__":
    unittest.main()
