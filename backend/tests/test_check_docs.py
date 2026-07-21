"""Tests for tools/check_docs.py -- deterministic documentation governance checks."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.check_docs import (
    validate_adr_metadata,
    validate_adr_references,
    validate_encoding,
    validate_repository,
    validate_review_freshness,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


DOC_HEADER = (
    "# Örnek belge\n\n"
    "**Durum:** Kabul edildi  \n"
    "**Son gözden geçirme:** 2026-07-17  \n"
    "**Sonraki gözden geçirme:** {next_review}\n\n"
)


class ValidateAdrMetadataTests(unittest.TestCase):
    """docs/decisions/*.md must carry Durum/Tarih/Sahip with canonical values."""

    def test_accepts_well_formed_adr(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "0001-example.md",
                "# ADR-0001: Örnek\n\n- Durum: Kabul edildi\n- Tarih: 2026-07-17\n- Sahip: test\n",
            )
            self.assertEqual(validate_adr_metadata(path), [])

    def test_flags_missing_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "0001-example.md", "# ADR-0001: Örnek\n\n- Durum: Kabul edildi\n"
            )
            findings = validate_adr_metadata(path)
            messages = [f.message for f in findings]
            self.assertTrue(any("Tarih" in m for m in messages))
            self.assertTrue(any("Sahip" in m for m in messages))

    def test_flags_invalid_status_value(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "0001-example.md",
                "# ADR-0001: Örnek\n\n- Durum: Taslak\n- Tarih: 2026-07-17\n- Sahip: test\n",
            )
            findings = validate_adr_metadata(path)
            self.assertTrue(any("Durum değeri geçersiz" in f.message for f in findings))

    def test_flags_non_iso_date(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(
                Path(tmp) / "0001-example.md",
                "# ADR-0001: Örnek\n\n- Durum: Kabul edildi\n- Tarih: 17.07.2026\n- Sahip: test\n",
            )
            findings = validate_adr_metadata(path)
            self.assertTrue(any("ISO YYYY-MM-DD" in f.message for f in findings))


class ValidateAdrReferencesTests(unittest.TestCase):
    """A document citing an ADR must not depend on an unaccepted decision."""

    def test_accepts_reference_to_accepted_adr(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "AUTH.md", "bkz. `docs/decisions/0001-backend-stack.md`\n")
            status = {"0001-backend-stack.md": "Kabul edildi"}
            self.assertEqual(validate_adr_references(path, status), [])

    def test_flags_reference_to_proposed_adr(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "AUTH.md", "bkz. `docs/decisions/0003-draft.md`\n")
            status = {"0003-draft.md": "Önerildi"}
            findings = validate_adr_references(path, status)
            self.assertEqual(len(findings), 1)
            self.assertIn("henüz kabul edilmemiş", findings[0].message)

    def test_ignores_unknown_adr_reference(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "AUTH.md", "bkz. `docs/decisions/0099-missing.md`\n")
            self.assertEqual(validate_adr_references(path, {}), [])


class ValidateReviewFreshnessTests(unittest.TestCase):
    """A lapsed 'Sonraki gözden geçirme' date must be reported as stale."""

    def test_future_review_date_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "SECURITY.md", DOC_HEADER.format(next_review="2026-10-17"))
            self.assertEqual(validate_review_freshness(path, date(2026, 7, 18)), [])

    def test_past_review_date_is_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "SECURITY.md", DOC_HEADER.format(next_review="2026-01-01"))
            findings = validate_review_freshness(path, date(2026, 7, 18))
            self.assertEqual(len(findings), 1)
            self.assertIn("gözden geçirme tarihi geçmiş", findings[0].message)

    def test_review_date_equal_to_today_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "SECURITY.md", DOC_HEADER.format(next_review="2026-07-18"))
            self.assertEqual(validate_review_freshness(path, date(2026, 7, 18)), [])


class ValidateEncodingTests(unittest.TestCase):
    """Mojibake left by a mis-decoded UTF-8 round trip must be flagged."""

    def test_clean_turkish_text_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "doc.md", "Güvenlik, gözden geçirme ve öneri metni.\n")
            self.assertEqual(validate_encoding(path), [])

    def test_flags_replacement_character(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / "doc.md", "bozuk kar�kter\n")
            findings = validate_encoding(path)
            self.assertEqual(len(findings), 1)

    def test_flags_mojibake_turkish_letters(self) -> None:
        with TemporaryDirectory() as tmp:
            # "güvenlik" mis-decoded through a UTF-8 -> Latin-1 round trip.
            path = _write(Path(tmp) / "doc.md", "gÃ¼venlik\n")
            findings = validate_encoding(path)
            self.assertEqual(len(findings), 1)
            self.assertIn("satır 1", findings[0].message)


class ValidateRepositoryIntegrationTests(unittest.TestCase):
    """The real repository must pass the full governance gate today."""

    def test_current_repository_has_no_findings(self) -> None:
        findings = validate_repository(ROOT, today=date(2026, 7, 18))
        self.assertEqual(findings, [], [f.render(ROOT) for f in findings])

    def test_stale_review_date_fails_far_future_today(self) -> None:
        findings = validate_repository(ROOT, today=date(2030, 1, 1))
        self.assertTrue(any("gözden geçirme tarihi geçmiş" in f.message for f in findings))


if __name__ == "__main__":
    unittest.main()
