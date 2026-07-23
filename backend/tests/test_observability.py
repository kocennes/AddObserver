"""Security and contract tests for application logs and telemetry."""

from __future__ import annotations

import io
import json
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.observability.logging import JsonEventLogger, pseudonymous_reference
from backend.src.observability.telemetry import Telemetry


class JsonEventLoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.logger = logging.getLogger(f"observability-test-{id(self)}")
        self.logger.handlers = [logging.StreamHandler(self.stream)]
        self.logger.propagate = False
        self.logger.setLevel(logging.DEBUG)
        self.events = JsonEventLogger(
            self.logger,
            service_version="0.1.0",
            environment="test",
            pseudonym_key=b"k" * 32,
        )

    def test_fixed_schema_pseudonymizes_identifiers_and_never_accepts_payload(self) -> None:
        event = self.events.emit(
            level="INFO",
            operation="google_report",
            outcome="success",
            reason_code="ok",
            correlation_id="corr-1",
            principal_id="principal-secret-id",
            customer_id="1234567890",
        )
        encoded = self.stream.getvalue()
        self.assertNotIn("principal-secret-id", encoded)
        self.assertNotIn("1234567890", encoded)
        self.assertEqual(
            event["principal_ref"], pseudonymous_reference("principal-secret-id", b"k" * 32)
        )
        self.assertEqual(json.loads(encoded)["correlation_id"], "corr-1")

    def test_control_characters_and_sensitive_free_text_are_replaced(self) -> None:
        secret = "Bearer secret-token\r\nFORGED"
        event = self.events.emit(
            level="INFO\nCRITICAL",
            operation=secret,
            outcome=secret,
            reason_code="cookie=session-secret\n",
            correlation_id="corr\r\nforged",
        )
        encoded = self.stream.getvalue()
        self.assertNotIn("secret-token", encoded)
        self.assertNotIn("session-secret", encoded)
        self.assertEqual(event["operation"], "unknown")
        self.assertEqual(event["correlation_id"], "unknown")
        self.assertEqual(len(encoded.splitlines()), 1)

    def test_google_request_id_is_carried_when_present_and_safe(self) -> None:
        event = self.events.emit(
            level="ERROR",
            operation="google_ads_campaign_report",
            outcome="failure",
            reason_code="quota_error.resource_exhausted",
            correlation_id="corr-2",
            google_request_id="AbCd-1234_efGH",
        )
        encoded = self.stream.getvalue()
        self.assertEqual(json.loads(encoded)["google_request_id"], "AbCd-1234_efGH")
        self.assertEqual(event["google_request_id"], "AbCd-1234_efGH")

    def test_google_request_id_is_omitted_when_absent_or_unsafe(self) -> None:
        event = self.events.emit(
            level="ERROR",
            operation="google_ads_campaign_report",
            outcome="failure",
            reason_code="transport.unavailable",
            correlation_id="corr-3",
        )
        self.assertNotIn("google_request_id", event)
        self.assertNotIn("google_request_id", self.stream.getvalue())

        self.stream.truncate(0)
        self.stream.seek(0)
        unsafe_event = self.events.emit(
            level="ERROR",
            operation="google_ads_campaign_report",
            outcome="failure",
            reason_code="transport.unavailable",
            correlation_id="corr-4",
            google_request_id="req\r\nid<script>",
        )
        self.assertNotIn("google_request_id", unsafe_event)
        self.assertNotIn("req", self.stream.getvalue())
        self.assertNotIn("script", self.stream.getvalue())


class TelemetryTests(unittest.TestCase):
    def test_operation_rejects_high_cardinality_dimensions(self) -> None:
        telemetry = Telemetry()
        with telemetry.operation("customer-1234567890", "/api/users/secret-id"):
            pass

    def test_operation_preserves_exception(self) -> None:
        telemetry = Telemetry()
        with (
            self.assertRaisesRegex(RuntimeError, "provider failed"),
            telemetry.operation("google", "search"),
        ):
            raise RuntimeError("provider failed")


if __name__ == "__main__":
    unittest.main()
