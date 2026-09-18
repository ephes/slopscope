"""Render Python composition reports as plain text, Rich text, or JSON."""

from __future__ import annotations

import io
import json
from typing import Any

from slopscope.render import OutputFormat, _load_rich, _percent
from slopscope.report import (
    COMPOSITION_CATEGORIES,
    COMPOSITION_CONSTRUCTS,
    COMPOSITION_MARKERS,
    COMPOSITION_TAGS,
    COMPOSITION_TEST_PLACEMENTS,
    CompositionAggregate,
    CompositionClassRow,
    CompositionCounts,
    CompositionDetector,
    CompositionFailure,
    CompositionFileRow,
    CompositionFunctionRow,
    CompositionReport,
    MultiProjectCompositionReport,
    SkippedProject,
)

RenderableCompositionReport = CompositionReport | MultiProjectCompositionReport


def render_composition_report(
    report: RenderableCompositionReport,
    *,
    output_format: OutputFormat,
    color: bool = True,
) -> str:
    """Render a composition report in the selected output format."""

    if output_format == "json":
        return render_composition_json(report)
    if output_format == "plain":
        return render_composition_plain(report)
    return render_composition_rich(report, color=color)


def render_composition_json(report: RenderableCompositionReport) -> str:
    """Render a composition report as stable JSON."""

    if isinstance(report, MultiProjectCompositionReport):
        payload: dict[str, Any] = {
            "report_type": "composition_projects",
            "schema_version": report.schema_version,
            "analyzer": {
                "name": report.analyzer,
                "version": report.analyzer_version,
                "python_version": report.python_version,
            },
            "detectors": [_detector_to_dict(detector) for detector in report.detectors],
            "projects": [
                {
                    "name": project.name,
                    "path": str(project.report.path),
                    "report": _composition_report_to_dict(project.report),
                }
                for project in report.projects
            ],
            "skipped_projects": [
                _skipped_project_to_dict(skipped) for skipped in report.skipped_projects
            ],
        }
    else:
        payload = _composition_report_to_dict(report)
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def render_composition_plain(report: RenderableCompositionReport) -> str:
    """Render a composition report as deterministic plain text."""

    if isinstance(report, MultiProjectCompositionReport):
        return _render_multi_project_plain(report)

    lines = [
        "Slopscope Composition",
        f"Path: {report.path}",
        f"Analyzer: {_analyzer_label(report.analyzer_version, report.python_version)}",
        f"Files: {report.total.files} analyzed, {len(report.failures)} failed",
        "",
    ]
    lines.extend(_plain_categories(report))
    lines.append("")
    lines.extend(_plain_tags(report))
    lines.append("")
    lines.extend(_plain_statements("Statements", "Kind", (*report.kinds, report.total)))
    lines.append("")
    lines.extend(_plain_constructs((*report.kinds, report.total)))
    lines.append("")
    lines.extend(_plain_test_placement(report))
    lines.append("")
    lines.extend(_plain_statements("Areas", "Area", report.areas))
    lines.append("")
    lines.extend(_plain_modules(report.largest_modules))
    lines.append("")
    lines.extend(_plain_classes(report.largest_classes))
    lines.append("")
    lines.extend(_plain_functions(report.largest_functions))
    if report.failures:
        lines.append("")
        lines.extend(_plain_failures(report.failures))
    lines.append("")
    return "\n".join(lines)


def render_composition_rich(report: RenderableCompositionReport, *, color: bool = True) -> str:
    """Render with Rich when available, otherwise fall back to plain text."""

    if not color:
        return render_composition_plain(report)

    rich = _load_rich()
    if rich is None:
        return render_composition_plain(report)

    console_class, table_class, text_class = rich
    buffer = io.StringIO()
    console = console_class(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=120,
        legacy_windows=False,
    )

    if isinstance(report, MultiProjectCompositionReport):
        console.print(text_class("Slopscope Composition Projects", style="bold blue"))
        console.print(
            text_class(
                f"Analyzer: {_analyzer_label(report.analyzer_version, report.python_version)}",
                style="dim",
            )
        )
        console.print()
        _print_rich_project_snapshot(console, table_class, report)
        if report.skipped_projects:
            console.print()
            _print_rich_skipped_projects(console, table_class, report.skipped_projects)
        for project in report.projects:
            console.print()
            console.print(text_class(f"Project: {project.name}", style="bold blue"))
            _print_rich_report(console, table_class, text_class, project.report)
        return buffer.getvalue()

    console.print(text_class("Slopscope Composition", style="bold blue"))
    _print_rich_report(console, table_class, text_class, report)
    return buffer.getvalue()


