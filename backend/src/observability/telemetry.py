"""Low-cardinality OpenTelemetry instruments with no exporter dependency."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter

from opentelemetry import metrics, trace
from opentelemetry.trace import SpanKind

_BOUNDARIES = frozenset({"http", "mcp", "auth", "google", "db", "worker"})
_OUTCOMES = frozenset({"success", "failure", "denied", "unavailable", "unknown"})


class Telemetry:
    """Own connector instruments while leaving provider/export configuration to deployment."""

    def __init__(self) -> None:
        meter = metrics.get_meter("addobserver.backend")
        self._tracer = trace.get_tracer("addobserver.backend")
        self.request_count = meter.create_counter("addobserver.request.count")
        self.request_duration = meter.create_histogram("addobserver.request.duration", unit="ms")
        self.auth_failures = meter.create_counter("addobserver.auth.failure.count")
        self.google_errors = meter.create_counter("addobserver.google.error.count")
        self.quota_events = meter.create_counter("addobserver.google.quota.count")
        self.audit_failures = meter.create_counter("addobserver.audit.failure.count")
        self.execution_outcomes = meter.create_counter("addobserver.execution.outcome.count")
        self.principal_mismatches = meter.create_counter("addobserver.principal_mismatch.count")
        self.queue_depth = meter.create_up_down_counter("addobserver.queue.depth")
        self.approval_age = meter.create_histogram("addobserver.approval.age", unit="s")

    @contextmanager
    def operation(self, boundary: str, operation: str) -> Iterator[None]:
        """Trace and measure one boundary operation using allowlisted dimensions only."""
        safe_boundary = boundary if boundary in _BOUNDARIES else "worker"
        safe_operation = operation if operation.replace("_", "").isalnum() else "unknown"
        started = perf_counter()
        outcome = "success"
        with self._tracer.start_as_current_span(
            f"{safe_boundary}.{safe_operation}", kind=SpanKind.INTERNAL
        ) as span:
            span.set_attribute("addobserver.boundary", safe_boundary)
            span.set_attribute("addobserver.operation", safe_operation)
            try:
                yield
            except Exception:
                outcome = "failure"
                span.set_attribute("addobserver.outcome", outcome)
                raise
            finally:
                attributes = {
                    "boundary": safe_boundary,
                    "operation": safe_operation,
                    "outcome": outcome if outcome in _OUTCOMES else "unknown",
                }
                self.request_count.add(1, attributes)
                self.request_duration.record((perf_counter() - started) * 1000, attributes)
