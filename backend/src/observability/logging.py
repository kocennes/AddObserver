"""Allowlist-only JSON application logging with defense-in-depth redaction."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

_SAFE_VALUE = re.compile(r"^[A-Za-z0-9._:/-]{0,128}$")
_OUTCOMES = frozenset({"success", "failure", "denied", "unavailable", "unknown"})
_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _safe_text(value: str | None, *, fallback: str = "unknown") -> str:
    if value is None or not _SAFE_VALUE.fullmatch(value):
        return fallback
    return value


def pseudonymous_reference(value: str | None, key: bytes) -> str | None:
    """Return a stable, non-reversible reference without exposing a raw identifier."""
    if not value:
        return None
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()[:20]


class JsonEventLogger:
    """Emit a fixed-schema JSON event; arbitrary payloads are intentionally unsupported."""

    def __init__(
        self,
        logger: logging.Logger,
        *,
        service_version: str,
        environment: str,
        pseudonym_key: bytes,
    ) -> None:
        self._logger = logger
        self._version = _safe_text(service_version)
        self._environment = _safe_text(environment)
        self._key = pseudonym_key

    def emit(
        self,
        *,
        level: str,
        operation: str,
        outcome: str,
        correlation_id: str,
        reason_code: str | None = None,
        principal_id: str | None = None,
        customer_id: str | None = None,
        duration_ms: float | None = None,
        trace_id: str | None = None,
        google_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Write and return one sanitized event for deterministic testing."""
        normalized_level = level.upper() if level.upper() in _LEVELS else "INFO"
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": normalized_level,
            "service.name": "addobserver-backend",
            "service.version": self._version,
            "service.environment": self._environment,
            "correlation_id": _safe_text(correlation_id),
            "operation": _safe_text(operation),
            "outcome": outcome if outcome in _OUTCOMES else "unknown",
            "reason_code": _safe_text(reason_code, fallback="none"),
        }
        principal_ref = pseudonymous_reference(principal_id, self._key)
        customer_ref = pseudonymous_reference(customer_id, self._key)
        if principal_ref:
            event["principal_ref"] = principal_ref
        if customer_ref:
            event["customer_ref"] = customer_ref
        if duration_ms is not None:
            event["duration_ms"] = round(max(0.0, duration_ms), 3)
        if trace_id and re.fullmatch(r"[0-9a-f]{32}", trace_id):
            event["trace_id"] = trace_id
        if google_request_id:
            safe_request_id = _safe_text(google_request_id, fallback="")
            if safe_request_id:
                event["google_request_id"] = safe_request_id
        self._logger.log(
            getattr(logging, normalized_level), json.dumps(event, separators=(",", ":"))
        )
        return event