def _composition_report_to_dict(report: CompositionReport) -> dict[str, Any]:
    settings = report.settings
    return {
        "report_type": "composition",
        "schema_version": report.schema_version,
        "analyzer": {
            "name": report.analyzer,
            "version": report.analyzer_version,
            "python_version": report.python_version,
        },
        "detectors": [_detector_to_dict(detector) for detector in report.detectors],
        "path": str(report.path),
        "settings": {
            "language": "Python",
            "limit": settings.limit,
            "excluded_paths": list(settings.excluded_paths),
            "include_globs": list(settings.include_globs),
            "source_dirs": list(settings.source_dirs),
            "test_dirs": list(settings.test_dirs),
            "areas": list(settings.areas),
        },
        "categories": list(COMPOSITION_CATEGORIES),
        "tags": list(COMPOSITION_TAGS),
        "markers": list(COMPOSITION_MARKERS),
        "test_placements": list(COMPOSITION_TEST_PLACEMENTS),
        "constructs": list(COMPOSITION_CONSTRUCTS),
        "total": _aggregate_to_dict(report.total),
        "kinds": [_aggregate_to_dict(aggregate) for aggregate in report.kinds],
        "areas": [_aggregate_to_dict(aggregate) for aggregate in report.areas],
        "largest_modules": [_module_to_dict(row) for row in report.largest_modules],
        "largest_classes": [_class_to_dict(row) for row in report.largest_classes],
        "largest_functions": [_function_to_dict(row) for row in report.largest_functions],
        "files": [_file_to_dict(row) for row in report.files],
        "failures": [_failure_to_dict(failure) for failure in report.failures],
    }


def _counts_to_dict(counts: CompositionCounts) -> dict[str, Any]:
    ratio = counts.code_per_statement
    return {
        "physical": counts.physical,
        "code": counts.code,
        "statements": counts.statements,
        "continuation_lines": counts.continuation_lines,
        "code_per_statement": None if ratio is None else round(ratio, 2),
        "categories": counts.as_mapping(),
        "tags": counts.tag_mapping(),
        "markers": counts.marker_mapping(),
        "test_placement": counts.placement_mapping(),
        "constructs": counts.construct_mapping(),
    }


def _aggregate_to_dict(aggregate: CompositionAggregate) -> dict[str, Any]:
    return {"name": aggregate.name, "files": aggregate.files, **_counts_to_dict(aggregate.counts)}


def _file_to_dict(row: CompositionFileRow) -> dict[str, Any]:
    return {"path": row.path, "kind": row.kind, "area": row.area, **_counts_to_dict(row.counts)}


def _module_to_dict(row: CompositionFileRow) -> dict[str, Any]:
    return {
        "path": row.path,
        "kind": row.kind,
        "code": row.counts.code,
        "physical": row.counts.physical,
        "statements": row.counts.statements,
    }


def _class_to_dict(row: CompositionClassRow) -> dict[str, Any]:
    return {
        "path": row.path,
        "name": row.name,
        "qualified_name": row.qualified_name,
        "line": row.line,
        "lines": row.lines,
        "methods": row.methods,
        "kind": row.kind,
    }


def _function_to_dict(row: CompositionFunctionRow) -> dict[str, Any]:
    return {
        "path": row.path,
        "name": row.name,
        "qualified_name": row.qualified_name,
        "line": row.line,
        "lines": row.lines,
        "kind": row.kind,
    }


def _detector_to_dict(detector: CompositionDetector) -> dict[str, str | int]:
    return {"name": detector.name, "kind": detector.kind, "version": detector.version}


