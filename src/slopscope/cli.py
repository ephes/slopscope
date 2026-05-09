"""Command-line interface for slopscope."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from slopscope import classify, cloc, fallback, render
from slopscope.report import FileAggregateReport, LanguageSummaryReport, RepositoryReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slopscope",
        description="Print a repository line-count report.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Repository path to inspect.",
    )
    parser.add_argument(
        "--engine",
        choices=("auto", "cloc", "python"),
        default="auto",
        help="Counting engine to use.",
    )
    parser.add_argument(
        "--format",
        choices=("rich", "plain", "json"),
        default="rich",
        help="Output format to use.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable color in human-readable output.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point."""

    return run(argv=argv)


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI with injectable streams for tests."""

    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    args = build_parser().parse_args(argv)
    selected_path = Path(args.path)

    if args.engine == "cloc" and not cloc.is_cloc_available():
        print("slopscope: cloc engine requested, but cloc was not found on PATH", file=err)
        return 2

    if args.engine == "python" or (args.engine == "auto" and not cloc.is_cloc_available()):
        report = _build_python_report(selected_path)
    else:
        cloc_report = _build_cloc_report(selected_path, err)
        if isinstance(cloc_report, int):
            return cloc_report
        report = cloc_report

    out.write(
        render.render_report(
            report,
            output_format=args.format,
            color=not args.no_color,
        )
    )
    return 0


def _print_language_summary(report: LanguageSummaryReport, out: TextIO) -> None:
    """Render a plain report from a language-only report for old internal callers."""

    aggregate = FileAggregateReport(
        source_tests=classify.aggregate_source_tests(()),
        area_rows=(),
        directory_rows=(),
    )
    repository_report = RepositoryReport.from_reports(
        language_report=report,
        aggregate_report=aggregate,
    )
    out.write(render.render_plain(repository_report))


def _build_python_report(path: Path) -> RepositoryReport:
    file_rows = fallback.build_file_rows(path)
    language_report = fallback.build_language_summary_from_file_rows(
        path=path,
        file_rows=file_rows,
    )
    aggregate_report = classify.build_file_aggregate_report(file_rows)
    return RepositoryReport.from_reports(
        language_report=language_report,
        aggregate_report=aggregate_report,
    )


def _build_cloc_report(path: Path, err: TextIO) -> RepositoryReport | int:
    language_result = cloc.run_language_summary(path)
    if language_result.returncode != 0:
        message = language_result.stderr.strip() or "cloc failed without stderr output"
        print(message, file=err)
        return language_result.returncode

    language_report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=path,
        language_rows=cloc.parse_language_summary_csv(language_result.stdout),
    )
    if not language_report.language_rows:
        print("slopscope: cloc returned no usable language rows", file=err)
        return 1

    file_result = cloc.run_file_summary(path)
    if file_result.returncode != 0:
        message = file_result.stderr.strip() or "cloc file summary failed without stderr output"
        print(message, file=err)
        return file_result.returncode

    aggregate_report = classify.build_file_aggregate_report(
        cloc.parse_file_summary_csv(file_result.stdout)
    )
    return RepositoryReport.from_reports(
        language_report=language_report,
        aggregate_report=aggregate_report,
    )
