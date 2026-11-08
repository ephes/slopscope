"""Small cloc integration surface for language summaries."""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from slopscope._capture import CaptureError, capture_process
from slopscope.report import FileRow, LanguageRow

CLOC_TIMEOUT_SECONDS = 30
MAX_CLOC_OUTPUT_BYTES = 16 * 1024 * 1024


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


def build_file_summary_command(path: Path | str, executable: str = "cloc") -> list[str]:
    """Build the cloc command used for file-level count rows."""

    return [executable, str(path), "--vcs=git", "--by-file", "--csv", "--quiet"]


def run_command(command: Sequence[str]) -> ClocResult:
    """Run cloc with a finite deadline and normalize execution failures."""

    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return ClocResult(2, "", f"slopscope: could not run cloc: {exc}")
    try:
        result = capture_process(
            process, timeout=CLOC_TIMEOUT_SECONDS, max_bytes=MAX_CLOC_OUTPUT_BYTES, label="cloc"
        )
    except subprocess.TimeoutExpired:
        return ClocResult(2, "", f"slopscope: cloc timed out after {CLOC_TIMEOUT_SECONDS} seconds")
    except CaptureError as exc:
        return ClocResult(2, "", f"slopscope: {exc}")
    # Match Popen(text=True): use the locale encoding and normalize universal newlines.
    encoding = io.text_encoding(None)
    try:
        stdout = io.TextIOWrapper(io.BytesIO(result.stdout), encoding=encoding).read()
        stderr = io.TextIOWrapper(io.BytesIO(result.stderr), encoding=encoding).read()
    except UnicodeDecodeError:
        return ClocResult(2, "", "slopscope: could not decode cloc output with the locale encoding")
    return ClocResult(result.returncode, stdout, stderr)


def run_language_summary(path: Path | str, executable: str = "cloc") -> ClocResult:
    """Run cloc for a language summary at the selected path."""

    return run_command(build_language_summary_command(path, executable=executable))


def run_file_summary(path: Path | str, executable: str = "cloc") -> ClocResult:
    """Run cloc for file-level count rows at the selected path."""

    return run_command(build_file_summary_command(path, executable=executable))


class ClocParseError(ValueError):
    """A successful cloc process returned an unusable CSV report."""


def _csv_rows(output: str) -> list[dict[str, str | None]]:
    reader = csv.DictReader(io.StringIO(output, newline=""), strict=True)
    try:
        if reader.fieldnames is not None and not {"language", "blank", "comment", "code"}.issubset(
            reader.fieldnames
        ):
            raise ClocParseError("invalid cloc CSV: missing required columns")
        return list(reader)
    except csv.Error as exc:
        raise ClocParseError(f"invalid cloc CSV near line {reader.line_num}: parser error") from exc


def parse_language_summary_csv(output: str) -> list[LanguageRow]:
    """Parse a complete cloc language report, refusing invalid records."""

    rows: list[LanguageRow] = []
    for index, row in enumerate(_csv_rows(output), start=2):
        try:
            rows.append(_parse_language_row(row))
        except ValueError as exc:
            raise ClocParseError(f"invalid cloc CSV row {index}: language or counters") from exc
    return rows


def parse_file_summary_csv(output: str) -> list[FileRow]:
    """Parse a complete cloc file report, excluding only recognized summary rows."""

    rows: list[FileRow] = []
    for index, row in enumerate(_csv_rows(output), start=2):
        try:
            parsed = _parse_file_row(row)
        except ValueError as exc:
            raise ClocParseError(
                f"invalid cloc CSV row {index}: file identity or counters"
            ) from exc
        if parsed is not None:
            rows.append(parsed)
    return rows


def _parse_language_row(row: dict[str, str | None]) -> LanguageRow:
    language = _clean(row.get("language"))
    if not language or None in row:
        raise ValueError("invalid language record")
    return LanguageRow(
        language=language,
        # cloc summary CSV has used both "filename" and "files" for file counts.
        files=_parse_int(row.get("filename") or row.get("files")),
        blank=_parse_int(row.get("blank")),
        comment=_parse_int(row.get("comment")),
        code=_parse_int(row.get("code")),
    )


def _parse_file_row(row: dict[str, str | None]) -> FileRow | None:
    language = _clean(row.get("language"))
    if language == "SUM":
        return None
    path = row.get("filename")
    if not language or not path or None in row:
        raise ValueError("invalid file record")
    return FileRow(
        language=language,
        path=path,
        blank=_parse_int(row.get("blank")),
        comment=_parse_int(row.get("comment")),
        code=_parse_int(row.get("code")),
    )


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def _parse_int(value: str | None) -> int:
    cleaned = _clean(value)
    if not cleaned:
        raise ValueError("missing integer")
    if not cleaned.isascii() or not cleaned.isdecimal():
        raise ValueError("invalid nonnegative integer")
    return int(cleaned)
