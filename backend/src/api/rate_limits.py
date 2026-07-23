"""Bounded, scope-aware token buckets for connector fairness."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BucketPolicy:
    """Runtime-configured capacity and refill rate; values are never provider constants."""

    capacity: float
    refill_per_second: float

    def __post_init__(self) -> None:
        if self.capacity <= 0 or self.refill_per_second <= 0:
            raise ValueError("Rate limit capacity/refill pozitif olmalidir.")


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class RateLimitExceeded(RuntimeError):
    """A safe rejection carrying the minimum client wait time."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("Istek kotasi gecici olarak dolu.")
        self.retry_after_seconds = max(retry_after_seconds, 0.001)


class ScopedTokenBucketLimiter:
    """Thread-safe buckets isolated by explicit scope keys.

    Production deployments provide a process-shared implementation behind the same
    ``acquire`` contract; this implementation is deterministic for local/single-worker use.
    """

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, ...], _Bucket] = {}
        self._lock = threading.Lock()

    def acquire(self, scope: tuple[str, ...], policy: BucketPolicy, *, cost: float = 1.0) -> None:
        """Consume one scoped budget atomically or raise ``RateLimitExceeded``."""
        if not scope or cost <= 0 or cost > policy.capacity:
            raise ValueError("Rate limit scope/cost gecersiz.")
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(scope)
            if bucket is None:
                bucket = _Bucket(tokens=policy.capacity, updated_at=now)
                self._buckets[scope] = bucket
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(policy.capacity, bucket.tokens + elapsed * policy.refill_per_second)
            bucket.updated_at = now
            if bucket.tokens < cost:
                raise RateLimitExceeded((cost - bucket.tokens) / policy.refill_per_second)
            bucket.tokens -= cost
