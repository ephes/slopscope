"""Path classification and aggregation helpers."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Literal

from slopscope.report import AreaRow, DirectoryRow, FileAggregateReport, FileRow, SourceTestSummary

DEFAULT_SOURCE_DIRS = ("src",)
DEFAULT_TEST_DIRS = ("tests",)
DEFAULT_NAMED_AREAS = ("scripts", "examples", "specs")
DEFAULT_NESTED_BUCKET_DIRS: tuple[str, ...] = ()

_DOCS_PREFIX = ("docs",)
_PathParts = tuple[str, ...]
_FileTotals = dict[str, tuple[int, int]]

SourceTestKind = Literal["source", "tests", "other"]


def is_test_path(
    path: Path | str,
    *,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
) -> bool:
    """Return whether a path should be classified as tests."""

    return _is_test_path(_path_parts(path), _iter_configured_parts(test_dirs))


def _is_test_path(parts: _PathParts, test_dirs: Iterable[_PathParts]) -> bool:
    return _first_matching_subpath_span(parts, test_dirs) is not None or _is_test_filename(parts)


def _is_test_filename(parts: _PathParts) -> bool:
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

    return _classify_source_test(
        _path_parts(path), _iter_configured_parts(source_dirs), _iter_configured_parts(test_dirs)
    )


def _classify_source_test(
    parts: _PathParts, source_dirs: Iterable[_PathParts], test_dirs: Iterable[_PathParts]
) -> SourceTestKind:
    is_test = _is_test_path(parts, test_dirs)
    source_prefix = None if is_test else _first_matching_prefix(parts, source_dirs)
    return _source_test_kind(is_test, source_prefix)


def _source_test_kind(is_test: bool, source_prefix: _PathParts | None) -> SourceTestKind:
    if is_test:
        return "tests"
    if source_prefix is not None:
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
    kind = _classify_source_test(
        parts, _iter_configured_parts(source_dirs), _iter_configured_parts(test_dirs)
    )
    return _area_for_kind(parts, kind, _iter_named_area_prefixes(named_areas))


def _area_for_kind(
    parts: _PathParts, kind: SourceTestKind, named_areas: Iterable[tuple[str, _PathParts]]
) -> str:
    if kind == "tests":
        return "tests"
    if kind == "source":
        return "src"
    if _matches_prefix(parts, _DOCS_PREFIX):
        return "docs"

    for area, prefix in named_areas:
        if _matches_prefix(parts, prefix):
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

    return _bucket_directory(
        _path_parts(path),
        _iter_configured_parts(source_dirs),
        _iter_configured_parts(test_dirs),
        _iter_configured_parts(nested_bucket_dirs),
    )


def _bucket_directory(
    parts: _PathParts,
    source_dirs: Iterable[_PathParts],
    test_dirs: Iterable[_PathParts],
    nested_bucket_dirs: Iterable[_PathParts],
) -> str:
    if len(parts) <= 1:
        return "."

    test_span = _first_matching_subpath_span(parts, test_dirs)
    source_prefix = None if test_span is not None else _first_matching_prefix(parts, source_dirs)
    return _bucket_for_matches(parts, test_span, source_prefix, nested_bucket_dirs)


def _bucket_for_matches(
    parts: _PathParts,
    test_span: tuple[int, int] | None,
    source_prefix: _PathParts | None,
    nested_bucket_dirs: Iterable[_PathParts],
) -> str:
    if test_span is not None:
        start, end = test_span
        return _bucket_from_match(parts, start=start, end=end)

    if source_prefix is not None:
        return _bucket_from_match(parts, start=0, end=len(source_prefix) - 1)

    if _matches_prefix(parts, _DOCS_PREFIX):
        return _bucket_from_match(parts, start=0, end=len(_DOCS_PREFIX) - 1)

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

    source_prefixes = tuple(_iter_configured_parts(source_dirs))
    test_paths = tuple(_iter_configured_parts(test_dirs))

    totals: _FileTotals = {}
    for row in file_rows:
        kind = _classify_source_test(_path_parts(row.path), source_prefixes, test_paths)
        if kind != "other":
            _add_file_total(totals, kind, row.code)
    return _source_test_summary(totals)


def aggregate_areas(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    named_areas: Iterable[str] = DEFAULT_NAMED_AREAS,
) -> tuple[AreaRow, ...]:
    """Aggregate repository area totals from file rows."""

    source_prefixes = tuple(_iter_configured_parts(source_dirs))
    test_paths = tuple(_iter_configured_parts(test_dirs))
    area_prefixes = tuple(_iter_named_area_prefixes(named_areas))

    totals: _FileTotals = {}
    for row in file_rows:
        parts = _path_parts(row.path)
        kind = _classify_source_test(parts, source_prefixes, test_paths)
        area = _area_for_kind(parts, kind, area_prefixes)
        _add_file_total(totals, area, row.code)
    return _area_rows(totals)


def aggregate_directories(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    nested_bucket_dirs: Iterable[str] = DEFAULT_NESTED_BUCKET_DIRS,
) -> tuple[DirectoryRow, ...]:
    """Aggregate directory bucket totals from file rows."""

    source_prefixes = tuple(_iter_configured_parts(source_dirs))
    test_paths = tuple(_iter_configured_parts(test_dirs))
    nested_prefixes = tuple(_iter_configured_parts(nested_bucket_dirs))

    totals: _FileTotals = {}
    for row in file_rows:
        bucket = _bucket_directory(
            _path_parts(row.path), source_prefixes, test_paths, nested_prefixes
        )
        _add_file_total(totals, bucket, row.code)
    return _directory_rows(totals)


def build_file_aggregate_report(
    file_rows: Iterable[FileRow],
    *,
    source_dirs: Iterable[str] = DEFAULT_SOURCE_DIRS,
    test_dirs: Iterable[str] = DEFAULT_TEST_DIRS,
    named_areas: Iterable[str] = DEFAULT_NAMED_AREAS,
    nested_bucket_dirs: Iterable[str] = DEFAULT_NESTED_BUCKET_DIRS,
) -> FileAggregateReport:
    """Build all internal aggregate sections from file rows."""

    source_prefixes = tuple(_iter_configured_parts(source_dirs))
    test_paths = tuple(_iter_configured_parts(test_dirs))
    area_prefixes = tuple(_iter_named_area_prefixes(named_areas))
    nested_prefixes = tuple(_iter_configured_parts(nested_bucket_dirs))

    source_test_totals: _FileTotals = {}
    area_totals: _FileTotals = {}
    directory_totals: _FileTotals = {}
    for row in file_rows:
        parts = _path_parts(row.path)
        test_span = _first_matching_subpath_span(parts, test_paths)
        source_prefix = (
            None if test_span is not None else _first_matching_prefix(parts, source_prefixes)
        )
        kind = _source_test_kind(test_span is not None or _is_test_filename(parts), source_prefix)
        area = _area_for_kind(parts, kind, area_prefixes)
        bucket = (
            "."
            if len(parts) <= 1
            else _bucket_for_matches(parts, test_span, source_prefix, nested_prefixes)
        )
        if kind != "other":
            _add_file_total(source_test_totals, kind, row.code)
        _add_file_total(area_totals, area, row.code)
        _add_file_total(directory_totals, bucket, row.code)

    return FileAggregateReport(
        source_tests=_source_test_summary(source_test_totals),
        area_rows=_area_rows(area_totals),
        directory_rows=_directory_rows(directory_totals),
    )


def _add_file_total(totals: _FileTotals, name: str, code: int) -> None:
    files, previous_code = totals.get(name, (0, 0))
    totals[name] = (files + 1, previous_code + code)


def _source_test_summary(totals: _FileTotals) -> SourceTestSummary:
    source_files, source_code = totals.get("source", (0, 0))
    test_files, test_code = totals.get("tests", (0, 0))
    return SourceTestSummary(
        source_files=source_files,
        source_code=source_code,
        test_files=test_files,
        test_code=test_code,
    )


def _area_rows(totals: _FileTotals) -> tuple[AreaRow, ...]:
    rows = [AreaRow(name=area, files=files, code=code) for area, (files, code) in totals.items()]
    return tuple(sorted(rows, key=_aggregate_sort_key))


def _directory_rows(totals: _FileTotals) -> tuple[DirectoryRow, ...]:
    rows = [
        DirectoryRow(name=bucket, files=files, code=code)
        for bucket, (files, code) in totals.items()
    ]
    return tuple(sorted(rows, key=_aggregate_sort_key))


def _path_parts(path: Path | str) -> _PathParts:
    file_path = Path(path)
    return tuple(part for part in file_path.parts if part not in ("", ".", file_path.anchor))


def _configured_parts(path: Path | str) -> _PathParts:
    """Normalize a configured path-like value for path comparisons."""

    return _path_parts(path)


def _iter_configured_parts(paths: Iterable[str]) -> Iterator[_PathParts]:
    for path in paths:
        yield _configured_parts(path)


def _iter_named_area_prefixes(areas: Iterable[str]) -> Iterator[tuple[str, _PathParts]]:
    for area in areas:
        yield area, _configured_parts(area)


def _matches_prefix(parts: _PathParts, prefix: _PathParts) -> bool:
    return bool(prefix) and len(parts) >= len(prefix) and parts[: len(prefix)] == prefix


def _first_matching_prefix(
    parts: _PathParts,
    prefixes: Iterable[_PathParts],
) -> _PathParts | None:
    for prefix in prefixes:
        if _matches_prefix(parts, prefix):
            return prefix
    return None


def _first_matching_subpath_span(
    parts: _PathParts,
    paths: Iterable[_PathParts],
) -> tuple[int, int] | None:
    for path_parts in paths:
        if not path_parts or len(path_parts) > len(parts):
            continue

        for index in range(len(parts) - len(path_parts) + 1):
            if parts[index : index + len(path_parts)] == path_parts:
                return index, index + len(path_parts) - 1

    return None


def _bucket_from_match(parts: _PathParts, *, start: int, end: int) -> str:
    bucket_parts = parts[start : end + 1]
    next_index = end + 1
    if next_index < len(parts) - 1:
        bucket_parts = parts[start : next_index + 1]
    return "/".join(bucket_parts)


def _aggregate_sort_key(row: AreaRow | DirectoryRow) -> tuple[int, int, str]:
    return (-row.code, -row.files, row.name)
