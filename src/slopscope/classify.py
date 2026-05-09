"""Path classification and aggregation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from slopscope.report import AreaRow, DirectoryRow, FileAggregateReport, FileRow, SourceTestSummary

DEFAULT_SOURCE_DIRS = ("src",)
DEFAULT_TEST_DIRS = ("tests",)
DEFAULT_NAMED_AREAS = ("scripts", "examples", "specs")
DEFAULT_NESTED_BUCKET_DIRS: tuple[str, ...] = ()

SourceTestKind = Literal["source", "tests", "other"]


def is_test_path(
    path: Path | str,
    *,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
) -> bool:
    """Return whether a path should be classified as tests."""

    parts = _path_parts(path)
    if _matches_any_subpath(parts, test_dirs):
        return True

    filename = parts[-1].lower() if parts else ""
    return (
        filename.startswith("test_")
        or filename.endswith("_test.py")
        or ".test." in filename
        or ".spec." in filename
    )


def classify_source_test(
    path: Path | str,
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
) -> SourceTestKind:
    """Classify a path as source, tests, or neither."""

    parts = _path_parts(path)
    if is_test_path(path, test_dirs=test_dirs):
        return "tests"
    if _matches_any_prefix(parts, source_dirs):
        return "source"
    return "other"


def classify_area(
    path: Path | str,
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    named_areas: Iterable[str] = DEFAULT_NAMED_AREAS,
) -> str:
    """Classify a path into the default repository areas."""

    parts = _path_parts(path)
    if is_test_path(path, test_dirs=test_dirs):
        return "tests"
    if _matches_any_prefix(parts, source_dirs):
        return "src"
    if _matches_prefix(parts, _configured_parts("docs")):
        return "docs"

    for area in named_areas:
        if _matches_prefix(parts, _configured_parts(area)):
            return area

    return "tooling"


def bucket_directory(
    path: Path | str,
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    nested_bucket_dirs: Iterable[str] = DEFAULT_NESTED_BUCKET_DIRS,
) -> str:
    """Return the directory bucket for a file path."""

    parts = _path_parts(path)
    if len(parts) <= 1:
        return "."

    test_span = _first_matching_subpath_span(parts, test_dirs)
    if test_span is not None:
        start, end = test_span
        return _bucket_from_match(parts, start=start, end=end)

    source_prefix = _first_matching_prefix(parts, source_dirs)
    if source_prefix is not None:
        return _bucket_from_match(parts, start=0, end=len(source_prefix) - 1)

    docs_prefix = _configured_parts("docs")
    if _matches_prefix(parts, docs_prefix):
        return _bucket_from_match(parts, start=0, end=len(docs_prefix) - 1)

    nested_prefix = _first_matching_prefix(parts, nested_bucket_dirs)
    if nested_prefix is not None:
        return _bucket_from_match(parts, start=0, end=len(nested_prefix) - 1)

    return parts[0]


def aggregate_source_tests(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
) -> SourceTestSummary:
    """Aggregate source and test totals from file rows."""

    source_files = 0
    source_code = 0
    test_files = 0
    test_code = 0

    for row in file_rows:
        kind = classify_source_test(row.path, source_dirs=source_dirs, test_dirs=test_dirs)
        if kind == "source":
            source_files += 1
            source_code += row.code
        elif kind == "tests":
            test_files += 1
            test_code += row.code

    return SourceTestSummary(
        source_files=source_files,
        source_code=source_code,
        test_files=test_files,
        test_code=test_code,
    )


def aggregate_areas(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    named_areas: Iterable[str] = DEFAULT_NAMED_AREAS,
) -> tuple[AreaRow, ...]:
    """Aggregate repository area totals from file rows."""

    totals: dict[str, tuple[int, int]] = {}
    for row in file_rows:
        area = classify_area(
            row.path,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
            named_areas=named_areas,
        )
        files, code = totals.get(area, (0, 0))
        totals[area] = (files + 1, code + row.code)

    rows = [AreaRow(name=area, files=files, code=code) for area, (files, code) in totals.items()]
    return tuple(sorted(rows, key=_aggregate_sort_key))


def aggregate_directories(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    nested_bucket_dirs: Iterable[str] = DEFAULT_NESTED_BUCKET_DIRS,
) -> tuple[DirectoryRow, ...]:
    """Aggregate directory bucket totals from file rows."""

    totals: dict[str, tuple[int, int]] = {}
    for row in file_rows:
        bucket = bucket_directory(
            row.path,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
            nested_bucket_dirs=nested_bucket_dirs,
        )
        files, code = totals.get(bucket, (0, 0))
        totals[bucket] = (files + 1, code + row.code)

    rows = [
        DirectoryRow(name=bucket, files=files, code=code)
        for bucket, (files, code) in totals.items()
    ]
    return tuple(sorted(rows, key=_aggregate_sort_key))


def build_file_aggregate_report(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    named_areas: Iterable[str] = DEFAULT_NAMED_AREAS,
    nested_bucket_dirs: Iterable[str] = DEFAULT_NESTED_BUCKET_DIRS,
) -> FileAggregateReport:
    """Build all internal aggregate sections from file rows."""

    rows = tuple(file_rows)
    return FileAggregateReport(
        source_tests=aggregate_source_tests(
            rows,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
        ),
        area_rows=aggregate_areas(
            rows,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
            named_areas=named_areas,
        ),
        directory_rows=aggregate_directories(
            rows,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
            nested_bucket_dirs=nested_bucket_dirs,
        ),
    )


def _path_parts(path: Path | str) -> tuple[str, ...]:
    file_path = Path(path)
    return tuple(part for part in file_path.parts if part not in ("", ".", file_path.anchor))


def _configured_parts(path: Path | str) -> tuple[str, ...]:
    """Normalize a configured path-like value for path comparisons."""

    return _path_parts(path)


def _matches_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return bool(prefix) and len(parts) >= len(prefix) and parts[: len(prefix)] == prefix


def _matches_any_prefix(parts: tuple[str, ...], prefixes: Iterable[str]) -> bool:
    return _first_matching_prefix(parts, prefixes) is not None


def _first_matching_prefix(
    parts: tuple[str, ...],
    prefixes: Iterable[str],
) -> tuple[str, ...] | None:
    for prefix in prefixes:
        prefix_parts = _configured_parts(prefix)
        if _matches_prefix(parts, prefix_parts):
            return prefix_parts
    return None


def _matches_any_subpath(parts: tuple[str, ...], paths: Iterable[str]) -> bool:
    return _first_matching_subpath_span(parts, paths) is not None


def _first_matching_subpath_span(
    parts: tuple[str, ...],
    paths: Iterable[str],
) -> tuple[int, int] | None:
    for path in paths:
        path_parts = _configured_parts(path)
        if not path_parts or len(path_parts) > len(parts):
            continue

        for index in range(len(parts) - len(path_parts) + 1):
            if parts[index : index + len(path_parts)] == path_parts:
                return index, index + len(path_parts) - 1

    return None


def _bucket_from_match(parts: tuple[str, ...], *, start: int, end: int) -> str:
    bucket_parts = parts[start : end + 1]
    next_index = end + 1
    if next_index < len(parts) - 1:
        bucket_parts = parts[start : next_index + 1]
    return "/".join(bucket_parts)


def _aggregate_sort_key(row: AreaRow | DirectoryRow) -> tuple[int, int, str]:
    return (-row.code, -row.files, row.name)
