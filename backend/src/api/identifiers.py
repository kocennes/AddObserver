"""Validation for opaque public identifiers accepted at API boundaries."""

from __future__ import annotations

import re

MAX_OPAQUE_ID_LENGTH = 128
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_opaque_id(value: str, *, field_name: str) -> str:
    """Return a bounded URL-safe identifier or raise ``ValueError``."""
    if not value or len(value) > MAX_OPAQUE_ID_LENGTH or _OPAQUE_ID_RE.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} 1-{MAX_OPAQUE_ID_LENGTH} karakterlik URL-guvenli bir kimlik olmalidir."
        )
    return value
