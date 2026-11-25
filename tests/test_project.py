from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from slopscope import cloc, config, project
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
    frontend = config.ProjectConfig(name="frontend", path=existing_path, optional=True)
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
    language_rows = cloc.parse_language_summary_csv(
        "language,files,blank,comment,code\nPython,1,0,0,20\nMarkdown,2,0,0,5\n"
    )
    report = RepositoryReport(
        engine="cloc",
        path=tmp_path / "frontend",
        language_rows=tuple(language_rows),
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


@pytest.mark.parametrize("error_number", [errno.EACCES, errno.EIO])
def test_partition_existing_projects_reports_optional_stat_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_number: int
) -> None:
    path = tmp_path / "frontend"
    frontend = config.ProjectConfig(name="frontend", path=path, optional=True)
    original_stat = Path.stat

    def failing_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self == path:
            raise OSError(error_number, "injected stat failure", str(path))
        return original_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", failing_stat)

    with pytest.raises(project.ProjectError, match="project frontend.*injected stat failure"):
        project.partition_existing_projects((frontend,))


def test_partition_existing_projects_reports_non_directory_component(tmp_path: Path) -> None:
    parent = tmp_path / "file"
    parent.touch()
    docs = config.ProjectConfig(name="docs", path=parent / "docs", optional=True)

    with pytest.raises(project.ProjectError, match="project docs.*non-directory component"):
        project.partition_existing_projects((docs,))
