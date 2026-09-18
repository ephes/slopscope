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
class ProjectSnapshotRow:
    """One row in a multi-project snapshot."""

    name: str
    path: Path
    engine: str
    files: int
    code: int
    source_code: int
    test_code: int


@dataclass(frozen=True)
class SkippedProject:
    """Configured project skipped during multi-project execution."""

    name: str
    path: Path
    reason: str


@dataclass(frozen=True)
class ProjectReport:
    """A named project and its default repository report."""

    name: str
    report: RepositoryReport


@dataclass(frozen=True)
class MultiProjectReport:
    """Complete multi-project workspace report data."""

    engine: str
    projects: tuple[ProjectReport, ...]
    snapshot_rows: tuple[ProjectSnapshotRow, ...]
    skipped_projects: tuple[SkippedProject, ...]


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


COMPOSITION_CATEGORIES = (
    "blank",
    "comment",
    "docstring",
    "import",
    "definition",
    "assertion",
    "error_handling",
    "literal_data",
    "other_code",
)
COMPOSITION_NON_CODE_CATEGORIES = ("blank", "comment", "docstring")


@dataclass(frozen=True)
class CompositionCounts:
    """Structural line categories and statement counts for one file or aggregate.

    ``categories`` holds one count per name in ``COMPOSITION_CATEGORIES``, in that order.
    """

    categories: tuple[int, ...]
    statements: int
    continuation_lines: int

    @classmethod
    def sum(cls, counts: Iterable[CompositionCounts]) -> Self:
        """Add counts category by category."""

        categories = [0] * len(COMPOSITION_CATEGORIES)
        statements = 0
        continuation_lines = 0
        for item in counts:
            for index, value in enumerate(item.categories):
                categories[index] += value
            statements += item.statements
            continuation_lines += item.continuation_lines
        return cls(
            categories=tuple(categories),
            statements=statements,
            continuation_lines=continuation_lines,
        )

    def category(self, name: str) -> int:
        """Return the line count for one category name."""

        return self.categories[COMPOSITION_CATEGORIES.index(name)]

    def as_mapping(self) -> dict[str, int]:
        """Return every category with its count, in category order."""

        return dict(zip(COMPOSITION_CATEGORIES, self.categories, strict=True))

    @property
    def physical(self) -> int:
        """Physical lines: the sum of all categories."""

        return sum(self.categories)

    @property
    def code(self) -> int:
        """Physical lines that are not blank, comment, or docstring lines."""

        return self.physical - sum(self.category(name) for name in COMPOSITION_NON_CODE_CATEGORIES)

    @property
    def code_per_statement(self) -> float | None:
        """Code lines per statement, or ``None`` without statements."""

        if self.statements == 0:
            return None
        return self.code / self.statements


@dataclass(frozen=True)
class CompositionFileRow:
    """Composition counts for one analyzed Python file."""

    path: str
    kind: str
    area: str
    counts: CompositionCounts


@dataclass(frozen=True)
class CompositionAggregate:
    """Composition counts summed over a named group of files."""

    name: str
    files: int
    counts: CompositionCounts


@dataclass(frozen=True)
class CompositionClassRow:
    """Size of one class definition, identified by its qualified name."""

    path: str
    name: str
    line: int
    lines: int
    methods: int
    kind: str

    @property
    def qualified_name(self) -> str:
        """Return ``path:Outer.Inner`` for display."""

        return f"{self.path}:{self.name}"


@dataclass(frozen=True)
class CompositionFunctionRow:
    """Size of one function or method definition, identified by its qualified name."""

    path: str
    name: str
    line: int
    lines: int
    kind: str

    @property
    def qualified_name(self) -> str:
        """Return ``path:Outer.method`` for display."""

        return f"{self.path}:{self.name}"


@dataclass(frozen=True)
class CompositionFailure:
    """A discovered Python file that could not be read, decoded, or parsed."""

    path: str
    error: str


@dataclass(frozen=True)
class CompositionSettings:
    """Settings that shaped a composition report."""

    limit: int
    excluded_paths: tuple[str, ...]
    include_globs: tuple[str, ...]
    source_dirs: tuple[str, ...]
    test_dirs: tuple[str, ...]
    areas: tuple[str, ...]


@dataclass(frozen=True)
class CompositionReport:
    """Complete Python composition report for one path."""

    path: Path
    analyzer: str
    analyzer_version: int
    schema_version: int
    python_version: str
    settings: CompositionSettings
    total: CompositionAggregate
    kinds: tuple[CompositionAggregate, ...]
    areas: tuple[CompositionAggregate, ...]
    files: tuple[CompositionFileRow, ...]
    largest_modules: tuple[CompositionFileRow, ...]
    largest_classes: tuple[CompositionClassRow, ...]
    largest_functions: tuple[CompositionFunctionRow, ...]
    failures: tuple[CompositionFailure, ...]


@dataclass(frozen=True)
class CompositionProjectReport:
    """A named configured project and its composition report."""

    name: str
    report: CompositionReport


@dataclass(frozen=True)
class MultiProjectCompositionReport:
    """Composition reports for configured projects."""

    analyzer: str
    analyzer_version: int
    schema_version: int
    python_version: str
    projects: tuple[CompositionProjectReport, ...]
    skipped_projects: tuple[SkippedProject, ...]

    @property
    def failures(self) -> tuple[tuple[str, CompositionFailure], ...]:
        """Return every failure with its project name."""

        return tuple(
            (project.name, failure)
            for project in self.projects
            for failure in project.report.failures
        )