def _failure_to_dict(failure: CompositionFailure) -> dict[str, str]:
    return {"path": failure.path, "error": failure.error}


def _skipped_project_to_dict(row: SkippedProject) -> dict[str, str]:
    return {"name": row.name, "path": str(row.path), "reason": row.reason}


def _analyzer_label(version: int, python_version: str) -> str:
    return f"composition v{version} (Python {python_version} ast)"


def _ratio(counts: CompositionCounts) -> str:
    ratio = counts.code_per_statement
    return "-" if ratio is None else f"{ratio:.2f}"


def _category_columns(report: CompositionReport) -> tuple[CompositionAggregate, ...]:
    return (report.total, *report.kinds)


def _plain_categories(report: CompositionReport) -> list[str]:
    columns = _category_columns(report)
    total_physical = report.total.counts.physical
    header = f"{'Category':<16} {'Total':>9} {'Share':>7}" + "".join(
        f" {column.name.capitalize():>9}" for column in columns[1:]
    )
    rule = "-" * len(header)
    lines = ["Line Categories", header, rule]
    for name in COMPOSITION_CATEGORIES:
        value = report.total.counts.category(name)
        lines.append(
            f"{name:<16} {value:>9} {_percent(value, total_physical):>7}"
            + "".join(f" {column.counts.category(name):>9}" for column in columns[1:])
        )
    lines.append(rule)
    for label, attribute in (("physical", "physical"), ("code", "code")):
        value = getattr(report.total.counts, attribute)
        lines.append(
            f"{label:<16} {value:>9} {_percent(value, total_physical):>7}"
            + "".join(f" {getattr(column.counts, attribute):>9}" for column in columns[1:])
        )
    return lines


def _tests_aggregate(report: CompositionReport) -> CompositionAggregate:
    return next(kind for kind in report.kinds if kind.name == "tests")


def _tag_rows(report: CompositionReport) -> list[tuple[str, int, list[int]]]:
    columns = _category_columns(report)[1:]
    rows = [
        (name, report.total.counts.tag(name), [column.counts.tag(name) for column in columns])
        for name in COMPOSITION_TAGS
    ]
    rows.extend(
        (
            f"{name} (marker)",
            report.total.counts.marker(name),
            [column.counts.marker(name) for column in columns],
        )
        for name in COMPOSITION_MARKERS
    )
    return rows


def _plain_tags(report: CompositionReport) -> list[str]:
    columns = _category_columns(report)[1:]
    total_code = report.total.counts.code
    header = f"{'Tag':<16} {'Lines':>9} {'Share':>7}" + "".join(
        f" {column.name.capitalize():>9}" for column in columns
    )
    lines = ["Semantic Tags (code lines; tags overlap)", header, "-" * len(header)]
    for name, total, values in _tag_rows(report):
        lines.append(
            f"{name:<16} {total:>9} {_percent(total, total_code):>7}"
            + "".join(f" {value:>9}" for value in values)
        )
    return lines


def _plain_constructs(rows: tuple[CompositionAggregate, ...]) -> list[str]:
    header = f"{'Kind':<16}" + "".join(f" {name:>18}" for name in COMPOSITION_CONSTRUCTS)
    lines = ["Constructs", header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row.name:<16}"
            + "".join(f" {row.counts.construct(name):>18}" for name in COMPOSITION_CONSTRUCTS)
        )
    return lines


def _tests_summary(tests: CompositionAggregate) -> str:
    count = tests.counts.construct("tests")
    if not count:
        return "No test functions or methods found."
    return f"{count} tests, {tests.counts.code / count:.1f} test-file code lines per test"


def _plain_test_placement(report: CompositionReport) -> list[str]:
    tests = _tests_aggregate(report)
    header = f"{'Placement':<18} {'Lines':>9} {'Share':>7}"
    lines = ["Test Placement (test-file code lines)", header, "-" * len(header)]
    for name in COMPOSITION_TEST_PLACEMENTS:
        value = tests.counts.placement(name)
        lines.append(f"{name:<18} {value:>9} {_percent(value, tests.counts.code):>7}")
    lines.append(_tests_summary(tests))
    return lines


