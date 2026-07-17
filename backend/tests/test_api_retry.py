"""ERROR_HANDLING.md retry-policy tests: budget, jitter and non-retryable classes."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.errors import AdsApiError, ErrorClass
from backend.src.api.retry import RetryPolicy, execute_with_retry


class _FakeClock:
    """Deterministic monotonic clock/sleep pair -- no real waiting in tests."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _rate_limit_error(retry_delay: float | None = None) -> AdsApiError:
    return AdsApiError(
        error_class=ErrorClass.RATE_LIMIT,
        code="quota_error.resource_exhausted",
        message="quota asildi",
        request_id="req-1",
        retry_delay_seconds=retry_delay,
    )


def _validation_error() -> AdsApiError:
    return AdsApiError(
        error_class=ErrorClass.VALIDATION,
        code="request_error.invalid_page_size",
        message="gecersiz",
        request_id="req-2",
    )


class ExecuteWithRetryTests(unittest.TestCase):
    def test_succeeds_without_retry_when_operation_succeeds_first_try(self) -> None:
        clock = _FakeClock()
        result = execute_with_retry(
            lambda: "ok",
            classify=lambda exc: _validation_error(),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(clock.sleeps, [])

    def test_retries_retryable_class_then_succeeds(self) -> None:
        clock = _FakeClock()
        attempts = {"count": 0}

        def operation() -> str:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient boom")
            return "ok"

        result = execute_with_retry(
            operation,
            classify=lambda exc: _rate_limit_error(),
            policy=RetryPolicy(max_attempts=5, max_elapsed_seconds=60),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            rng=random.Random(0),
        )
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["count"], 3)
        self.assertEqual(len(clock.sleeps), 2)

    def test_non_retryable_class_raises_immediately_without_sleeping(self) -> None:
        clock = _FakeClock()
        attempts = {"count": 0}

        def operation() -> str:
            attempts["count"] += 1
            raise RuntimeError("policy violation")

        with self.assertRaises(AdsApiError) as ctx:
            execute_with_retry(
                operation,
                classify=lambda exc: _validation_error(),
                policy=RetryPolicy(max_attempts=5),
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )
        self.assertEqual(ctx.exception.error_class, ErrorClass.VALIDATION)
        self.assertEqual(attempts["count"], 1)
        self.assertEqual(clock.sleeps, [])

    def test_exhausting_max_attempts_raises_last_classified_error(self) -> None:
        clock = _FakeClock()

        def operation() -> str:
            raise RuntimeError("always fails")

        with self.assertRaises(AdsApiError) as ctx:
            execute_with_retry(
                operation,
                classify=lambda exc: _rate_limit_error(),
                policy=RetryPolicy(max_attempts=3, max_elapsed_seconds=60),
                sleep=clock.sleep,
                monotonic=clock.monotonic,
                rng=random.Random(0),
            )
        self.assertEqual(ctx.exception.error_class, ErrorClass.RATE_LIMIT)
        self.assertEqual(len(clock.sleeps), 2)

    def test_elapsed_time_budget_stops_retrying_even_with_attempts_left(self) -> None:
        clock = _FakeClock()

        def operation() -> str:
            clock.now += 100.0
            raise RuntimeError("slow and always fails")

        with self.assertRaises(AdsApiError):
            execute_with_retry(
                operation,
                classify=lambda exc: _rate_limit_error(),
                policy=RetryPolicy(max_attempts=10, max_elapsed_seconds=1.0),
                sleep=clock.sleep,
                monotonic=clock.monotonic,
            )
        self.assertEqual(clock.sleeps, [])

    def test_googles_retry_delay_is_applied_as_a_floor_not_a_ceiling(self) -> None:
        clock = _FakeClock()
        attempts = {"count": 0}

        def operation() -> str:
            attempts["count"] += 1
            if attempts["count"] < 2:
                raise RuntimeError("quota")
            return "ok"

        execute_with_retry(
            operation,
            classify=lambda exc: _rate_limit_error(retry_delay=9.0),
            policy=RetryPolicy(max_attempts=3, max_elapsed_seconds=60, base_delay_seconds=0.1, max_delay_seconds=1.0),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            rng=random.Random(0),
        )
        self.assertEqual(clock.sleeps, [9.0])


if __name__ == "__main__":
    unittest.main()
