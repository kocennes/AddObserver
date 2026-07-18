"""Unit tests for public opaque identifier validation."""

from __future__ import annotations

import unittest

from backend.src.api.identifiers import MAX_OPAQUE_ID_LENGTH, validate_opaque_id


class OpaqueIdentifierTests(unittest.TestCase):
    def test_accepts_uuid_ulid_and_existing_url_safe_ids(self) -> None:
        values = (
            "550e8400-e29b-41d4-a716-446655440000",
            "01J2QX9Q5J8M4F3B8W7K6P2N1C",
            "proposal_1",
        )
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(validate_opaque_id(value, field_name="proposal_id"), value)

    def test_rejects_empty_oversized_and_non_url_safe_values(self) -> None:
        values = (
            "",
            "a" * (MAX_OPAQUE_ID_LENGTH + 1),
            "contains space",
            "../proposal",
            "line\nbreak",
        )
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_opaque_id(value, field_name="proposal_id")


if __name__ == "__main__":
    unittest.main()
