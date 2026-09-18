"""Render size-limits reports as plain text, Rich text, or JSON."""

from __future__ import annotations

import io
import json
from typing import Any

from slopscope.render import OutputFormat, _load_rich
from slopscope.report import (
    SIZE_LIMIT_KINDS,
    CompositionFailure,
    MultiProjectSizeLimitsReport,
    SizeLimitEntry,
    SizeLimitsReport,
    SkippedProject,
)
from slopscope.size_limits import SCHEMA_VERSION, SEED, UPDATE

RenderableSizeLimitsReport = SizeLimitsReport | MultiProjectSizeLimitsReport

RENAME_NOTE = "A renamed or moved unit appears as new over its limit plus a stale entry."
_KIND_LABELS = {"functions": "function", "classes": "class", "test_files": "test file"}
_KIND_PLURALS = {"functions": "functions", "classes": "classes", "test_files": "test files"}
_UNITS = {"functions": "span lines", "classes": "span lines", "test_files": "code lines"}


def render_size_limits_report(
    report: RenderableSizeLimitsReport,
    *,
    output_format: OutputFormat,
    color: bool = True,
) -> str:
    """Render a size-limits report in the selected output format."""

    if output_format == "json":
        return render_size_limits_json(report)
    if output_format == "plain":
        return render_size_limits_plain(report)
    return render_size_limits_rich(report, color=color)


def render_size_limits_json(report: RenderableSizeLimitsReport) -> str:
    """Render a size-limits report as stable JSON."""

    if isinstance(report, MultiProjectSizeLimitsReport):
        payload: dict[str, Any] = {
            "report_type": "size_limits_projects",
            "schema_version": SCHEMA_VERSION,
            "projects": [
                {
                    "name": project.name,
                    "path": str(project.report.path),
                    "report": _report_to_dict(project.report),
                }
                for project in report.projects
            ],
            "skipped_projects": [_skipped_to_dict(row) for row in report.skipped_projects],
        }
    else:
        payload = _report_to_dict(report)
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _report_to_dict(report: SizeLimitsReport) -> dict[str, Any]:
    return {
        "report_type": "size_limits",
        "schema_version": SCHEMA_VERSION,
        "path": str(report.path),
        "action": report.action,
        "limits": {kind: report.settings.limit(kind) for kind in SIZE_LIMIT_KINDS},
        "allowlist": {
            "path": str(report.allowlist_path),
            "existed": report.allowlist_existed,
            "entries": dict(zip(SIZE_LIMIT_KINDS, report.allowlist_entries, strict=True)),
        },
        "units": dict(zip(SIZE_LIMIT_KINDS, report.units, strict=True)),
        "check_failed": report.check_failed,
        "over_limit": [_entry_to_dict(entry) for entry in report.over_limit],
        "grown": [_entry_to_dict(entry) for entry in report.grown],
        "shrunk": [_entry_to_dict(entry) for entry in report.shrunk],
        "stale": [_entry_to_dict(entry) for entry in report.stale],
        "seeded": [_entry_to_dict(entry) for entry in report.seeded],
        "lowered": [_entry_to_dict(entry) for entry in report.lowered],
        "dropped": [_entry_to_dict(entry) for entry in report.dropped],
        "failures": [_failure_to_dict(failure) for failure in report.failures],
    }


def _entry_to_dict(entry: SizeLimitEntry) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "key": entry.key,
        "size": entry.size,
        "limit": entry.limit,
        "recorded": entry.recorded,
        "delta": entry.delta,
        "reason": entry.reason,
    }


def _failure_to_dict(failure: CompositionFailure) -> dict[str, str]:
    return {"path": failure.path, "error": failure.error}


def _skipped_to_dict(row: SkippedProject) -> dict[str, str]:
    return {"name": row.name, "path": str(row.path), "reason": row.reason}


def _size(value: int | None) -> str:
    return "-" if value is None else str(value)


def _delta(value: int | None) -> str:
    return "-" if value is None else f"{value:+d}"


# (title, columns, rows) for each report group, shared by the plain and Rich renderers.
Group = tuple[str, tuple[str, ...], list[tuple[str, ...]]]


def _groups(report: SizeLimitsReport) -> list[Group]:
    groups: list[Group] = []
    if report.action == SEED:
        groups.append(
            (
                "Seeded Allowlist Entries",
                ("Kind", "Size", "Limit", "Unit"),
                [_row(entry, entry.size, entry.limit) for entry in report.seeded],
            )
        )
    if report.action == UPDATE:
        groups.append(
            (
                "Lowered Allowlist Entries",
                ("Kind", "Was", "Now", "Unit"),
                [_row(entry, entry.recorded, entry.size) for entry in report.lowered],
            )
        )
        groups.append(
            (
                "Dropped Allowlist Entries",
                ("Kind", "Was", "Reason", "Unit"),
                [
                    (_KIND_LABELS[entry.kind], _size(entry.recorded), entry.reason or "", entry.key)
                    for entry in report.dropped
                ],
            )
        )
    groups.extend(
        [
            (
                "New Over Limit (not allowlisted)",
                ("Kind", "Size", "Limit", "Unit"),
                [_row(entry, entry.size, entry.limit) for entry in report.over_limit],
            ),
            (
                "Grown Past Allowlist",
                ("Kind", "Recorded", "Size", "Delta", "Unit"),
                [
                    (
                        _KIND_LABELS[entry.kind],
                        _size(entry.recorded),
                        _size(entry.size),
                        _delta(entry.delta),
                        entry.key,
                    )
                    for entry in report.grown
                ],
            ),
            (
                "Shrunk Below Allowlist",
                ("Kind", "Recorded", "Size", "Delta", "Unit"),
                [
                    (
                        _KIND_LABELS[entry.kind],
                        _size(entry.recorded),
                        _size(entry.size),
                        _delta(entry.delta),
                        entry.key,
                    )
                    for entry in report.shrunk
                ],
            ),
            (
                "Stale Allowlist Entries",
                ("Kind", "Recorded", "Size", "Reason", "Unit"),
                [
                    (
                        _KIND_LABELS[entry.kind],
                        _size(entry.recorded),
                        _size(entry.size),
                        entry.reason or "",
                        entry.key,
                    )
                    for entry in report.stale
                ],
            ),
        ]
    )
    return groups


