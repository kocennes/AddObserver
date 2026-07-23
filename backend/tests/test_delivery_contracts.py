"""Static delivery contracts usable without Docker, cloud credentials or production secrets."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


class DeliveryContractTests(unittest.TestCase):
    def test_lock_is_present_and_declares_python_311(self) -> None:
        lock = (ROOT / "backend" / "uv.lock").read_text(encoding="utf-8")
        self.assertIn('requires-python = ">=3.11"', lock)
        self.assertIn("sha256:", lock)

    def test_container_is_digest_pinned_non_root_and_has_no_secret_build_args(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"python:3\.11\.13-slim-bookworm@sha256:[0-9a-f]{64}")
        self.assertIn(" AS builder", dockerfile)
        self.assertIn(" AS runtime", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("uv sync --frozen --no-dev", dockerfile)
        self.assertNotRegex(dockerfile, r"(?i)ARG .*?(TOKEN|SECRET|PASSWORD|KEY)")

    def test_actions_are_sha_pinned_and_permissions_are_explicit(self) -> None:
        workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
        self.assertGreaterEqual(len(workflows), 3)
        for path in workflows:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIn("permissions:", text)
                for ref in re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", text):
                    self.assertRegex(ref, r"^[0-9a-f]{40}$")
                self.assertNotRegex(text, r"(?i)(client_secret|developer_token|refresh_token):")

    def test_deploy_is_manual_digest_only_and_contains_no_provider_apply(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", workflow)
        self.assertIn("image_digest", workflow)
        self.assertIn("environment: production", workflow)
        self.assertNotIn("terraform apply", workflow)
        self.assertNotIn("kubectl apply", workflow)


if __name__ == "__main__":
    unittest.main()
