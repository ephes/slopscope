"""Pure report data structures."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class LanguageRow:
    """A language summary row."""

    language: str
    files: int
    blank: int
    comment: int
    code: int


@dataclass(frozen=True)
class FileRow:
    """A file-level count row."""

    language: str
    path: str
    blank: int
    comment: int
    code: int


@dataclass(frozen=True)
class SourceTestSummary:
    """Source and test totals aggregated from file-level count rows."""

    source_files: int
    source_code: int
    test_files: int
    test_code: int


@dataclass(frozen=True)
class AreaRow:
    """Repository area totals aggregated from file-level count rows."""

    name: str
    files: int
    code: int


@dataclass(frozen=True)
class DirectoryRow:
    """Directory bucket totals aggregated from file-level count rows."""

    name: str
    files: int
    code: int


@dataclass(frozen=True)
class FileAggregateReport:
    """Internal aggregate report data built from file-level count rows."""

    source_tests: SourceTestSummary
    area_rows: tuple[AreaRow, ...]
    directory_rows: tuple[DirectoryRow, ...]


@dataclass(frozen=True)
class GroupedRow:
    """Grouped profile row aggregated from matching file-level rows."""

    name: str
    files: int
    code: int


@dataclass(frozen=True)
class ProfileTotalReport:
    """Profile total report data independent of rendering."""

    profile: str
    engine: str
    path: Path
    total: int
    physical_lines: bool


@dataclass(frozen=True)
class GroupedProfileReport:
    """Grouped profile report data independent of rendering."""

    profile: str
    engine: str
    path: Path
    group_by: str
    rows: tuple[GroupedRow, ...]
    total: int
    top: int | None
    physical_lines: bool


@dataclass(frozen=True)
class LanguageSummaryReport:
    """Language-summary report data independent of counting and rendering."""

    engine: str
    path: Path
    language_rows: tuple[LanguageRow, ...]

    @classmethod
    def from_rows(
        cls,
        *,
        engine: str,
        path: Path | str,
        language_rows: Iterable[LanguageRow],
    ) -> Self:
        """Build a report from any iterable of language rows."""

        return cls(
            engine=engine,
            path=Path(path),
            language_rows=tuple(language_rows),
        )


@dataclass(frozen=True)
class RepositoryReport:
    """Complete single-repository report data independent of rendering."""

    engine: str
    path: Path
    language_rows: tuple[LanguageRow, ...]
    source_test_summary: SourceTestSummary
    area_rows: tuple[AreaRow, ...]
    directory_rows: tuple[DirectoryRow, ...]

    @classmethod
    def from_reports(
        cls,
        *,
        language_report: LanguageSummaryReport,
        aggregate_report: FileAggregateReport,
    ) -> Self:
        """Build a complete report from language and file aggregate reports."""

        return cls(
            engine=language_report.engine,
            path=language_report.path,
            language_rows=language_report.language_rows,
            source_test_summary=aggregate_report.source_tests,
            area_rows=aggregate_report.area_rows,
            directory_rows=aggregate_report.directory_rows,
        )
