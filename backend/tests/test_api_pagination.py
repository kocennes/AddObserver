"""Tests for backend.src.api.pagination: opaque signed keyset cursors (todo.md 1.5).

Covers the safety invariants docs/API_DESIGN.md's "Pagination sozlesmesi" requires: a cursor
round-trips only for the exact context it was minted for, a tampered/forged cursor is
rejected, an expired cursor is rejected, and no internal detail distinguishes *why* a cursor
was rejected (docs/SECURITY.md -- cross-principal existence must never leak through an error
message).
"""

from __future__ import annotations

import base64
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.pagination import (
    CURSOR_TTL,
    CursorPosition,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)

_VAULT_KEY = "unit-test-vault-key-not-a-real-secret"
_OTHER_VAULT_KEY = "a-different-vault-key-also-not-real"
_NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
_POSITION = CursorPosition(after_created_at="2026-07-18T11:00:00+00:00", after_id="proposal-1")


class CursorRoundTripTests(unittest.TestCase):
    def test_round_trips_for_the_exact_context_it_was_minted_for(self) -> None:
        cursor = encode_cursor(
            _VAULT_KEY,
            principal_id="principal-a",
            customer_id="1234567890",
            status="pending_approval",
            position=_POSITION,
            now=_NOW,
        )
        decoded = decode_cursor(
            _VAULT_KEY,
            cursor,
            principal_id="principal-a",
            customer_id="1234567890",
            status="pending_approval",
            now=_NOW,
        )
        self.assertEqual(decoded, _POSITION)

    def test_round_trips_with_no_customer_filter(self) -> None:
        cursor = encode_cursor(
            _VAULT_KEY,
            principal_id="principal-a",
            customer_id=None,
            status="pending_approval",
            position=_POSITION,
            now=_NOW,
        )
        decoded = decode_cursor(
            _VAULT_KEY,
            cursor,
            principal_id="principal-a",
            customer_id=None,
            status="pending_approval",
            now=_NOW,
        )
        self.assertEqual(decoded, _POSITION)

    def test_cursor_is_not_a_raw_offset_or_plaintext_position(self) -> None:
        cursor = encode_cursor(
            _VAULT_KEY,
            principal_id="principal-a",
            customer_id=None,
            status="pending_approval",
            position=_POSITION,
            now=_NOW,
        )
        self.assertNotIn("proposal-1", cursor)
        self.assertNotIn("principal-a", cursor)
        self.assertFalse(cursor.isdigit())


class CursorRejectionTests(unittest.TestCase):
    def _mint(self, **overrides: object) -> str:
        params: dict[str, object] = {
            "principal_id": "principal-a",
            "customer_id": "1234567890",
            "status": "pending_approval",
            "position": _POSITION,
            "now": _NOW,
        }
        params.update(overrides)
        return encode_cursor(_VAULT_KEY, **params)  # type: ignore[arg-type]

    def test_rejects_cursor_minted_for_a_different_principal(self) -> None:
        cursor = self._mint(principal_id="principal-b")
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                cursor,
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_cursor_minted_for_a_different_customer_id(self) -> None:
        cursor = self._mint(customer_id="1234567890")
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                cursor,
                principal_id="principal-a",
                customer_id="9999999999",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_cursor_minted_without_a_customer_filter_when_one_is_now_requested(
        self,
    ) -> None:
        cursor = self._mint(customer_id=None)
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                cursor,
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_signature_forged_with_a_different_vault_key(self) -> None:
        cursor = encode_cursor(
            _OTHER_VAULT_KEY,
            principal_id="principal-a",
            customer_id="1234567890",
            status="pending_approval",
            position=_POSITION,
            now=_NOW,
        )
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                cursor,
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_a_single_flipped_byte_in_the_signature(self) -> None:
        cursor = self._mint()
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = bytearray(base64.urlsafe_b64decode(padded))
        raw[-1] ^= 0x01
        tampered = base64.urlsafe_b64encode(bytes(raw)).decode("ascii").rstrip("=")
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                tampered,
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_malformed_base64(self) -> None:
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                "not-valid-base64!!!",
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_empty_string(self) -> None:
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                "",
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=_NOW,
            )

    def test_rejects_expired_cursor(self) -> None:
        cursor = self._mint()
        past_expiry = _NOW + CURSOR_TTL + timedelta(seconds=1)
        with self.assertRaises(InvalidCursorError):
            decode_cursor(
                _VAULT_KEY,
                cursor,
                principal_id="principal-a",
                customer_id="1234567890",
                status="pending_approval",
                now=past_expiry,
            )

    def test_accepts_cursor_at_the_edge_of_the_ttl(self) -> None:
        cursor = self._mint()
        just_before_expiry = _NOW + CURSOR_TTL - timedelta(seconds=1)
        decoded = decode_cursor(
            _VAULT_KEY,
            cursor,
            principal_id="principal-a",
            customer_id="1234567890",
            status="pending_approval",
            now=just_before_expiry,
        )
        self.assertEqual(decoded, _POSITION)


if __name__ == "__main__":
    unittest.main()
