"""Signed, context-bound cursors and payload limits for Google Ads reports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken

from .errors import AdsApiError, ErrorClass
from .reporting import ReportPage

REPORT_CURSOR_TTL = timedelta(minutes=15)
REPORT_MAX_ROWS = 100
REPORT_MAX_BYTES = 256 * 1024
_KEY_INFO = b"addobserver-google-report-cursor-v1"


@dataclass(frozen=True, slots=True)
class ReportCursorPosition:
    """Provider page plus the next row offset within that page."""

    provider_page_token: str | None
    row_offset: int


def _invalid_cursor() -> AdsApiError:
    return AdsApiError(
        error_class=ErrorClass.VALIDATION,
        code="invalid_report_cursor",
        message="Rapor devam anahtari gecersiz veya suresi dolmus.",
        request_id=None,
    )


def _cursor_cipher(vault_key: str) -> Fernet:
    derived = hmac.new(vault_key.encode(), _KEY_INFO, hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encode_report_cursor(
    vault_key: str,
    *,
    principal_id: str,
    customer_id: str,
    report_type: str,
    start_date: date,
    end_date: date,
    position: ReportCursorPosition,
    now: datetime,
) -> str:
    """Mint a short-lived cursor bound to one exact report request."""
    payload = {
        "principal_id": principal_id,
        "customer_id": customer_id,
        "report_type": report_type,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "provider_page_token": position.provider_page_token,
        "row_offset": position.row_offset,
        "issued_at": now.astimezone(UTC).isoformat(),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _cursor_cipher(vault_key).encrypt(body).decode()


def decode_report_cursor(
    vault_key: str,
    cursor: str,
    *,
    principal_id: str,
    customer_id: str,
    report_type: str,
    start_date: date,
    end_date: date,
    now: datetime,
) -> ReportCursorPosition:
    """Verify a report cursor without revealing which validation failed."""
    if not cursor or len(cursor) > 2048:
        raise _invalid_cursor()
    try:
        body = _cursor_cipher(vault_key).decrypt(cursor.encode("ascii"))
        payload = json.loads(body)
        issued_at = datetime.fromisoformat(payload["issued_at"])
        row_offset = payload["row_offset"]
        provider_token = payload["provider_page_token"]
        if (
            issued_at.tzinfo is None
            or now.astimezone(UTC) < issued_at.astimezone(UTC)
            or now.astimezone(UTC) - issued_at.astimezone(UTC) > REPORT_CURSOR_TTL
            or payload["principal_id"] != principal_id
            or payload["customer_id"] != customer_id
            or payload["report_type"] != report_type
            or payload["start_date"] != start_date.isoformat()
            or payload["end_date"] != end_date.isoformat()
            or not isinstance(row_offset, int)
            or isinstance(row_offset, bool)
            or row_offset < 0
            or (provider_token is not None and not isinstance(provider_token, str))
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, InvalidToken) as error:
        raise _invalid_cursor() from error
    return ReportCursorPosition(provider_page_token=provider_token, row_offset=row_offset)


def bound_report_page(
    page: ReportPage,
    *,
    provider_page_token: str | None,
    row_offset: int,
    mint_cursor: Callable[[str | None, int], str],
) -> dict:
    """Return a bounded MCP payload without silently dropping a provider row."""
    if row_offset > len(page.rows):
        raise _invalid_cursor()
    available = page.rows[row_offset:]
    selected: list[dict] = []
    next_cursor: str | None = None

    for row in available[:REPORT_MAX_ROWS]:
        candidate = [*selected, dict(row)]
        next_offset = row_offset + len(candidate)
        has_local_more = next_offset < len(page.rows)
        candidate_cursor = (
            mint_cursor(provider_page_token, next_offset)
            if has_local_more
            else (mint_cursor(page.next_page_token, 0) if page.next_page_token else None)
        )
        candidate_payload = _report_payload(candidate, candidate_cursor)
        if _json_size(candidate_payload) > REPORT_MAX_BYTES:
            if not selected:
                raise AdsApiError(
                    error_class=ErrorClass.VALIDATION,
                    code="report_row_too_large",
                    message="Tek bir rapor satiri guvenli response boyutu sinirini asiyor.",
                    request_id=None,
                )
            break
        selected = candidate
        next_cursor = candidate_cursor

    consumed = row_offset + len(selected)
    if consumed < len(page.rows):
        next_cursor = mint_cursor(provider_page_token, consumed)
    elif page.next_page_token:
        next_cursor = mint_cursor(page.next_page_token, 0)
    payload = _report_payload(selected, next_cursor)
    while payload["response_bytes"] != _json_size(payload):
        payload["response_bytes"] = _json_size(payload)
    return payload


def _report_payload(rows: list[dict], next_cursor: str | None) -> dict:
    return {
        "rows": rows,
        "next_page_token": next_cursor,
        "truncated": next_cursor is not None,
        "returned_row_count": len(rows),
        # Reserve the maximum-width value while selecting rows, so replacing
        # it with the exact final size can never push a payload over the cap.
        "response_bytes": REPORT_MAX_BYTES,
        "quota": {"google_requests": 1},
    }


def _json_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
