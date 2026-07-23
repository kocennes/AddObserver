"""Contract tests for Phase 11 legal and Google submission evidence."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Phase11DocumentationTests(unittest.TestCase):
    """Prevent legal unknowns from silently becoming production assertions."""

    def _read(self, name: str) -> str:
        """Read one repository document as UTF-8."""
        return (ROOT / "docs" / name).read_text(encoding="utf-8")

    def test_data_inventory_covers_every_required_category(self) -> None:
        """The production inventory covers the Phase 11.2 minimum categories."""
        inventory = self._read("PRODUCTION_DATA_INVENTORY.md")
        for category in (
            "Ads account mapping", "OAuth grant/session metadata", "Google credential",
            "Uygulama logu/metric/trace", "Audit", "Support talebi", "Backup",
        ):
            with self.subTest(category=category):
                self.assertIn(category, inventory)

    def test_unknown_providers_are_not_presented_as_selected(self) -> None:
        """A proposed cloud must not be presented as an actual subprocessor."""
        subprocessors = self._read("SUBPROCESSORS.md")
        self.assertIn("Hayali sağlayıcı yayımlanmaz", subprocessors)
        self.assertIn("ADR-0008 önerildi; sağlayıcı değildir", subprocessors)

    def test_external_submissions_require_explicit_owner_approval(self) -> None:
        """Google submission material remains non-executing until owner approval."""
        evidence = self._read("GOOGLE_SUBMISSION_EVIDENCE.md")
        self.assertIn("ürün sahibinin açık onayı zorunlu", evidence)
        self.assertIn("Gönderilmedi", evidence)
        self.assertIn("hiçbir satır `N/A` veya compliant sayılmaz", evidence)

    def test_legal_runbook_does_not_invent_notification_deadlines(self) -> None:
        """Incident deadlines must come from counsel's jurisdiction matrix."""
        runbook = self._read("LEGAL_OPERATIONS_RUNBOOK.md")
        self.assertIn("varsayımsal süre yazılmaz", runbook)
        self.assertIn("matrisinden hesaplanır", runbook)

    def test_public_policies_remain_draft_while_inputs_are_unknown(self) -> None:
        """Preparatory work cannot make privacy or terms publishable."""
        for filename in ("PRIVACY_POLICY.md", "TERMS.md"):
            policy = (ROOT / filename).read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                self.assertIn("DRAFT", policy)
                self.assertIn("[TBD", policy)


if __name__ == "__main__":
    unittest.main()