def _row(entry: SizeLimitEntry, first: int | None, second: int | None) -> tuple[str, ...]:
    return (_KIND_LABELS[entry.kind], _size(first), _size(second), entry.key)


def _header_lines(report: SizeLimitsReport) -> list[str]:
    limits = ", ".join(
        f"{_KIND_PLURALS[kind]} {report.settings.limit(kind)} {_UNITS[kind]}"
        for kind in SIZE_LIMIT_KINDS
    )
    measured = ", ".join(
        f"{count} {_KIND_PLURALS[kind]}"
        for kind, count in zip(SIZE_LIMIT_KINDS, report.units, strict=True)
    )
    allowlist = str(report.allowlist_path)
    if not report.allowlist_existed and report.action != SEED:
        allowlist += " (not found; nothing is allowlisted)"
    return [
        f"Path: {report.path}",
        f"Limits: {limits}",
        f"Allowlist: {allowlist}",
        f"Measured: {measured}",
    ]


def _summary(report: SizeLimitsReport) -> str:
    return (
        f"{len(report.over_limit)} new over limit, {len(report.grown)} grown, "
        f"{len(report.shrunk)} shrunk, {len(report.stale)} stale"
    )


def render_size_limits_plain(report: RenderableSizeLimitsReport) -> str:
    """Render a size-limits report as deterministic plain text."""

    if isinstance(report, MultiProjectSizeLimitsReport):
        lines = ["Slopscope Size Limits (projects)", ""]
        for skipped in report.skipped_projects:
            lines.append(f"Skipped project {skipped.name}: {skipped.reason} ({skipped.path})")
        if report.skipped_projects:
            lines.append("")
        if not report.projects:
            lines.append("(no project reports)")
        for project in report.projects:
            lines.append(f"Project: {project.name}")
            lines.append(render_size_limits_plain(project.report).rstrip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    lines = ["Slopscope Size Limits", *_header_lines(report), ""]
    for title, columns, rows in _groups(report):
        lines.append(title)
        lines.append(
            "  ".join(
                f"{column:<10}" if index == 0 else f"{column:>9}"
                for index, column in enumerate(columns[:-1])
            )
            + "  "
            + columns[-1]
        )
        lines.append("-" * 60)
        if not rows:
            lines.append("(none)")
        for row in rows:
            lines.append(
                "  ".join(
                    f"{cell:<10}" if index == 0 else f"{cell:>9}"
                    for index, cell in enumerate(row[:-1])
                )
                + "  "
                + row[-1]
            )
        lines.append("")
    if report.failures:
        lines.append("Failures (units in these files were not measured)")
        lines.append("-" * 60)
        lines.extend(f"{failure.path}: {failure.error}" for failure in report.failures)
        lines.append("")
    lines.append(_summary(report))
    lines.append(RENAME_NOTE)
    lines.append("")
    return "\n".join(lines)


def render_size_limits_rich(report: RenderableSizeLimitsReport, *, color: bool = True) -> str:
    """Render with Rich when available, otherwise fall back to plain text."""

    if not color:
        return render_size_limits_plain(report)
    rich = _load_rich()
    if rich is None:
        return render_size_limits_plain(report)

    console_class, table_class, text_class = rich
    buffer = io.StringIO()
    console = console_class(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=120,
        legacy_windows=False,
    )
    if isinstance(report, MultiProjectSizeLimitsReport):
        console.print(text_class("Slopscope Size Limits (projects)", style="bold blue"))
        for skipped in report.skipped_projects:
            console.print(
                text_class(
                    f"Skipped project {skipped.name}: {skipped.reason} ({skipped.path})",
                    style="yellow",
                )
            )
        for project in report.projects:
            console.print()
            console.print(text_class(f"Project: {project.name}", style="bold blue"))
            _print_rich_report(console, table_class, text_class, project.report)
        return buffer.getvalue()

    console.print(text_class("Slopscope Size Limits", style="bold blue"))
    _print_rich_report(console, table_class, text_class, report)
    return buffer.getvalue()


def _print_rich_report(
    console: Any,
    table_class: Any,
    text_class: Any,
    report: SizeLimitsReport,
) -> None:
    for line in _header_lines(report):
        console.print(text_class(line, style="dim"))
    for title, columns, rows in _groups(report):
        console.print()
        failing = title.startswith(("New Over", "Grown")) and rows
        table = table_class(title=title, title_style="bold red" if failing else "bold blue")
        for index, column in enumerate(columns):
            if index == len(columns) - 1:
                table.add_column(column, style="cyan")
            elif index == 0:
                table.add_column(column)
            else:
                table.add_column(column, justify="right", style="green")
        if not rows:
            table.add_row("(none)", *([""] * (len(columns) - 1)))
        for row in rows:
            table.add_row(*row)
        console.print(table)
    if report.failures:
        console.print()
        table = table_class(
            title="Failures (units in these files were not measured)", title_style="bold red"
        )
        table.add_column("Path", style="cyan")
        table.add_column("Error")
        for failure in report.failures:
            table.add_row(failure.path, failure.error)
        console.print(table)
    console.print()
    console.print(text_class(_summary(report), style="red" if report.check_failed else "green"))
    console.print(text_class(RENAME_NOTE, style="dim"))