def _plain_statements(
    title: str,
    name_column: str,
    rows: tuple[CompositionAggregate, ...],
) -> list[str]:
    header = (
        f"{name_column:<16} {'Files':>5} {'Physical':>9} {'Code':>9} {'Statements':>10} "
        f"{'Code/Stmt':>9} {'Continuation':>12} {'Cont.%':>7}"
    )
    lines = [title, header, "-" * len(header)]
    if not rows:
        lines.append("(no analyzed files)")
        return lines
    for row in rows:
        counts = row.counts
        lines.append(
            f"{row.name:<16} {row.files:>5} {counts.physical:>9} {counts.code:>9} "
            f"{counts.statements:>10} {_ratio(counts):>9} {counts.continuation_lines:>12} "
            f"{_percent(counts.continuation_lines, counts.code):>7}"
        )
    return lines


def _plain_modules(rows: tuple[CompositionFileRow, ...]) -> list[str]:
    header = f"{'Code':>8} {'Physical':>9} {'Statements':>10} {'Kind':<6} Module"
    lines = ["Largest Modules (code lines)", header, "-" * 60]
    if not rows:
        lines.append("(no analyzed files)")
    for row in rows:
        lines.append(
            f"{row.counts.code:>8} {row.counts.physical:>9} {row.counts.statements:>10} "
            f"{row.kind:<6} {row.path}"
        )
    return lines


def _plain_classes(rows: tuple[CompositionClassRow, ...]) -> list[str]:
    header = f"{'Lines':>8} {'Methods':>7} {'Kind':<6} Class"
    lines = ["Largest Classes (span lines)", header, "-" * 60]
    if not rows:
        lines.append("(no classes)")
    for row in rows:
        lines.append(
            f"{row.lines:>8} {row.methods:>7} {row.kind:<6} {row.qualified_name} (line {row.line})"
        )
    return lines


def _plain_functions(rows: tuple[CompositionFunctionRow, ...]) -> list[str]:
    header = f"{'Lines':>8} {'Kind':<6} Function"
    lines = ["Largest Functions (span lines)", header, "-" * 60]
    if not rows:
        lines.append("(no functions)")
    for row in rows:
        lines.append(f"{row.lines:>8} {row.kind:<6} {row.qualified_name} (line {row.line})")
    return lines


def _plain_failures(failures: tuple[CompositionFailure, ...]) -> list[str]:
    lines = ["Failures", "-" * 60]
    for failure in failures:
        lines.append(f"{failure.path}: {failure.error}")
    return lines


def _render_multi_project_plain(report: MultiProjectCompositionReport) -> str:
    lines = [
        "Slopscope Composition Projects",
        f"Analyzer: {_analyzer_label(report.analyzer_version, report.python_version)}",
        "",
    ]
    lines.extend(_plain_project_snapshot(report))
    if report.skipped_projects:
        lines.append("")
        lines.append("Skipped Projects")
        lines.append(f"{'Project':<16} {'Reason':<32} Path")
        lines.append("-" * 65)
        for skipped in report.skipped_projects:
            lines.append(f"{skipped.name:<16} {skipped.reason:<32} {skipped.path}")
    lines.append("")
    lines.append("Per-Project Reports")
    if not report.projects:
        lines.append("(no project reports)")
    for project in report.projects:
        lines.append("")
        lines.append(f"Project: {project.name}")
        lines.append(render_composition_plain(project.report).rstrip())
    lines.append("")
    return "\n".join(lines)


def _plain_project_snapshot(report: MultiProjectCompositionReport) -> list[str]:
    header = (
        f"{'Project':<16} {'Files':>5} {'Physical':>9} {'Code':>9} {'Statements':>10} "
        f"{'Code/Stmt':>9} {'Failures':>8}"
    )
    lines = ["Project Snapshot", header, "-" * len(header)]
    if not report.projects:
        lines.append("(no project rows)")
    for project in report.projects:
        total = project.report.total
        lines.append(
            f"{project.name:<16} {total.files:>5} {total.counts.physical:>9} "
            f"{total.counts.code:>9} {total.counts.statements:>10} {_ratio(total.counts):>9} "
            f"{len(project.report.failures):>8}"
        )
        lines.append(f"  Path: {project.report.path}")
    return lines


