"""Render repository reports as plain text, Rich text, or JSON."""

from __future__ import annotations

import importlib
import io
import json
from typing import Any, Literal

from slopscope.report import (
    AreaRow,
    DirectoryRow,
    GroupedProfileReport,
    GroupedRow,
    LanguageRow,
    MultiProjectReport,
    ProfileTotalReport,
    ProjectSnapshotRow,
    RepositoryReport,
    SkippedProject,
)

OutputFormat = Literal["rich", "plain", "json"]
RenderableReport = RepositoryReport | ProfileTotalReport | GroupedProfileReport | MultiProjectReport


def _is_profile_report(report: RenderableReport) -> bool:
    return isinstance(report, ProfileTotalReport | GroupedProfileReport)


def render_report(
    report: RenderableReport,
    *,
    output_format: OutputFormat,
    color: bool = True,
) -> str:
    """Render a repository or profile report in the selected output format."""

    if output_format == "json":
        return render_json(report)
    if output_format == "plain":
        return render_plain(report)
    return render_rich(report, color=color)


def render_plain(report: RenderableReport) -> str:
    """Render a repository or profile report as deterministic plain text."""

    if isinstance(report, ProfileTotalReport):
        return render_profile_total_plain(report)
    if isinstance(report, GroupedProfileReport):
        return render_grouped_profile_plain(report)
    if isinstance(report, MultiProjectReport):
        return render_multi_project_plain(report)

    lines = [
        "Slopscope Report",
        f"Path: {report.path}",
        f"Engine: {_engine_label(report.engine)}",
        "",
    ]
    lines.extend(_render_language_summary(report.language_rows))
    lines.append("")
    lines.extend(_render_source_tests(report))
    lines.append("")
    lines.extend(_render_named_rows("Repository Areas", "Area", report.area_rows))
    lines.append("")
    lines.extend(_render_named_rows("Directory Buckets", "Directory", report.directory_rows))
    lines.append("")
    return "\n".join(lines)


