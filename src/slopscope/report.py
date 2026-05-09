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
