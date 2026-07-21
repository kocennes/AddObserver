"""Contract tests for the durable credential-revocation worker core."""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.auth.revocation_worker import process_one_revocation  # noqa: E402


class FakeRevocations:
    def __init__(self, events: list[str], job):  # noqa: ANN001
        self.events = events
        self.job = job

    def claim_due(self, principal_id: str, *, now: datetime, lease_until: datetime):  # noqa: ANN201
        self.events.append(f"claim:{principal_id}")
        job, self.job = self.job, None
        return job

    def retry(
        self,
        principal_id: str,
        job_id: str,
        *,
        claimed_attempt: int,
        error_code: str,
        next_attempt_at: datetime,
    ) -> bool:
        self.events.append(f"retry:{principal_id}:{job_id}:{claimed_attempt}:{error_code}")
        return True

    def complete(
        self,
        principal_id: str,
        job_id: str,
        *,
        claimed_attempt: int,
        completed_at: datetime,
    ) -> bool:
        self.events.append(f"complete:{principal_id}:{job_id}:{claimed_attempt}")
        return True


class FakeWork:
    def __init__(self, events: list[str], revocations: FakeRevocations, number: int):
        self.events = events
        self.number = number
        self.repositories = SimpleNamespace(credential_revocations=revocations)

    def __enter__(self):  # noqa: ANN204
        self.events.append(f"work{self.number}.enter")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
        self.events.append(f"work{self.number}.exit")

    def bind_principal(self, principal_id: str) -> None:
        self.events.append(f"work{self.number}.bind:{principal_id}")


class FakeFactory:
    def __init__(self, events: list[str], revocations: FakeRevocations):
        self.events = events
        self.revocations = revocations
        self.count = 0

    def request(self) -> FakeWork:
        self.count += 1
        return FakeWork(self.events, self.revocations, self.count)


class RevocationWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events: list[str] = []
        self.now = datetime(2026, 7, 19, 18, tzinfo=UTC)
        self.principal_id = "753e587a-bcad-46c5-9ed0-169a051adb7b"

    def _job(self):  # noqa: ANN202
        return SimpleNamespace(id="job-id", vault_ref="vault-ref", attempts=2)

    def test_success_calls_vault_between_claim_and_completion_transactions(self) -> None:
        revocations = FakeRevocations(self.events, self._job())

        class Vault:
            def revoke(test_self, vault_ref: str) -> None:
                self.events.append(f"vault.revoke:{vault_ref}")

        result = process_one_revocation(
            self.principal_id,
            uow_factory=FakeFactory(self.events, revocations),  # pyright: ignore[reportArgumentType]
            vault=Vault(),  # pyright: ignore[reportArgumentType]
            now=self.now,
        )

        self.assertTrue(result.completed)
        self.assertEqual(
            self.events,
            [
                "work1.enter",
                f"work1.bind:{self.principal_id}",
                f"claim:{self.principal_id}",
                "work1.exit",
                "vault.revoke:vault-ref",
                "work2.enter",
                f"work2.bind:{self.principal_id}",
                f"complete:{self.principal_id}:job-id:2",
                "work2.exit",
            ],
        )

    def test_provider_failure_is_sanitized_and_persisted_after_claim_commit(self) -> None:
        revocations = FakeRevocations(self.events, self._job())

        class Vault:
            def revoke(test_self, vault_ref: str) -> None:
                self.events.append("vault.failed-with-sensitive-provider-text")
                raise RuntimeError("provider token=secret")

        result = process_one_revocation(
            self.principal_id,
            uow_factory=FakeFactory(self.events, revocations),  # pyright: ignore[reportArgumentType]
            vault=Vault(),  # pyright: ignore[reportArgumentType]
            now=self.now,
        )

        self.assertTrue(result.processed)
        self.assertFalse(result.completed)
        self.assertIn(f"retry:{self.principal_id}:job-id:2:VAULT_UNAVAILABLE", self.events)
        self.assertNotIn("provider token=secret", " ".join(self.events))

    def test_no_due_job_avoids_vault_and_second_transaction(self) -> None:
        revocations = FakeRevocations(self.events, None)

        class Vault:
            def revoke(self, vault_ref: str) -> None:
                raise AssertionError("vault must not be called without a claimed job")

        result = process_one_revocation(
            self.principal_id,
            uow_factory=FakeFactory(self.events, revocations),  # pyright: ignore[reportArgumentType]
            vault=Vault(),  # pyright: ignore[reportArgumentType]
            now=self.now,
        )

        self.assertFalse(result.processed)
        self.assertEqual(self.events[-1], "work1.exit")
        self.assertEqual(self.events.count("work1.enter"), 1)


if __name__ == "__main__":
    unittest.main()
