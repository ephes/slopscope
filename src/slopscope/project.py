"""Configured project selection and multi-project report helpers."""

from __future__ import annotations

import os
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

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


@dataclass(frozen=True)
class AdmittedProject:
    name: str
    path: Path
    resolved_path: Path
    metadata: os.stat_result

    def verify_directory(self) -> Path:
        """Return the admitted scan root if the configured name still selects it."""
        message = (
            f"incomplete analysis: project {self.name} directory changed after admission: "
            f"{self.path}"
        )
        try:
            if self.path.resolve(strict=True) == self.resolved_path:
                current = self.resolved_path.stat()
                if stat.S_ISDIR(current.st_mode) and os.path.samestat(current, self.metadata):
                    return self.resolved_path
        except (OSError, RuntimeError) as exc:
            raise ProjectError(f"{message}: {exc}") from exc
        raise ProjectError(message)


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
) -> tuple[tuple[AdmittedProject, ...], tuple[SkippedProject, ...]]:
    """Split selected projects into existing required work and optional skips."""

    existing: list[AdmittedProject] = []
    skipped: list[SkippedProject] = []
    for configured_project in projects:
        path = configured_project.path
        label = f"project {configured_project.name}"
        try:
            metadata = path.stat()
        except (FileNotFoundError, NotADirectoryError) as exc:
            _verify_missing_project_path(path, label)
            if isinstance(exc, NotADirectoryError):
                raise ProjectError(f"{label} path has a non-directory component: {path}") from exc
            if not configured_project.optional:
                raise ProjectError(f"{label} path not found: {path}") from exc
            skipped.append(
                SkippedProject(
                    name=configured_project.name,
                    path=path,
                    reason="missing optional project path",
                )
            )
            continue
        except OSError as exc:
            raise ProjectError(
                f"incomplete analysis: could not stat {label} path {path}: {exc}"
            ) from exc

        if not stat.S_ISDIR(metadata.st_mode):
            raise ProjectError(f"{label} path is not a directory: {path}")
        try:
            resolved_path = path.resolve(strict=True)
            current = resolved_path.stat()
        except (OSError, RuntimeError) as exc:
            raise ProjectError(
                f"incomplete analysis: could not resolve {label} path {path}: {exc}"
            ) from exc
        if not stat.S_ISDIR(current.st_mode) or not os.path.samestat(current, metadata):
            raise ProjectError(
                f"incomplete analysis: {label} path changed during admission: {path}"
            )
        existing.append(AdmittedProject(configured_project.name, path, resolved_path, metadata))
    return tuple(existing), tuple(skipped)


def _verify_missing_project_path(path: Path, label: str) -> None:
    """Walk reachable prefixes to distinguish a missing entry from a broken link."""
    for component in (*reversed(path.parents), path):
        try:
            entry = component.lstat()
            if stat.S_ISLNK(entry.st_mode):
                try:
                    entry = component.stat()
                except (FileNotFoundError, NotADirectoryError) as exc:
                    raise ProjectError(
                        f"incomplete analysis: {label} path has a dangling symlink: {component}"
                    ) from exc
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ProjectError(
                f"incomplete analysis: could not inspect {label} path {path}: {exc}"
            ) from exc

        if component != path and not stat.S_ISDIR(entry.st_mode):
            raise ProjectError(f"{label} path has a non-directory component: {path}")
    raise ProjectError(f"incomplete analysis: {label} path changed during admission: {path}")


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
    if config_module.ALL_PROJECTS in requested_names and len(requested_names) > 1:
        raise ProjectError("--project all cannot be combined with named projects")

    seen: set[str] = set()
    for requested_name in requested_names:
        if requested_name in seen:
            raise ProjectError(f"duplicate project selected: {requested_name}")
        seen.add(requested_name)


def _requests_all(requested_names: Sequence[str]) -> bool:
    return len(requested_names) == 1 and requested_names[0] == config_module.ALL_PROJECTS


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
