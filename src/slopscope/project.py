"""Configured project selection and multi-project report helpers."""

from __future__ import annotations

from collections.abc import Sequence

from slopscope import config as config_module
from slopscope.report import (
    LanguageRow,
    MultiProjectReport,
    ProjectReport,
    ProjectSnapshotRow,
    RepositoryReport,
    SkippedProject,
)


class ProjectError(Exception):
    """User-facing project selection or execution error."""


def select_projects(
    slopscope_config: config_module.SlopscopeConfig,
    requested_names: Sequence[str],
) -> tuple[config_module.ProjectConfig, ...]:
    """Select configured projects by name, or all projects in config order."""

    if not requested_names:
        return ()

    _validate_requested_names(requested_names)
    configured_projects = slopscope_config.projects
    if not configured_projects:
        if _requests_all(requested_names):
            raise ProjectError("no configured projects")
        raise ProjectError(
            f"no configured project named {requested_names[0]!r} (no projects are configured)"
        )

    if _requests_all(requested_names):
        return configured_projects

    requested = set(requested_names)
    configured_names = {configured_project.name for configured_project in configured_projects}
    for requested_name in requested_names:
        if requested_name not in configured_names:
            raise ProjectError(f"no configured project named {requested_name!r}")

    return tuple(
        configured_project
        for configured_project in configured_projects
        if configured_project.name in requested
    )


def partition_existing_projects(
    projects: Sequence[config_module.ProjectConfig],
) -> tuple[tuple[config_module.ProjectConfig, ...], tuple[SkippedProject, ...]]:
    """Split selected projects into existing required work and optional skips."""

    existing: list[config_module.ProjectConfig] = []
    skipped: list[SkippedProject] = []
    for configured_project in projects:
        if configured_project.path.exists():
            existing.append(configured_project)
            continue
        if configured_project.optional:
            skipped.append(
                SkippedProject(
                    name=configured_project.name,
                    path=configured_project.path,
                    reason="missing optional project path",
                )
            )
            continue
        raise ProjectError(
            f"project {configured_project.name} path not found: {configured_project.path}"
        )
    return tuple(existing), tuple(skipped)


def build_multi_project_report(
    *,
    engine: str,
    project_reports: Sequence[ProjectReport],
    skipped_projects: Sequence[SkippedProject],
) -> MultiProjectReport:
    """Build a multi-project report from per-project repository reports."""

    reports = tuple(project_reports)
    return MultiProjectReport(
        engine=engine,
        projects=reports,
        snapshot_rows=tuple(
            _build_snapshot_row(project_report.name, project_report.report)
            for project_report in reports
        ),
        skipped_projects=tuple(skipped_projects),
    )


def _validate_requested_names(requested_names: Sequence[str]) -> None:
    if "all" in requested_names and len(requested_names) > 1:
        raise ProjectError("--project all cannot be combined with named projects")

    seen: set[str] = set()
    for requested_name in requested_names:
        if requested_name in seen:
            raise ProjectError(f"duplicate project selected: {requested_name}")
        seen.add(requested_name)


def _requests_all(requested_names: Sequence[str]) -> bool:
    return len(requested_names) == 1 and requested_names[0] == "all"


def _build_snapshot_row(name: str, report: RepositoryReport) -> ProjectSnapshotRow:
    total = _total_language_row(report.language_rows)
    summary = report.source_test_summary
    return ProjectSnapshotRow(
        name=name,
        path=report.path,
        engine=report.engine,
        files=total.files,
        code=total.code,
        source_code=summary.source_code,
        test_code=summary.test_code,
    )


def _total_language_row(rows: Sequence[LanguageRow]) -> LanguageRow:
    for row in rows:
        if row.language == "SUM":
            return row

    return LanguageRow(
        language="SUM",
        files=sum(row.files for row in rows),
        blank=sum(row.blank for row in rows),
        comment=sum(row.comment for row in rows),
        code=sum(row.code for row in rows),
    )
