"""Signed, context-bound continuations and bounded MCP reporting pages."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

REPORT_CURSOR_TTL = timedelta(minutes=15)
MAX_REPORT_CURSOR_LENGTH = 4096
MAX_REPORT_ROWS = 500
MAX_REPORT_BYTES = 512 * 1024
_SIGNATURE_LENGTH = hashlib.sha256().digest_size
_KEY_INFO = b"addobserver-mcp-reporting-cursor-v1"


class InvalidReportCursorError(ValueError):
    """Raised for every malformed, expired, or context-mismatched continuation."""


@dataclass(frozen=True, slots=True)
class ReportCursorPosition:
    """Provider page and row offset from which a bounded response resumes."""

    provider_page_token: str | None
    row_offset: int


@dataclass(frozen=True, slots=True)
class BoundedReportRows:
    """Rows selected for one MCP response plus the next in-page offset."""

    rows: tuple[Mapping[str, Any], ...]
    next_offset: int | None
    byte_count: int


def _signing_key(vault_key: str) -> bytes:
    return hmac.new(vault_key.encode("utf-8"), _KEY_INFO, hashlib.sha256).digest()


def encode_report_cursor(
    vault_key: str,
    *,
    principal_id: str,
    customer_id: str,
    report_kind: str,
    start_date: str,
    end_date: str,
    position: ReportCursorPosition,
    now: datetime,
) -> str:
    """Mint a short-lived continuation bound to the exact report request."""
    payload = {
        "principal_id": principal_id,
        "customer_id": customer_id,
        "report_kind": report_kind,
        "start_date": start_date,
        "end_date": end_date,
        "provider_page_token": position.provider_page_token,
        "row_offset": position.row_offset,
        "issued_at": now.astimezone(UTC).isoformat(),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_signing_key(vault_key), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode("ascii").rstrip("=")


def decode_report_cursor(
    vault_key: str,
    cursor: str,
    *,
    principal_id: str,
    customer_id: str,
    report_kind: str,
    start_date: str,
    end_date: str,
    now: datetime,
) -> ReportCursorPosition:
    """Decode a continuation, collapsing every rejection to one safe error."""
    if not cursor or len(cursor) > MAX_REPORT_CURSOR_LENGTH:
        raise InvalidReportCursorError("report continuation gecersiz")
    try:
        raw = base64.urlsafe_b64decode((cursor + "=" * (-len(cursor) % 4)).encode("ascii"))
    except (ValueError, UnicodeEncodeError, binascii.Error) as error:
        raise InvalidReportCursorError("report continuation gecersiz") from error
    if len(raw) <= _SIGNATURE_LENGTH:
        raise InvalidReportCursorError("report continuation gecersiz")
    body, signature = raw[:-_SIGNATURE_LENGTH], raw[-_SIGNATURE_LENGTH:]
    expected = hmac.new(_signing_key(vault_key), body, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidReportCursorError("report continuation gecersiz")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidReportCursorError("report continuation gecersiz") from error
    if not isinstance(payload, dict) or any(
        payload.get(key) != value
        for key, value in {
            "principal_id": principal_id,
            "customer_id": customer_id,
            "report_kind": report_kind,
            "start_date": start_date,
            "end_date": end_date,
        }.items()
    ):
        raise InvalidReportCursorError("report continuation gecersiz")
    try:
        issued_at = datetime.fromisoformat(payload["issued_at"])
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidReportCursorError("report continuation gecersiz") from error
    if issued_at.tzinfo is None:
        raise InvalidReportCursorError("report continuation gecersiz")
    age = now.astimezone(UTC) - issued_at.astimezone(UTC)
    if age < timedelta(0) or age > REPORT_CURSOR_TTL:
        raise InvalidReportCursorError("report continuation gecersiz")
    provider_token = payload.get("provider_page_token")
    row_offset = payload.get("row_offset")
    if (provider_token is not None and not isinstance(provider_token, str)) or not isinstance(
        row_offset, int
    ):
        raise InvalidReportCursorError("report continuation gecersiz")
    if row_offset < 0:
        raise InvalidReportCursorError("report continuation gecersiz")
    return ReportCursorPosition(provider_page_token=provider_token, row_offset=row_offset)


def bound_report_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    offset: int,
    max_rows: int = MAX_REPORT_ROWS,
    max_bytes: int = MAX_REPORT_BYTES,
) -> BoundedReportRows:
    """Select a deterministic row slice within both count and JSON byte budgets."""
    if offset < 0 or offset > len(rows):
        raise InvalidReportCursorError("report continuation gecersiz")
    selected: list[Mapping[str, Any]] = []
    byte_count = 2  # JSON array brackets.
    for row in rows[offset:]:
        encoded_size = len(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        separator_size = 1 if selected else 0
        over_budget = byte_count + separator_size + encoded_size > max_bytes
        if selected and (len(selected) >= max_rows or over_budget):
            break
        if not selected and encoded_size + 2 > max_bytes:
            raise ValueError("single reporting row exceeds the response byte budget")
        selected.append(row)
        byte_count += separator_size + encoded_size
    next_offset = offset + len(selected)
    return BoundedReportRows(
        rows=tuple(selected),
        next_offset=next_offset if next_offset < len(rows) else None,
        byte_count=byte_count,
    )
