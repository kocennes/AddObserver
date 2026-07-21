"""Tests for the public HTTP problem+json helper."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.src.api.problems import PROBLEM_JSON, problem_body, problem_response


class ProblemResponseTests(unittest.TestCase):
    def test_problem_body_includes_stable_fields_and_optional_correlation_id(self) -> None:
        body = problem_body(
            status_code=400,
            title="Bad request",
            detail="Safe detail.",
            code="bad_request",
            correlation_id="corr-1",
        )

        self.assertEqual(
            body,
            {
                "type": "about:blank",
                "title": "Bad request",
                "status": 400,
                "detail": "Safe detail.",
                "code": "bad_request",
                "correlation_id": "corr-1",
            },
        )

    def test_problem_response_sets_media_type_and_headers(self) -> None:
        response = problem_response(
            status_code=401,
            title="Authentication required",
            detail="Authentication required.",
            code="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.media_type, PROBLEM_JSON)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")


if __name__ == "__main__":
    unittest.main()
