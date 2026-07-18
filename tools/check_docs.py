"""Validate the repository's binding Markdown documentation."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import unquote


METADATA_FIELDS = ("Durum", "Son gözden geçirme", "Sonraki gözden geçirme")
METADATA_PATTERN = re.compile(r"^\*\*(?P<name>[^*]+):\*\*\s*(?P<value>.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^]]*]\((?P<target>[^)]+)\)")
MATRIX_DOC_PATTERN = re.compile(r"`(?P<target>(?:\.\./)?[A-Z][A-Z0-9_/-]*\.md)`")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")

ADR_METADATA_FIELDS = ("Durum", "Tarih", "Sahip")
ADR_METADATA_PATTERN = re.compile(
    r"^- (?P<name>Durum|Tarih|Sahip):\s*(?P<value>.+?)\s*$", re.MULTILINE
)
ADR_STATUS_VALUES = ("Önerildi", "Kabul edildi", "Geçersiz kılındı")
ADR_REFERENCE_PATTERN = re.compile(r"docs/decisions/(?P<file>\d{4}-[A-Za-z0-9_-]+\.md)")

# UTF-8 metnin yanlışlıkla Latin-1/CP1254 olarak yeniden kodlanmasıyla oluşan, Türkçe
# belgelerde en sık görülen mojibake dizileri. U+FFFD, çözümlenemeyen baytların kanıtıdır.
MOJIBAKE_MARKERS = (
    "�",
    "Ã§", "Ã‡",
    "Ã¼", "Ãœ",
    "Ã¶", "Ã–",
    "Ä±", "Ä°",
    "ÅŸ", "Åž",
    "ÄŸ", "Äž",
)


@dataclass(frozen=True)
class Finding:
    """A single actionable documentation validation failure."""

    path: Path
    message: str

    def render(self, root: Path) -> str:
        """Render the finding with a repository-relative path."""
        return f"{self.path.relative_to(root)}: {self.message}"


def documentation_files(root: Path) -> list[Path]:
    """Return binding design documents that require lifecycle metadata."""
    return sorted(path for path in (root / "docs").glob("*.md") if path.is_file())


def validate_metadata(path: Path) -> list[Finding]:
    """Check required lifecycle metadata and ISO-formatted review dates."""
    text = path.read_text(encoding="utf-8")
    metadata = {match["name"]: match["value"] for match in METADATA_PATTERN.finditer(text)}
    findings = [
        Finding(path, f"eksik metadata alanı: {field}")
        for field in METADATA_FIELDS
        if field not in metadata
    ]

    status = metadata.get("Durum", "")
    if status and not (status.startswith("Kabul edildi") or status.startswith("Taslak")):
        findings.append(Finding(path, "Durum 'Kabul edildi' veya 'Taslak' ile başlamalı"))

    for field in METADATA_FIELDS[1:]:
        value = metadata.get(field)
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            findings.append(Finding(path, f"{field} ISO YYYY-MM-DD biçiminde olmalı"))
    return findings


def _local_target(raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split(maxsplit=1)[0]
    if not target or target.startswith("#") or target.lower().startswith(EXTERNAL_SCHEMES):
        return None
    return unquote(target.split("#", 1)[0])


def validate_local_links(path: Path) -> list[Finding]:
    """Check that relative file links in a Markdown document resolve."""
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    for match in MARKDOWN_LINK_PATTERN.finditer(text):
        target = _local_target(match["target"])
        if target and not (path.parent / target).resolve().exists():
            findings.append(Finding(path, f"bozuk yerel bağlantı: {target}"))
    return findings


def validate_documentation_matrix(root: Path) -> list[Finding]:
    """Check that every Markdown file named by the documentation matrix exists."""
    path = root / "docs" / "DOCUMENTATION.md"
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    for match in MATRIX_DOC_PATTERN.finditer(text):
        target = match["target"]
        if not (path.parent / target).resolve().is_file():
            findings.append(Finding(path, f"matriste bulunamayan belge: {target}"))
    return findings


def validate_review_freshness(path: Path, today: date) -> list[Finding]:
    """Check that a document's next scheduled review date has not lapsed."""
    text = path.read_text(encoding="utf-8")
    metadata = {match["name"]: match["value"] for match in METADATA_PATTERN.finditer(text)}
    value = metadata.get("Sonraki gözden geçirme")
    if not value or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return []
    try:
        next_review = date.fromisoformat(value)
    except ValueError:
        return []
    if next_review < today:
        return [
            Finding(path, f"gözden geçirme tarihi geçmiş: {value} (bugün: {today.isoformat()})")
        ]
    return []


