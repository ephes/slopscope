"""Small cloc integration surface for language summaries."""

from __future__ import annotations

import csv
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from slopscope.report import LanguageRow


@dataclass(frozen=True)
class ClocResult:
    """Completed cloc process data used by the CLI."""

    returncode: int
    stdout: str
    stderr: str


def is_cloc_available(executable: str = "cloc") -> bool:
    """Return whether the configured cloc executable can be found."""

    return shutil.which(executable) is not None


def build_language_summary_command(path: Path | str, executable: str = "cloc") -> list[str]:
    """Build the cloc command used for the first language-summary slice."""

    return [executable, str(path), "--vcs=git", "--csv", "--quiet"]


def run_command(command: Sequence[str]) -> ClocResult:
    """Run a command and capture text output."""

    completed = subprocess.run(command, capture_output=True, check=False, text=True)
    return ClocResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_language_summary(path: Path | str, executable: str = "cloc") -> ClocResult:
    """Run cloc for a language summary at the selected path."""

    return run_command(build_language_summary_command(path, executable=executable))


def parse_language_summary_csv(output: str) -> list[LanguageRow]:
    """Parse cloc CSV language summary output, skipping malformed rows."""

    reader = csv.DictReader(output.splitlines())
    rows: list[LanguageRow] = []
    for row in reader:
        parsed = _parse_language_row(row)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _parse_language_row(row: dict[str, str | None]) -> LanguageRow | None:
    language = _clean(row.get("language"))
    if not language:
        return None

    try:
        return LanguageRow(
            language=language,
            # cloc summary CSV uses "filename" as the file-count column name.
            files=_parse_int(row.get("filename")),
            blank=_parse_int(row.get("blank")),
            comment=_parse_int(row.get("comment")),
            code=_parse_int(row.get("code")),
        )
    except ValueError:
        return None


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _parse_int(value: str | None) -> int:
    cleaned = _clean(value)
    if not cleaned:
        raise ValueError("missing integer")
    return int(cleaned)
