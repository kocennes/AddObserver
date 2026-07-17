"""Central retry policy for Google Ads reads (docs/ERROR_HANDLING.md -- "Karar").

"Retry policy merkezi adapter'dadir; cagiran katman kendi dongusunu
ekleyemez." -- this is the one place a retry loop is allowed to exist.
Callers pass a zero-argument operation and a classifier; everything else
(max attempts, elapsed-time budget, full-jitter backoff, honouring Google's
``retry_delay`` as a floor) lives here.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from .errors import AdsApiError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounds a retry loop by both attempt count and total elapsed time.

    ``base_delay``/``max_delay`` seed a full-jitter exponential backoff
    (``random.uniform(0, min(max_delay, base_delay * 2**attempt))``);
    Google's own ``retry_delay`` (when present) is applied as a lower bound,
    never a ceiling, per ERROR_HANDLING.md.
    """

    max_attempts: int = 4
    max_elapsed_seconds: float = 30.0
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts en az 1 olmalidir.")
        if self.max_elapsed_seconds <= 0:
            raise ValueError("max_elapsed_seconds pozitif olmalidir.")
        if self.base_delay_seconds <= 0 or self.max_delay_seconds <= 0:
            raise ValueError("base_delay_seconds ve max_delay_seconds pozitif olmalidir.")


def _backoff_seconds(policy: RetryPolicy, attempt: int, retry_delay_floor: float | None, rng: random.Random) -> float:
    ceiling = min(policy.max_delay_seconds, policy.base_delay_seconds * (2**attempt))
    jittered = rng.uniform(0, ceiling)
    if retry_delay_floor is not None:
        return max(jittered, retry_delay_floor)
    return jittered


def execute_with_retry(
    operation: Callable[[], T],
    *,
    classify: Callable[[Exception], AdsApiError],
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    rng: random.Random | None = None,
) -> T:
    """Run ``operation``, retrying only classes ERROR_HANDLING.md marks retryable.

    ``classify`` turns any exception ``operation`` raises into an
    ``AdsApiError``; non-retryable classes (validation, auth, sync/stale) and
    exhausted attempt/elapsed budgets re-raise that ``AdsApiError`` immediately
    -- there is no blind retry of an ambiguous or non-idempotent failure.
    """
    generator = rng or random.Random()
    started_at = monotonic()
    last_error: AdsApiError | None = None

    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 -- reclassified into AdsApiError below
            last_error = classify(exc)

        is_last_attempt = attempt == policy.max_attempts - 1
        elapsed = monotonic() - started_at
        if not last_error.retryable or is_last_attempt or elapsed >= policy.max_elapsed_seconds:
            raise last_error

        delay = _backoff_seconds(policy, attempt, last_error.retry_delay_seconds, generator)
        remaining_budget = policy.max_elapsed_seconds - elapsed
        sleep(min(delay, max(remaining_budget, 0.0)))

    assert last_error is not None  # pragma: no cover -- loop always sets or returns
    raise last_error
