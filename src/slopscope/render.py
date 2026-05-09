"""Render repository reports as plain text, Rich text, or JSON."""

from __future__ import annotations

import importlib
import io
import json
from typing import Any, Literal

from slopscope.report import AreaRow, DirectoryRow, LanguageRow, RepositoryReport

OutputFormat = Literal["rich", "plain", "json"]


def render_report(
    report: RepositoryReport,
    *,
    output_format: OutputFormat,
    color: bool = True,
) -> str:
    """Render a repository report in the selected output format."""

    if output_format == "json":
        return render_json(report)
    if output_format == "plain":
        return render_plain(report)
    return render_rich(report, color=color)


def render_plain(report: RepositoryReport) -> str:
    """Render a repository report as deterministic plain text."""

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


def render_json(report: RepositoryReport) -> str:
    """Render a repository report as stable JSON."""

    payload = {
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
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def render_rich(report: RepositoryReport, *, color: bool = True) -> str:
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

    console.print(text_class("Slopscope Report", style="bold blue"))
    console.print(text_class(f"Path: {report.path}", style="dim"))
    console.print(text_class(f"Engine: {_engine_label(report.engine)}", style="dim"))
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
    return buffer.getvalue()


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