def validate_encoding(path: Path) -> list[Finding]:
    """Flag Turkish/English mojibake left by a mis-decoded UTF-8 round trip."""
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    for marker in MOJIBAKE_MARKERS:
        index = text.find(marker)
        if index == -1:
            continue
        line = text.count("\n", 0, index) + 1
        findings.append(Finding(path, f"olası bozuk karakter kodlaması ({marker!r}), satır {line}"))
    return findings


def adr_files(root: Path) -> list[Path]:
    """Return architecture decision records that require ADR lifecycle metadata."""
    decisions_dir = root / "docs" / "decisions"
    return sorted(
        path for path in decisions_dir.glob("*.md") if path.is_file() and path.name != "README.md"
    )


def _adr_metadata(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    metadata: dict[str, str] = {}
    for match in ADR_METADATA_PATTERN.finditer(text):
        metadata.setdefault(match["name"], match["value"])
    return metadata


def validate_adr_metadata(path: Path) -> list[Finding]:
    """Check required ADR fields and that Durum/Tarih use canonical values."""
    metadata = _adr_metadata(path)
    findings = [
        Finding(path, f"eksik ADR metadata alanı: {field}")
        for field in ADR_METADATA_FIELDS
        if field not in metadata
    ]
    status = metadata.get("Durum")
    if status and status not in ADR_STATUS_VALUES:
        findings.append(
            Finding(
                path,
                f"ADR Durum değeri geçersiz: {status!r} (beklenen: {', '.join(ADR_STATUS_VALUES)})",
            )
        )
    date_value = metadata.get("Tarih")
    if date_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
        findings.append(Finding(path, "Tarih ISO YYYY-MM-DD biçiminde olmalı"))
    return findings


def validate_adr_references(path: Path, adr_status: dict[str, str]) -> list[Finding]:
    """Check that a document does not cite an ADR whose decision isn't accepted."""
    text = path.read_text(encoding="utf-8")
    findings: list[Finding] = []
    seen: set[str] = set()
    for match in ADR_REFERENCE_PATTERN.finditer(text):
        adr_name = match["file"]
        if adr_name in seen or adr_name not in adr_status:
            continue
        seen.add(adr_name)
        status = adr_status[adr_name]
        if status != "Kabul edildi":
            findings.append(
                Finding(path, f"henüz kabul edilmemiş ADR'a referans veriyor: {adr_name} (Durum: {status})")
            )
    return findings


def validate_repository(root: Path, today: date | None = None) -> list[Finding]:
    """Run all documentation governance checks for a repository root."""
    today = today or date.today()
    markdown_files = sorted(root.glob("*.md")) + sorted((root / "docs").rglob("*.md"))
    findings: list[Finding] = []
    for path in documentation_files(root):
        findings.extend(validate_metadata(path))
        findings.extend(validate_review_freshness(path, today))
    for path in markdown_files:
        findings.extend(validate_local_links(path))
        findings.extend(validate_encoding(path))
    findings.extend(validate_documentation_matrix(root))
    for path in adr_files(root):
        findings.extend(validate_adr_metadata(path))
    adr_status = {path.name: _adr_metadata(path).get("Durum", "") for path in adr_files(root)}
    for path in markdown_files:
        findings.extend(validate_adr_references(path, adr_status))
    return findings


def main(argv: list[str] | None = None) -> int:
    """Run checks and return a shell-friendly status code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    findings = validate_repository(root)
    if findings:
        for finding in findings:
            print(finding.render(root), file=sys.stderr)
        return 1
    print(f"Dokümantasyon kapısı başarılı: {len(documentation_files(root))} belge doğrulandı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
