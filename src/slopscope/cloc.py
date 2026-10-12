"""Small cloc integration surface for language summaries."""

from __future__ import annotations

import contextlib
import csv
import io
import os
import shutil
import signal
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from slopscope.report import FileRow, LanguageRow

CLOC_TIMEOUT_SECONDS = 30


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
            text=True,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return ClocResult(2, "", f"slopscope: could not run cloc: {exc}")
    try:
        stdout, stderr = process.communicate(timeout=CLOC_TIMEOUT_SECONDS)
    except BaseException as exc:
        # Match churn's cleanup policy, including children holding captured pipes open.
        if os.name == "posix":
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
        with contextlib.suppress(OSError):
            process.kill()
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            process.communicate(timeout=1)
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=5)
        if isinstance(exc, subprocess.TimeoutExpired):
            return ClocResult(
                2, "", f"slopscope: cloc timed out after {CLOC_TIMEOUT_SECONDS} seconds"
            )
        raise
    return ClocResult(process.returncode, stdout, stderr)


def run_language_summary(path: Path | str, executable: str = "cloc") -> ClocResult:
    """Run cloc for a language summary at the selected path."""

    return run_command(build_language_summary_command(path, executable=executable))


def run_file_summary(path: Path | str, executable: str = "cloc") -> ClocResult:
    """Run cloc for file-level count rows at the selected path."""

    return run_command(build_file_summary_command(path, executable=executable))


def parse_language_summary_csv(output: str) -> list[LanguageRow]:
    """Parse cloc CSV language summary output, skipping malformed rows."""

    reader = csv.DictReader(io.StringIO(output, newline=""))
    rows: list[LanguageRow] = []
    for row in reader:
        parsed = _parse_language_row(row)
        if parsed is not None:
            rows.append(parsed)
    return rows


def parse_file_summary_csv(output: str) -> list[FileRow]:
    """Parse cloc CSV file-level output, skipping malformed and non-file rows."""

    reader = csv.DictReader(io.StringIO(output, newline=""))
    rows: list[FileRow] = []
    for row in reader:
        parsed = _parse_file_row(row)
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
            # cloc summary CSV has used both "filename" and "files" for file counts.
            files=_parse_int(row.get("filename") or row.get("files")),
            blank=_parse_int(row.get("blank")),
            comment=_parse_int(row.get("comment")),
            code=_parse_int(row.get("code")),
        )
    except ValueError:
        return None


def _parse_file_row(row: dict[str, str | None]) -> FileRow | None:
    language = _clean(row.get("language"))
    path = row.get("filename")
    if not language or language == "SUM" or not path:
        return None

    try:
        return FileRow(
            language=language,
            path=path,
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
