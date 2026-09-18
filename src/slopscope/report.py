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
COMPOSITION_TAGS = ("qt", "logging", "data_shape")
COMPOSITION_MARKERS = ("compat",)
COMPOSITION_TEST_PLACEMENTS = ("test", "fixture", "setup", "module_level", "helper_or_unknown")
COMPOSITION_CONSTRUCTS = ("classes", "functions", "data_shape_classes", "qt_classes", "tests")


def _zeros(names: tuple[str, ...]) -> tuple[int, ...]:
    return (0,) * len(names)


def _add(left: tuple[int, ...], right: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(a + b for a, b in zip(left, right, strict=True))


@dataclass(frozen=True)
class CompositionCounts:
    """Structural line categories, semantic tags, and statement counts for files.

    Each tuple holds one count per name in the matching ``COMPOSITION_*`` constant, in order.
    ``categories`` add up to the physical line count. ``tags`` and ``markers`` count code lines
    and may overlap. ``placements`` count code lines of test files only.
    """

    categories: tuple[int, ...]
    statements: int
    continuation_lines: int
    tags: tuple[int, ...] = _zeros(COMPOSITION_TAGS)
    markers: tuple[int, ...] = _zeros(COMPOSITION_MARKERS)
    placements: tuple[int, ...] = _zeros(COMPOSITION_TEST_PLACEMENTS)
    constructs: tuple[int, ...] = _zeros(COMPOSITION_CONSTRUCTS)
    duplicated_lines: int = 0

    @classmethod
    def sum(cls, counts: Iterable[CompositionCounts]) -> Self:
        """Add counts field by field."""

        total = cls(categories=_zeros(COMPOSITION_CATEGORIES), statements=0, continuation_lines=0)
        for item in counts:
            total = cls(
                categories=_add(total.categories, item.categories),
                statements=total.statements + item.statements,
                continuation_lines=total.continuation_lines + item.continuation_lines,
                tags=_add(total.tags, item.tags),
                markers=_add(total.markers, item.markers),
                placements=_add(total.placements, item.placements),
                constructs=_add(total.constructs, item.constructs),
                duplicated_lines=total.duplicated_lines + item.duplicated_lines,
            )
        return total

    def tag(self, name: str) -> int:
        """Return the code line count for one semantic tag."""

        return self.tags[COMPOSITION_TAGS.index(name)]

    def marker(self, name: str) -> int:
        """Return the code line count for one marker."""

        return self.markers[COMPOSITION_MARKERS.index(name)]

    def placement(self, name: str) -> int:
        """Return the test-file code line count for one test placement."""

        return self.placements[COMPOSITION_TEST_PLACEMENTS.index(name)]

    def construct(self, name: str) -> int:
        """Return the count for one construct."""

        return self.constructs[COMPOSITION_CONSTRUCTS.index(name)]

    def tag_mapping(self) -> dict[str, int]:
        """Return every tag with its count, in tag order."""

        return dict(zip(COMPOSITION_TAGS, self.tags, strict=True))

    def marker_mapping(self) -> dict[str, int]:
        """Return every marker with its count, in marker order."""

        return dict(zip(COMPOSITION_MARKERS, self.markers, strict=True))

    def placement_mapping(self) -> dict[str, int]:
        """Return every test placement with its count, in placement order."""

        return dict(zip(COMPOSITION_TEST_PLACEMENTS, self.placements, strict=True))

    def construct_mapping(self) -> dict[str, int]:
        """Return every construct with its count, in construct order."""

        return dict(zip(COMPOSITION_CONSTRUCTS, self.constructs, strict=True))

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
class CompositionDuplicateOccurrence:
    """One copy of a duplicated block."""

    path: str
    start_line: int
    end_line: int
    kind: str

    @property
    def lines(self) -> int:
        """Physical lines spanned by this copy."""

        return self.end_line - self.start_line + 1


@dataclass(frozen=True)
class CompositionDuplicateBlock:
    """A duplicated token sequence and every place it occurs."""

    tokens: int
    occurrences: tuple[CompositionDuplicateOccurrence, ...]

    @property
    def lines(self) -> int:
        """Physical lines spanned by the longest copy."""

        return max(occurrence.lines for occurrence in self.occurrences)


@dataclass(frozen=True)
class CompositionFailure:
    """A discovered Python file that could not be read, decoded, or parsed."""

    path: str
    error: str


@dataclass(frozen=True)
class CompositionDetector:
    """A named, versioned semantic detector that contributed to a composition report."""

    name: str
    kind: str
    version: int


@dataclass(frozen=True)
class CompositionSettings:
    """Settings that shaped a composition report."""

    limit: int
    min_duplicate_tokens: int
    qt_modules: tuple[str, ...]
    logging_patterns: tuple[str, ...]
    compat_markers: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    include_globs: tuple[str, ...]
    source_dirs: tuple[str, ...]
    test_dirs: tuple[str, ...]
    areas: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        """Return the settings as JSON-ready values, in output order."""

        return {
            "language": "Python",
            "limit": self.limit,
            "min_duplicate_tokens": self.min_duplicate_tokens,
            "qt_modules": list(self.qt_modules),
            "logging_patterns": list(self.logging_patterns),
            "compat_markers": list(self.compat_markers),
            "excluded_paths": list(self.excluded_paths),
            "include_globs": list(self.include_globs),
            "source_dirs": list(self.source_dirs),
            "test_dirs": list(self.test_dirs),
            "areas": list(self.areas),
        }


@dataclass(frozen=True)
class CompositionMetricDelta:
    """One metric compared with its baseline value."""

    metric: str
    baseline: int | None
    current: int

    @property
    def delta(self) -> int | None:
        """Current minus baseline, or ``None`` without a baseline value."""

        return None if self.baseline is None else self.current - self.baseline


@dataclass(frozen=True)
class CompositionScopeDelta:
    """Metric deltas for the total or one source/test kind."""

    name: str
    metrics: tuple[CompositionMetricDelta, ...]

    def get(self, metric: str) -> CompositionMetricDelta:
        """Return the delta for one metric name."""

        for item in self.metrics:
            if item.metric == metric:
                return item
        raise KeyError(metric)


@dataclass(frozen=True)
class CompositionComparison:
    """A composition report compared with an earlier snapshot."""

    path: Path
    analyzer_version: int | None
    python_version: str | None
    comparable: bool
    warnings: tuple[str, ...]
    scopes: tuple[CompositionScopeDelta, ...]


@dataclass(frozen=True)
class CompositionChurnMonth:
    """Python lines added and removed in one month, per source/tests/other kind."""

    month: str
    added: tuple[int, ...]
    removed: tuple[int, ...]

    @property
    def net(self) -> tuple[int, ...]:
        """Added minus removed, per kind."""

        return tuple(a - r for a, r in zip(self.added, self.removed, strict=True))


@dataclass(frozen=True)
class CompositionChurn:
    """Monthly churn of Python lines on one Git ref, or why it was skipped."""

    ref: str
    months: int
    since: str
    status: str
    reason: str | None
    rows: tuple[CompositionChurnMonth, ...]


@dataclass(frozen=True)
class CompositionReport:
    """Complete Python composition report for one path."""

    path: Path
    analyzer: str
    analyzer_version: int
    schema_version: int
    python_version: str
    detectors: tuple[CompositionDetector, ...]
    settings: CompositionSettings
    total: CompositionAggregate
    kinds: tuple[CompositionAggregate, ...]
    areas: tuple[CompositionAggregate, ...]
    files: tuple[CompositionFileRow, ...]
    largest_modules: tuple[CompositionFileRow, ...]
    largest_classes: tuple[CompositionClassRow, ...]
    largest_functions: tuple[CompositionFunctionRow, ...]
    duplicates: tuple[CompositionDuplicateBlock, ...]
    failures: tuple[CompositionFailure, ...]
    baseline: CompositionComparison | None = None
    churn: CompositionChurn | None = None


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
    detectors: tuple[CompositionDetector, ...]
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
