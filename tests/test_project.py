from __future__ import annotations

from pathlib import Path

import pytest

from slopscope import config, project
from slopscope.report import (
    AreaRow,
    DirectoryRow,
    LanguageRow,
    ProjectReport,
    RepositoryReport,
    SkippedProject,
    SourceTestSummary,
)


def sample_repository_report(path: Path, *, files: int = 3, code: int = 30) -> RepositoryReport:
    return RepositoryReport(
        engine="python",
        path=path,
        language_rows=(
            LanguageRow(language="Python", files=files, blank=0, comment=0, code=code),
            LanguageRow(language="SUM", files=files, blank=0, comment=0, code=code),
        ),
        source_test_summary=SourceTestSummary(
            source_files=1,
            source_code=20,
            test_files=1,
            test_code=10,
        ),
        area_rows=(AreaRow(name="src", files=1, code=20),),
        directory_rows=(DirectoryRow(name="src", files=1, code=20),),
    )


def test_select_all_projects_keeps_config_order(tmp_path: Path) -> None:
    frontend = config.ProjectConfig(name="frontend", path=tmp_path / "frontend")
    backend = config.ProjectConfig(name="backend", path=tmp_path / "backend")
    slopscope_config = config.SlopscopeConfig(projects=(frontend, backend))

    assert project.select_projects(slopscope_config, ("all",)) == (frontend, backend)


def test_select_named_projects_returns_subset_in_config_order(tmp_path: Path) -> None:
    frontend = config.ProjectConfig(name="frontend", path=tmp_path / "frontend")
    backend = config.ProjectConfig(name="backend", path=tmp_path / "backend")
    docs = config.ProjectConfig(name="docs", path=tmp_path / "docs")
    slopscope_config = config.SlopscopeConfig(projects=(frontend, backend, docs))

    assert project.select_projects(slopscope_config, ("docs", "frontend")) == (frontend, docs)


def test_select_projects_rejects_unknown_name(tmp_path: Path) -> None:
    slopscope_config = config.SlopscopeConfig(
        projects=(config.ProjectConfig(name="frontend", path=tmp_path / "frontend"),)
    )

    with pytest.raises(project.ProjectError, match="no configured project named 'backend'"):
        project.select_projects(slopscope_config, ("backend",))


def test_select_projects_rejects_empty_config_for_named_project() -> None:
    with pytest.raises(project.ProjectError, match="no configured project named 'frontend'"):
        project.select_projects(config.SlopscopeConfig(), ("frontend",))


def test_select_projects_rejects_all_mixed_with_names(tmp_path: Path) -> None:
    slopscope_config = config.SlopscopeConfig(
        projects=(config.ProjectConfig(name="frontend", path=tmp_path / "frontend"),)
    )

    with pytest.raises(project.ProjectError, match="cannot be combined"):
        project.select_projects(slopscope_config, ("all", "frontend"))


def test_select_projects_rejects_duplicate_names(tmp_path: Path) -> None:
    slopscope_config = config.SlopscopeConfig(
        projects=(config.ProjectConfig(name="frontend", path=tmp_path / "frontend"),)
    )

    with pytest.raises(project.ProjectError, match="duplicate project selected: frontend"):
        project.select_projects(slopscope_config, ("frontend", "frontend"))


def test_partition_existing_projects_skips_optional_missing_paths(tmp_path: Path) -> None:
    existing_path = tmp_path / "frontend"
    existing_path.mkdir()
    frontend = config.ProjectConfig(name="frontend", path=existing_path)
    docs = config.ProjectConfig(name="docs", path=tmp_path / "docs", optional=True)

    existing, skipped = project.partition_existing_projects((frontend, docs))

    assert existing == (frontend,)
    assert skipped == (
        SkippedProject(
            name="docs",
            path=tmp_path / "docs",
            reason="missing optional project path",
        ),
    )


def test_partition_existing_projects_fails_required_missing_path(tmp_path: Path) -> None:
    backend = config.ProjectConfig(name="backend", path=tmp_path / "backend")

    with pytest.raises(project.ProjectError, match="project backend path not found"):
        project.partition_existing_projects((backend,))


def test_build_multi_project_report_builds_snapshot_rows(tmp_path: Path) -> None:
    report = sample_repository_report(tmp_path / "frontend")

    multi_report = project.build_multi_project_report(
        engine="python",
        project_reports=(ProjectReport(name="frontend", report=report),),
        skipped_projects=(),
    )

    assert multi_report.engine == "python"
    assert multi_report.snapshot_rows[0].name == "frontend"
    assert multi_report.snapshot_rows[0].files == 3
    assert multi_report.snapshot_rows[0].code == 30
    assert multi_report.snapshot_rows[0].source_code == 20
    assert multi_report.snapshot_rows[0].test_code == 10


def test_build_multi_project_report_sums_languages_when_sum_row_is_missing(tmp_path: Path) -> None:
    report = RepositoryReport(
        engine="python",
        path=tmp_path / "frontend",
        language_rows=(
            LanguageRow(language="Python", files=1, blank=0, comment=0, code=20),
            LanguageRow(language="Markdown", files=2, blank=0, comment=0, code=5),
        ),
        source_test_summary=SourceTestSummary(
            source_files=1,
            source_code=20,
            test_files=0,
            test_code=0,
        ),
        area_rows=(),
        directory_rows=(),
    )

    multi_report = project.build_multi_project_report(
        engine="python",
        project_reports=(ProjectReport(name="frontend", report=report),),
        skipped_projects=(),
    )

    assert multi_report.snapshot_rows[0].files == 3
    assert multi_report.snapshot_rows[0].code == 25
