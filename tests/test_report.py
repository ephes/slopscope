from __future__ import annotations

from pathlib import Path

from slopscope.report import (
    AreaRow,
    DirectoryRow,
    FileAggregateReport,
    FileRow,
    LanguageRow,
    LanguageSummaryReport,
    MultiProjectReport,
    ProjectReport,
    ProjectSnapshotRow,
    RepositoryReport,
    SkippedProject,
    SourceTestSummary,
)


def test_language_summary_report_from_rows_normalizes_path_and_rows() -> None:
    rows = [
        LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        LanguageRow(language="SUM", files=2, blank=10, comment=4, code=90),
    ]

    report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=".",
        language_rows=rows,
    )

    assert report.engine == "cloc"
    assert report.path == Path(".")
    assert report.language_rows == tuple(rows)


def test_file_row_keeps_cloc_path_text() -> None:
    row = FileRow(language="Python", path="./src/slopscope/cli.py", blank=2, comment=3, code=40)

    assert row.path == "./src/slopscope/cli.py"


def test_file_aggregate_report_keeps_aggregate_rows() -> None:
    report = FileAggregateReport(
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

    assert report.source_tests.source_code == 10
    assert report.area_rows[0].name == "src"
    assert report.directory_rows[1].code == 3


def test_repository_report_from_reports_combines_language_and_aggregates() -> None:
    language_report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=".",
        language_rows=[
            LanguageRow(language="Python", files=1, blank=0, comment=0, code=10),
        ],
    )
    aggregate_report = FileAggregateReport(
        source_tests=SourceTestSummary(
            source_files=1,
            source_code=10,
            test_files=0,
            test_code=0,
        ),
        area_rows=(AreaRow(name="src", files=1, code=10),),
        directory_rows=(DirectoryRow(name="src", files=1, code=10),),
    )

    report = RepositoryReport.from_reports(
        language_report=language_report,
        aggregate_report=aggregate_report,
    )

    assert report.engine == "cloc"
    assert report.path == Path(".")
    assert report.language_rows == language_report.language_rows
    assert report.source_test_summary.source_code == 10
    assert report.area_rows == aggregate_report.area_rows
    assert report.directory_rows == aggregate_report.directory_rows


def test_multi_project_report_keeps_project_snapshot_and_skips() -> None:
    repository_report = RepositoryReport(
        engine="python",
        path=Path("frontend"),
        language_rows=(),
        source_test_summary=SourceTestSummary(
            source_files=0,
            source_code=0,
            test_files=0,
            test_code=0,
        ),
        area_rows=(),
        directory_rows=(),
    )
    project_report = ProjectReport(name="frontend", report=repository_report)
    snapshot_row = ProjectSnapshotRow(
        name="frontend",
        path=Path("frontend"),
        engine="python",
        files=0,
        code=0,
        source_code=0,
        test_code=0,
    )
    skipped_project = SkippedProject(
        name="docs",
        path=Path("docs"),
        reason="missing optional project path",
    )

    report = MultiProjectReport(
        engine="python",
        projects=(project_report,),
        snapshot_rows=(snapshot_row,),
        skipped_projects=(skipped_project,),
    )

    assert report.projects == (project_report,)
    assert report.snapshot_rows == (snapshot_row,)
    assert report.skipped_projects == (skipped_project,)
