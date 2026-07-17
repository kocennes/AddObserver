"""Tests for the repository documentation quality gate."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.check_docs import validate_local_links, validate_metadata, validate_repository


class DocumentationGateTests(unittest.TestCase):
    """Keep binding documentation machine-checkable."""

    def test_repository_documentation_passes(self) -> None:
        """The checked-in documentation obeys its governance contract."""
        self.assertEqual([], validate_repository(ROOT))

    def test_missing_metadata_is_reported(self) -> None:
        """A design document without lifecycle fields is rejected."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BROKEN.md"
            path.write_text("# Eksik belge\n\n**Durum:** Taslak\n", encoding="utf-8")

            messages = [finding.message for finding in validate_metadata(path)]

        self.assertIn("eksik metadata alanı: Son gözden geçirme", messages)
        self.assertIn("eksik metadata alanı: Sonraki gözden geçirme", messages)

    def test_broken_local_link_is_reported(self) -> None:
        """A relative Markdown link must resolve from its containing file."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BROKEN.md"
            path.write_text("[olmayan](missing.md)\n", encoding="utf-8")

            findings = validate_local_links(path)

        self.assertEqual(["bozuk yerel bağlantı: missing.md"], [item.message for item in findings])


if __name__ == "__main__":
    unittest.main()
