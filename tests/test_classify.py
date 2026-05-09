from __future__ import annotations

from slopscope import classify
from slopscope.report import AreaRow, DirectoryRow, FileAggregateReport, FileRow, SourceTestSummary


def row(path: str, code: int) -> FileRow:
    return FileRow(language="Python", path=path, blank=0, comment=0, code=code)


def test_is_test_path_detects_tests_segment() -> None:
    assert classify.is_test_path("tests/test_cli.py")
    assert classify.is_test_path("src/package/tests/test_model.py")


def test_is_test_path_detects_filename_patterns() -> None:
    assert classify.is_test_path("src/test_widget.py")
    assert classify.is_test_path("src/widget_test.py")
    assert classify.is_test_path("frontend/widget.test.ts")
    assert classify.is_test_path("frontend/widget.spec.ts")


def test_classify_source_test_uses_source_dirs_and_tests() -> None:
    assert classify.classify_source_test("src/slopscope/cli.py") == "source"
    assert classify.classify_source_test("tests/test_cli.py") == "tests"
    assert classify.classify_source_test("docs/index.md") == "other"
    assert classify.classify_source_test("lib/app.py", source_dirs=("lib",)) == "source"


def test_classify_area_defaults() -> None:
    assert classify.classify_area("src/slopscope/cli.py") == "src"
    assert classify.classify_area("docs/index.md") == "docs"
    assert classify.classify_area("scripts/release.py") == "scripts"
    assert classify.classify_area("examples/demo.py") == "examples"
    assert classify.classify_area("specs/api.md") == "specs"
    assert classify.classify_area("pyproject.toml") == "tooling"


def test_classify_area_keeps_tests_before_source() -> None:
    assert classify.classify_area("src/test_widget.py") == "tests"


def test_bucket_directory_defaults() -> None:
    assert classify.bucket_directory("pyproject.toml") == "."
    assert classify.bucket_directory("src/package.py") == "src"
    assert classify.bucket_directory("src/slopscope/cli.py") == "src/slopscope"
    assert classify.bucket_directory("tests/test_cli.py") == "tests"
    assert classify.bucket_directory("tests/unit/test_cli.py") == "tests/unit"
    assert classify.bucket_directory("src/package/tests/test_model.py") == "tests"
    assert classify.bucket_directory("src/package/tests/unit/test_model.py") == "tests/unit"
    assert classify.bucket_directory("docs/index.md") == "docs"
    assert classify.bucket_directory("docs/api/index.md") == "docs/api"
    assert classify.bucket_directory("packages/app/main.py") == "packages"


def test_bucket_directory_supports_multi_segment_test_dirs() -> None:
    assert (
        classify.bucket_directory(
            "tests/integration/test_api.py",
            test_dirs=("tests/integration",),
        )
        == "tests/integration"
    )
    assert (
        classify.bucket_directory(
            "tests/integration/http/test_api.py",
            test_dirs=("tests/integration",),
        )
        == "tests/integration/http"
    )


def test_bucket_directory_does_not_return_absolute_path_anchor() -> None:
    assert classify.bucket_directory("/abs/src/cli.py") == "abs"


def test_bucket_directory_supports_nested_bucket_parameters() -> None:
    assert (
        classify.bucket_directory(
            "shells/desktop/app/main.py",
            nested_bucket_dirs=("shells",),
        )
        == "shells/desktop"
    )


def test_aggregate_source_tests_counts_files_and_code() -> None:
    rows = [
        row("src/slopscope/cli.py", 10),
        row("src/slopscope/empty.py", 0),
        row("tests/test_cli.py", 3),
        row("README.md", 5),
    ]

    assert classify.aggregate_source_tests(rows) == SourceTestSummary(
        source_files=2,
        source_code=10,
        test_files=1,
        test_code=3,
    )


def test_aggregate_areas_counts_files_and_code() -> None:
    rows = [
        row("src/slopscope/cli.py", 10),
        row("src/slopscope/empty.py", 0),
        row("docs/index.md", 5),
        row("tests/test_cli.py", 3),
    ]

    assert classify.aggregate_areas(rows) == (
        AreaRow(name="src", files=2, code=10),
        AreaRow(name="docs", files=1, code=5),
        AreaRow(name="tests", files=1, code=3),
    )


def test_aggregate_directories_counts_files_and_code() -> None:
    rows = [
        row("src/slopscope/cli.py", 10),
        row("src/slopscope/empty.py", 0),
        row("tests/unit/test_cli.py", 3),
        row("pyproject.toml", 2),
    ]

    assert classify.aggregate_directories(rows) == (
        DirectoryRow(name="src/slopscope", files=2, code=10),
        DirectoryRow(name="tests/unit", files=1, code=3),
        DirectoryRow(name=".", files=1, code=2),
    )


def test_aggregate_rows_sort_by_code_files_and_name() -> None:
    rows = [
        row("gamma/one.py", 5),
        row("beta/one.py", 3),
        row("beta/two.py", 2),
        row("alpha/one.py", 5),
    ]

    assert classify.aggregate_directories(rows) == (
        DirectoryRow(name="beta", files=2, code=5),
        DirectoryRow(name="alpha", files=1, code=5),
        DirectoryRow(name="gamma", files=1, code=5),
    )


def test_build_file_aggregate_report_returns_all_sections() -> None:
    rows = [
        row("src/slopscope/cli.py", 10),
        row("tests/test_cli.py", 3),
    ]

    assert classify.build_file_aggregate_report(rows) == FileAggregateReport(
        source_tests=SourceTestSummary(
            source_files=1,
            source_code=10,
            test_files=1,
            test_code=3,
        ),
        area_rows=(
            AreaRow(name="src", files=1, code=10),
            AreaRow(name="tests", files=1, code=3),
        ),
        directory_rows=(
            DirectoryRow(name="src/slopscope", files=1, code=10),
            DirectoryRow(name="tests", files=1, code=3),
        ),
    )
