"""Validate the repository's binding Markdown documentation."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


METADATA_FIELDS = ("Durum", "Son gözden geçirme", "Sonraki gözden geçirme")
METADATA_PATTERN = re.compile(r"^\*\*(?P<name>[^*]+):\*\*\s*(?P<value>.+?)\s*$", re.MULTILINE)
MARKDOWN_LINK_PATTERN = re.compile(r"(?<!!)\[[^]]*]\((?P<target>[^)]+)\)")
MATRIX_DOC_PATTERN = re.compile(r"`(?P<target>(?:\.\./)?[A-Z][A-Z0-9_/-]*\.md)`")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:")


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


def validate_repository(root: Path) -> list[Finding]:
    """Run all documentation governance checks for a repository root."""
    markdown_files = sorted(root.glob("*.md")) + sorted((root / "docs").rglob("*.md"))
    findings: list[Finding] = []
    for path in documentation_files(root):
        findings.extend(validate_metadata(path))
    for path in markdown_files:
        findings.extend(validate_local_links(path))
    findings.extend(validate_documentation_matrix(root))
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
