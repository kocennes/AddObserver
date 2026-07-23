from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.src.api.rate_limits import BucketPolicy, RateLimitExceeded, ScopedTokenBucketLimiter


class ScopedTokenBucketLimiterTests(unittest.TestCase):
    def test_scope_exhaustion_does_not_starve_another_principal(self) -> None:
        now = [0.0]
        limiter = ScopedTokenBucketLimiter(clock=lambda: now[0])
        policy = BucketPolicy(capacity=1, refill_per_second=0.5)
        limiter.acquire(("principal", "a", "customer", "1"), policy)
        with self.assertRaises(RateLimitExceeded) as raised:
            limiter.acquire(("principal", "a", "customer", "1"), policy)
        self.assertEqual(raised.exception.retry_after_seconds, 2.0)
        limiter.acquire(("principal", "b", "customer", "1"), policy)

    def test_tokens_refill_with_a_bound(self) -> None:
        now = [0.0]
        limiter = ScopedTokenBucketLimiter(clock=lambda: now[0])
        policy = BucketPolicy(capacity=2, refill_per_second=1)
        limiter.acquire(("developer-token",), policy, cost=2)
        now[0] = 1.0
        limiter.acquire(("developer-token",), policy)
        with self.assertRaises(RateLimitExceeded):
            limiter.acquire(("developer-token",), policy)

    def test_invalid_policy_and_cost_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            BucketPolicy(capacity=0, refill_per_second=1)
        limiter = ScopedTokenBucketLimiter()
        with self.assertRaises(ValueError):
            limiter.acquire((), BucketPolicy(1, 1))


if __name__ == "__main__":
    unittest.main()