def render_json(report: RenderableReport) -> str:
    """Render a repository or profile report as stable JSON."""

    if isinstance(report, ProfileTotalReport):
        payload = {
            "engine": report.engine,
            "path": str(report.path),
            "profile": report.profile,
            "total": report.total,
            "physical_lines": report.physical_lines,
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"
    if isinstance(report, GroupedProfileReport):
        payload = {
            "engine": report.engine,
            "path": str(report.path),
            "profile": report.profile,
            "group_by": report.group_by,
            "top": report.top,
            "total": report.total,
            "physical_lines": report.physical_lines,
            "rows": [_grouped_row_to_dict(row) for row in report.rows],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"
    if isinstance(report, MultiProjectReport):
        payload = {
            "engine": report.engine,
            "projects": [
                {
                    "name": project_report.name,
                    "path": str(project_report.report.path),
                    "report": _repository_report_to_dict(project_report.report),
                }
                for project_report in report.projects
            ],
            "snapshot_rows": [_project_snapshot_row_to_dict(row) for row in report.snapshot_rows],
            "skipped_projects": [
                _skipped_project_to_dict(skipped_project)
                for skipped_project in report.skipped_projects
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=False) + "\n"

    payload = _repository_report_to_dict(report)
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _repository_report_to_dict(report: RepositoryReport) -> dict[str, Any]:
    return {
        "engine": report.engine,
        "path": str(report.path),
        "language_rows": [_language_row_to_dict(row) for row in report.language_rows],
        "source_test_summary": {
            "source_files": report.source_test_summary.source_files,
            "source_code": report.source_test_summary.source_code,
            "test_files": report.source_test_summary.test_files,
            "test_code": report.source_test_summary.test_code,
        },
        "area_rows": [_named_row_to_dict(row) for row in report.area_rows],
        "directory_rows": [_named_row_to_dict(row) for row in report.directory_rows],
    }


def render_rich(report: RenderableReport, *, color: bool = True) -> str:
    """Render with Rich when available, otherwise fall back to plain text."""

    if not color:
        return render_plain(report)

    rich = _load_rich()
    if rich is None:
        return render_plain(report)

    console_class, table_class, text_class = rich
    buffer = io.StringIO()
    console = console_class(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=100,
        legacy_windows=False,
    )

    if isinstance(report, MultiProjectReport):
        title = "Slopscope Projects"
    else:
        title = "Slopscope Profile" if _is_profile_report(report) else "Slopscope Report"
    console.print(text_class(title, style="bold blue"))
    console.print(text_class(f"Engine: {_engine_label(report.engine)}", style="dim"))
    if isinstance(report, MultiProjectReport):
        console.print()
        _print_rich_project_snapshot(console, table_class, report.snapshot_rows)
        if report.skipped_projects:
            console.print()
            _print_rich_skipped_projects(console, table_class, report.skipped_projects)
        for project_report in report.projects:
            console.print()
            console.print(text_class(f"Project: {project_report.name}", style="bold blue"))
            console.print(text_class(f"Path: {project_report.report.path}", style="dim"))
            _print_rich_repository_sections(console, table_class, project_report.report)
        return buffer.getvalue()

    console.print(text_class(f"Path: {report.path}", style="dim"))
    if isinstance(report, ProfileTotalReport):
        console.print(text_class(f"Profile: {report.profile}", style="dim"))
        console.print(text_class(f"Total: {report.total}", style="green"))
        return buffer.getvalue()
    if isinstance(report, GroupedProfileReport):
        console.print(text_class(f"Profile: {report.profile}", style="dim"))
        console.print(text_class(f"Group: {report.group_by}", style="dim"))
        console.print(text_class(f"Top: {_top_label(report.top)}", style="dim"))
        console.print(text_class(f"Total: {report.total}", style="green"))
        console.print()
        _print_rich_grouped_rows(console, table_class, report.rows)
        return buffer.getvalue()

    _print_rich_repository_sections(console, table_class, report)
    return buffer.getvalue()


def render_profile_total_plain(report: ProfileTotalReport) -> str:
    """Render a profile total report as deterministic plain text."""

    return "\n".join(
        [
            "Slopscope Profile",
            f"Path: {report.path}",
            f"Engine: {_engine_label(report.engine)}",
            f"Profile: {report.profile}",
            f"Total: {report.total}",
            "",
        ]
    )


def render_grouped_profile_plain(report: GroupedProfileReport) -> str:
    """Render a grouped profile report as deterministic plain text."""

    lines = [
        "Slopscope Profile",
        f"Path: {report.path}",
        f"Engine: {_engine_label(report.engine)}",
        f"Profile: {report.profile}",
        f"Group: {report.group_by}",
        f"Top: {_top_label(report.top)}",
        f"Total: {report.total}",
        "",
    ]
    lines.extend(_render_grouped_rows(report.rows))
    lines.append("")
    return "\n".join(lines)


def render_multi_project_plain(report: MultiProjectReport) -> str:
    """Render a multi-project report as deterministic plain text."""

    lines = [
        "Slopscope Projects",
        f"Engine: {_engine_label(report.engine)}",
        "",
    ]
    lines.extend(_render_project_snapshot(report.snapshot_rows))
    if report.skipped_projects:
        lines.append("")
        lines.extend(_render_skipped_projects(report.skipped_projects))
    lines.append("")
    lines.append("Per-Project Reports")
    if not report.projects:
        lines.append("(no project reports)")
    for project_report in report.projects:
        lines.append("")
        lines.append(f"Project: {project_report.name}")
        lines.append(render_plain(project_report.report).rstrip())
    lines.append("")
    return "\n".join(lines)


def _render_language_summary(rows: tuple[LanguageRow, ...]) -> list[str]:
    lines = [
        "Language Summary",
        "Language         Files    Blank  Comment     Code",
        "-------------------------------------------------",
    ]
    if not rows:
        lines.append("(no language rows)")
        return lines

    for row in rows:
        lines.append(
            f"{row.language:<16} {row.files:>5} {row.blank:>8} {row.comment:>8} {row.code:>8}"
        )
    return lines


def _render_source_tests(report: RepositoryReport) -> list[str]:
    summary = report.source_test_summary
    total = summary.source_code + summary.test_code
    return [
        "Source vs Tests",
        "Kind            Files     Code    Share",
        "---------------------------------------",
        _source_test_line("Source", summary.source_files, summary.source_code, total),
        _source_test_line("Tests", summary.test_files, summary.test_code, total),
        _source_test_line(
            "Source+Tests",
            summary.source_files + summary.test_files,
            total,
            total,
        ),
    ]


def _render_named_rows(
    title: str,
    name_column: str,
    rows: tuple[AreaRow, ...] | tuple[DirectoryRow, ...],
) -> list[str]:
    lines = [
        title,
        f"{name_column:<24} {'Files':>5} {'Code':>8}",
        "---------------------------------------",
    ]
    if not rows:
        lines.append("(no file-level rows)")
        return lines

    for row in rows:
        lines.append(f"{row.name:<24} {row.files:>5} {row.code:>8}")
    return lines


def _render_grouped_rows(rows: tuple[GroupedRow, ...]) -> list[str]:
    lines = [
        "Grouped Lines",
        f"{'Group':<24} {'Files':>5} {'Code':>8}",
        "---------------------------------------",
    ]
    if not rows:
        lines.append("(no grouped rows)")
        return lines

    for row in rows:
        lines.append(f"{row.name:<24} {row.files:>5} {row.code:>8}")
    return lines


def _render_project_snapshot(rows: tuple[ProjectSnapshotRow, ...]) -> list[str]:
    lines = [
        "Project Snapshot",
        f"{'Project':<16} {'Files':>5} {'Code':>8} {'Source':>8} {'Tests':>8} {'Test Share':>10}",
        "-----------------------------------------------------------------",
    ]
    if not rows:
        lines.append("(no project rows)")
        return lines

    for row in rows:
        total = row.source_code + row.test_code
        lines.append(
            f"{row.name:<16} {row.files:>5} {row.code:>8} "
            f"{row.source_code:>8} {row.test_code:>8} {_percent(row.test_code, total):>10}"
        )
        lines.append(f"  Path: {row.path}")
        lines.append(f"  Engine: {_engine_label(row.engine)}")
    return lines


def _render_skipped_projects(rows: tuple[SkippedProject, ...]) -> list[str]:
    lines = [
        "Skipped Projects",
        f"{'Project':<16} {'Reason':<32} Path",
        "-----------------------------------------------------------------",
    ]
    for row in rows:
        lines.append(f"{row.name:<16} {row.reason:<32} {row.path}")
    return lines


def _source_test_line(label: str, files: int, code: int, total: int) -> str:
    return f"{label:<16} {files:>5} {code:>8} {_percent(code, total):>7}"


def _percent(value: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(value / total) * 100:.1f}%"


def _engine_label(engine: str) -> str:
    if engine == "python":
        return "python (physical lines)"
    return engine


def _language_row_to_dict(row: LanguageRow) -> dict[str, int | str]:
    return {
        "language": row.language,
        "files": row.files,
        "blank": row.blank,
        "comment": row.comment,
        "code": row.code,
    }


def _named_row_to_dict(row: AreaRow | DirectoryRow) -> dict[str, int | str]:
    return {
        "name": row.name,
        "files": row.files,
        "code": row.code,
    }


def _grouped_row_to_dict(row: GroupedRow) -> dict[str, int | str]:
    return {
        "name": row.name,
        "files": row.files,
        "code": row.code,
    }


def _project_snapshot_row_to_dict(row: ProjectSnapshotRow) -> dict[str, int | str]:
    return {
        "name": row.name,
        "path": str(row.path),
        "engine": row.engine,
        "files": row.files,
        "code": row.code,
        "source_code": row.source_code,
        "test_code": row.test_code,
    }


def _skipped_project_to_dict(row: SkippedProject) -> dict[str, str]:
    return {
        "name": row.name,
        "path": str(row.path),
        "reason": row.reason,
    }


def _top_label(top: int | None) -> str:
    if top is None:
        return "all"
    return str(top)


def _load_rich() -> tuple[Any, Any, Any] | None:
    try:
        console_module = importlib.import_module("rich.console")
        table_module = importlib.import_module("rich.table")
        text_module = importlib.import_module("rich.text")
    except ImportError:
        return None
    return console_module.Console, table_module.Table, text_module.Text


def _print_rich_language_summary(
    console: Any,
    table_class: Any,
    rows: tuple[LanguageRow, ...],
) -> None:
    table = table_class(title="Language Summary", title_style="bold blue")
    table.add_column("Language", style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Blank", justify="right")
    table.add_column("Comment", justify="right")
    table.add_column("Code", justify="right", style="green")

    if rows:
        for row in rows:
            table.add_row(
                row.language,
                str(row.files),
                str(row.blank),
                str(row.comment),
                str(row.code),
            )
    else:
        table.add_row("(no language rows)", "", "", "", "")

    console.print(table)


def _print_rich_repository_sections(
    console: Any,
    table_class: Any,
    report: RepositoryReport,
) -> None:
    console.print()
    _print_rich_language_summary(console, table_class, report.language_rows)
    console.print()
    _print_rich_source_tests(console, table_class, report)
    console.print()
    _print_rich_named_rows(console, table_class, "Repository Areas", "Area", report.area_rows)
    console.print()
    _print_rich_named_rows(
        console,
        table_class,
        "Directory Buckets",
        "Directory",
        report.directory_rows,
    )


def _print_rich_project_snapshot(
    console: Any,
    table_class: Any,
    rows: tuple[ProjectSnapshotRow, ...],
) -> None:
    table = table_class(title="Project Snapshot", title_style="bold blue")
    table.add_column("Project", style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Code", justify="right", style="green")
    table.add_column("Source", justify="right", style="green")
    table.add_column("Tests", justify="right", style="green")
    table.add_column("Test Share", justify="right")

    if rows:
        for row in rows:
            total = row.source_code + row.test_code
            table.add_row(
                row.name,
                str(row.files),
                str(row.code),
                str(row.source_code),
                str(row.test_code),
                _percent(row.test_code, total),
            )
    else:
        table.add_row("(no project rows)", "", "", "", "", "")

    console.print(table)


def _print_rich_skipped_projects(
    console: Any,
    table_class: Any,
    rows: tuple[SkippedProject, ...],
) -> None:
    table = table_class(title="Skipped Projects", title_style="bold blue")
    table.add_column("Project", style="cyan")
    table.add_column("Reason")
    table.add_column("Path")

    for row in rows:
        table.add_row(row.name, row.reason, str(row.path))

    console.print(table)


def _print_rich_source_tests(console: Any, table_class: Any, report: RepositoryReport) -> None:
    summary = report.source_test_summary
    total = summary.source_code + summary.test_code
    table = table_class(title="Source vs Tests", title_style="bold blue")
    table.add_column("Kind", style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Code", justify="right", style="green")
    table.add_column("Share", justify="right")
    table.add_row(
        "Source",
        str(summary.source_files),
        str(summary.source_code),
        _percent(summary.source_code, total),
    )
    table.add_row(
        "Tests", str(summary.test_files), str(summary.test_code), _percent(summary.test_code, total)
    )
    table.add_row(
        "Source+Tests",
        str(summary.source_files + summary.test_files),
        str(total),
        _percent(total, total),
    )
    console.print(table)


def _print_rich_named_rows(
    console: Any,
    table_class: Any,
    title: str,
    name_column: str,
    rows: tuple[AreaRow, ...] | tuple[DirectoryRow, ...],
) -> None:
    table = table_class(title=title, title_style="bold blue")
    table.add_column(name_column, style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Code", justify="right", style="green")

    if rows:
        for row in rows:
            table.add_row(row.name, str(row.files), str(row.code))
    else:
        table.add_row("(no file-level rows)", "", "")

    console.print(table)


def _print_rich_grouped_rows(
    console: Any,
    table_class: Any,
    rows: tuple[GroupedRow, ...],
) -> None:
    table = table_class(title="Grouped Lines", title_style="bold blue")
    table.add_column("Group", style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Code", justify="right", style="green")

    if rows:
        for row in rows:
            table.add_row(row.name, str(row.files), str(row.code))
    else:
        table.add_row("(no grouped rows)", "", "")

    console.print(table)