def _print_rich_report(
    console: Any,
    table_class: Any,
    text_class: Any,
    report: CompositionReport,
) -> None:
    console.print(text_class(f"Path: {report.path}", style="dim"))
    console.print(
        text_class(
            f"Analyzer: {_analyzer_label(report.analyzer_version, report.python_version)}",
            style="dim",
        )
    )
    console.print(
        text_class(
            f"Files: {report.total.files} analyzed, {len(report.failures)} failed",
            style="red" if report.failures else "dim",
        )
    )
    console.print()
    _print_rich_categories(console, table_class, report)
    console.print()
    _print_rich_tags(console, table_class, report)
    console.print()
    _print_rich_statements(
        console, table_class, "Statements", "Kind", (*report.kinds, report.total)
    )
    console.print()
    _print_rich_constructs(console, table_class, (*report.kinds, report.total))
    console.print()
    _print_rich_test_placement(console, table_class, text_class, report)
    console.print()
    _print_rich_statements(console, table_class, "Areas", "Area", report.areas)
    console.print()
    _print_rich_modules(console, table_class, report.largest_modules)
    console.print()
    _print_rich_classes(console, table_class, report.largest_classes)
    console.print()
    _print_rich_functions(console, table_class, report.largest_functions)
    if report.failures:
        console.print()
        table = table_class(title="Failures", title_style="bold red")
        table.add_column("Path", style="cyan")
        table.add_column("Error")
        for failure in report.failures:
            table.add_row(failure.path, failure.error)
        console.print(table)


def _print_rich_categories(console: Any, table_class: Any, report: CompositionReport) -> None:
    columns = _category_columns(report)
    total_physical = report.total.counts.physical
    table = table_class(title="Line Categories", title_style="bold blue")
    table.add_column("Category", style="cyan")
    table.add_column("Total", justify="right", style="green")
    table.add_column("Share", justify="right")
    for column in columns[1:]:
        table.add_column(column.name.capitalize(), justify="right", style="green")
    for name in COMPOSITION_CATEGORIES:
        value = report.total.counts.category(name)
        table.add_row(
            name,
            str(value),
            _percent(value, total_physical),
            *(str(column.counts.category(name)) for column in columns[1:]),
        )
    for label in ("physical", "code"):
        value = getattr(report.total.counts, label)
        table.add_row(
            label,
            str(value),
            _percent(value, total_physical),
            *(str(getattr(column.counts, label)) for column in columns[1:]),
            style="bold",
        )
    console.print(table)


def _print_rich_tags(console: Any, table_class: Any, report: CompositionReport) -> None:
    columns = _category_columns(report)[1:]
    total_code = report.total.counts.code
    table = table_class(title="Semantic Tags (code lines; tags overlap)", title_style="bold blue")
    table.add_column("Tag", style="cyan")
    table.add_column("Lines", justify="right", style="green")
    table.add_column("Share", justify="right")
    for column in columns:
        table.add_column(column.name.capitalize(), justify="right", style="green")
    for name, total, values in _tag_rows(report):
        table.add_row(name, str(total), _percent(total, total_code), *(str(v) for v in values))
    console.print(table)


def _print_rich_constructs(
    console: Any,
    table_class: Any,
    rows: tuple[CompositionAggregate, ...],
) -> None:
    table = table_class(title="Constructs", title_style="bold blue")
    table.add_column("Kind", style="cyan")
    for name in COMPOSITION_CONSTRUCTS:
        table.add_column(name, justify="right", style="magenta")
    for row in rows:
        table.add_row(
            row.name, *(str(row.counts.construct(name)) for name in COMPOSITION_CONSTRUCTS)
        )
    console.print(table)


def _print_rich_test_placement(
    console: Any,
    table_class: Any,
    text_class: Any,
    report: CompositionReport,
) -> None:
    tests = _tests_aggregate(report)
    table = table_class(title="Test Placement (test-file code lines)", title_style="bold blue")
    table.add_column("Placement", style="cyan")
    table.add_column("Lines", justify="right", style="green")
    table.add_column("Share", justify="right")
    for name in COMPOSITION_TEST_PLACEMENTS:
        value = tests.counts.placement(name)
        table.add_row(name, str(value), _percent(value, tests.counts.code))
    console.print(table)
    console.print(text_class(_tests_summary(tests), style="dim"))


