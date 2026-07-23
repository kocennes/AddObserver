from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.src.app import create_app
from test_app_lifecycle import _settings


class PublicOpenApiContractTests(unittest.TestCase):
    """Breaking-change gate for the deliberately small public JSON API."""

    def test_public_v1_paths_and_methods_are_an_exact_snapshot(self) -> None:
        app = create_app(settings=_settings())
        paths = app.openapi()["paths"]
        public = {
            path: sorted(
                method for method in item if method in {"get", "post", "put", "patch", "delete"}
            )
            for path, item in paths.items()
            if path.startswith("/api/v1/")
        }
        self.assertEqual(
            public,
            {
                "/api/v1/accounts": ["get"],
                "/api/v1/proposals": ["get"],
                "/api/v1/proposals/{proposal_id}": ["get"],
            },
        )

    def test_non_json_surfaces_are_not_part_of_public_v1_contract(self) -> None:
        public_paths = {
            path
            for path in create_app(settings=_settings()).openapi()["paths"]
            if path.startswith("/api/v1/")
        }
        self.assertNotIn("/mcp", public_paths)
        self.assertTrue(
            all("callback" not in path and "approvals" not in path for path in public_paths)
        )


if __name__ == "__main__":
    unittest.main()