def _print_rich_statements(
    console: Any,
    table_class: Any,
    title: str,
    name_column: str,
    rows: tuple[CompositionAggregate, ...],
) -> None:
    table = table_class(title=title, title_style="bold blue")
    table.add_column(name_column, style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Physical", justify="right", style="green")
    table.add_column("Code", justify="right", style="green")
    table.add_column("Statements", justify="right", style="green")
    table.add_column("Code/Stmt", justify="right")
    table.add_column("Continuation", justify="right")
    table.add_column("Cont.%", justify="right")
    if not rows:
        table.add_row("(no analyzed files)", "", "", "", "", "", "", "")
    for row in rows:
        counts = row.counts
        table.add_row(
            row.name,
            str(row.files),
            str(counts.physical),
            str(counts.code),
            str(counts.statements),
            _ratio(counts),
            str(counts.continuation_lines),
            _percent(counts.continuation_lines, counts.code),
        )
    console.print(table)


def _print_rich_modules(
    console: Any,
    table_class: Any,
    rows: tuple[CompositionFileRow, ...],
) -> None:
    table = table_class(title="Largest Modules (code lines)", title_style="bold blue")
    table.add_column("Module", style="cyan")
    table.add_column("Kind")
    table.add_column("Code", justify="right", style="green")
    table.add_column("Physical", justify="right", style="green")
    table.add_column("Statements", justify="right", style="green")
    if not rows:
        table.add_row("(no analyzed files)", "", "", "", "")
    for row in rows:
        table.add_row(
            row.path,
            row.kind,
            str(row.counts.code),
            str(row.counts.physical),
            str(row.counts.statements),
        )
    console.print(table)


def _print_rich_classes(
    console: Any,
    table_class: Any,
    rows: tuple[CompositionClassRow, ...],
) -> None:
    table = table_class(title="Largest Classes (span lines)", title_style="bold blue")
    table.add_column("Class", style="cyan")
    table.add_column("Line", justify="right", style="dim")
    table.add_column("Kind")
    table.add_column("Lines", justify="right", style="green")
    table.add_column("Methods", justify="right", style="magenta")
    if not rows:
        table.add_row("(no classes)", "", "", "", "")
    for row in rows:
        table.add_row(row.qualified_name, str(row.line), row.kind, str(row.lines), str(row.methods))
    console.print(table)


def _print_rich_functions(
    console: Any,
    table_class: Any,
    rows: tuple[CompositionFunctionRow, ...],
) -> None:
    table = table_class(title="Largest Functions (span lines)", title_style="bold blue")
    table.add_column("Function", style="cyan")
    table.add_column("Line", justify="right", style="dim")
    table.add_column("Kind")
    table.add_column("Lines", justify="right", style="green")
    if not rows:
        table.add_row("(no functions)", "", "", "")
    for row in rows:
        table.add_row(row.qualified_name, str(row.line), row.kind, str(row.lines))
    console.print(table)


def _print_rich_project_snapshot(
    console: Any,
    table_class: Any,
    report: MultiProjectCompositionReport,
) -> None:
    table = table_class(title="Project Snapshot", title_style="bold blue")
    table.add_column("Project", style="cyan")
    table.add_column("Files", justify="right", style="magenta")
    table.add_column("Physical", justify="right", style="green")
    table.add_column("Code", justify="right", style="green")
    table.add_column("Statements", justify="right", style="green")
    table.add_column("Code/Stmt", justify="right")
    table.add_column("Failures", justify="right")
    if not report.projects:
        table.add_row("(no project rows)", "", "", "", "", "", "")
    for project in report.projects:
        total = project.report.total
        table.add_row(
            project.name,
            str(total.files),
            str(total.counts.physical),
            str(total.counts.code),
            str(total.counts.statements),
            _ratio(total.counts),
            str(len(project.report.failures)),
        )
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
