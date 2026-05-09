"""Command-line interface for slopscope."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from slopscope import classify, cloc, fallback, profile, render
from slopscope import config as config_module
from slopscope.report import (
    FileAggregateReport,
    FileRow,
    LanguageRow,
    LanguageSummaryReport,
    RepositoryReport,
)


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
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Read configuration from PATH instead of pyproject.toml under the inspected path.",
    )
    parser.add_argument(
        "--profile",
        metavar="NAME",
        help="Run the configured profile named NAME.",
    )
    parser.add_argument(
        "--total-only",
        action="store_true",
        help="Print only the selected profile total.",
    )
    parser.add_argument(
        "--top",
        metavar="N",
        type=_positive_int,
        help="Override a grouped profile top-N limit.",
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

    try:
        slopscope_config = _load_selected_config(
            selected_path=selected_path,
            explicit_config_path=args.config,
        )
    except config_module.ConfigError as exc:
        print(f"slopscope: {exc}", file=err)
        return 2

    if args.total_only and args.profile is None:
        print("slopscope: --total-only requires --profile", file=err)
        return 2

    if args.profile is not None:
        return _run_profile(
            args=args,
            path=selected_path,
            slopscope_config=slopscope_config,
            out=out,
            err=err,
        )

    if args.engine == "cloc" and not cloc.is_cloc_available():
        print("slopscope: cloc engine requested, but cloc was not found on PATH", file=err)
        return 2

    if args.engine == "python" or (args.engine == "auto" and not cloc.is_cloc_available()):
        report = _build_python_report(selected_path, slopscope_config)
    else:
        cloc_report = _build_cloc_report(selected_path, err, slopscope_config)
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


def _run_profile(
    *,
    args: argparse.Namespace,
    path: Path,
    slopscope_config: config_module.SlopscopeConfig,
    out: TextIO,
    err: TextIO,
) -> int:
    try:
        selected_profile = profile.find_profile(slopscope_config, args.profile)
    except profile.ProfileError as exc:
        print(f"slopscope: {exc}", file=err)
        return 2

    if selected_profile.physical_lines or args.engine == "python":
        profile_engine = "python"
    elif args.engine == "cloc":
        if not cloc.is_cloc_available():
            print("slopscope: cloc engine requested, but cloc was not found on PATH", file=err)
            return 2
        profile_engine = "cloc"
    elif cloc.is_cloc_available():
        profile_engine = "cloc"
    else:
        profile_engine = "python"

    try:
        report = profile.build_profile_report(
            path=path,
            slopscope_config=slopscope_config,
            selected_profile=selected_profile,
            engine=profile_engine,
            top=args.top,
        )
    except profile.ProfileError as exc:
        print(f"slopscope: {exc}", file=err)
        return 2
    except profile.ProfileCountError as exc:
        print(str(exc), file=err)
        return exc.returncode

    if args.total_only:
        out.write(f"{report.total}\n")
    else:
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


def _load_selected_config(
    *,
    selected_path: Path,
    explicit_config_path: str | None,
) -> config_module.SlopscopeConfig:
    if explicit_config_path is not None:
        return config_module.load_config_from_pyproject(Path(explicit_config_path))
    return config_module.load_config(config_module.find_default_config_path(selected_path))


def _build_python_report(
    path: Path, slopscope_config: config_module.SlopscopeConfig
) -> RepositoryReport:
    file_rows = fallback.build_file_rows(
        path,
        excluded_paths=_effective_fallback_excludes(slopscope_config),
        include_globs=slopscope_config.include_globs,
        include_languages=slopscope_config.include_languages,
        exclude_languages=slopscope_config.exclude_languages,
    )
    language_report = fallback.build_language_summary_from_file_rows(
        path=path,
        file_rows=file_rows,
    )
    aggregate_report = _build_aggregate_report(file_rows, slopscope_config)
    return RepositoryReport.from_reports(
        language_report=language_report,
        aggregate_report=aggregate_report,
    )


def _build_cloc_report(
    path: Path,
    err: TextIO,
    slopscope_config: config_module.SlopscopeConfig,
) -> RepositoryReport | int:
    language_result = cloc.run_language_summary(path)
    if language_result.returncode != 0:
        message = language_result.stderr.strip() or "cloc failed without stderr output"
        print(message, file=err)
        return language_result.returncode

    raw_language_rows = tuple(cloc.parse_language_summary_csv(language_result.stdout))
    if not raw_language_rows:
        print("slopscope: cloc returned no usable language rows", file=err)
        return 1

    file_result = cloc.run_file_summary(path)
    if file_result.returncode != 0:
        message = file_result.stderr.strip() or "cloc file summary failed without stderr output"
        print(message, file=err)
        return file_result.returncode

    file_rows = _filter_file_rows(
        cloc.parse_file_summary_csv(file_result.stdout),
        path=path,
        slopscope_config=slopscope_config,
    )
    if slopscope_config.exclude_dirs:
        language_rows = _language_rows_from_file_rows(file_rows)
    else:
        language_rows = _filter_language_rows(raw_language_rows, slopscope_config)

    language_report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=path,
        language_rows=language_rows,
    )
    aggregate_report = _build_aggregate_report(file_rows, slopscope_config)
    return RepositoryReport.from_reports(
        language_report=language_report,
        aggregate_report=aggregate_report,
    )


def _build_aggregate_report(
    file_rows: Sequence[FileRow],
    slopscope_config: config_module.SlopscopeConfig,
) -> FileAggregateReport:
    return classify.build_file_aggregate_report(
        file_rows,
        source_dirs=slopscope_config.source_dirs,
        test_dirs=slopscope_config.test_dirs,
        named_areas=slopscope_config.areas,
        nested_bucket_dirs=slopscope_config.nested_bucket_dirs,
    )


def _filter_file_rows(
    file_rows: Sequence[FileRow],
    *,
    path: Path,
    slopscope_config: config_module.SlopscopeConfig,
) -> tuple[FileRow, ...]:
    return tuple(
        row
        for row in file_rows
        if _language_is_included(row.language, slopscope_config)
        and not _row_is_excluded(row, path=path, slopscope_config=slopscope_config)
    )


def _filter_language_rows(
    language_rows: Sequence[LanguageRow],
    slopscope_config: config_module.SlopscopeConfig,
) -> tuple[LanguageRow, ...]:
    if not slopscope_config.include_languages and not slopscope_config.exclude_languages:
        return tuple(language_rows)

    rows = tuple(
        row
        for row in language_rows
        if row.language != "SUM" and _language_is_included(row.language, slopscope_config)
    )
    return _language_rows_with_sum(rows)


def _language_rows_from_file_rows(file_rows: Sequence[FileRow]) -> tuple[LanguageRow, ...]:
    totals: dict[str, tuple[int, int, int, int]] = {}
    for row in file_rows:
        files, blank, comment, code = totals.get(row.language, (0, 0, 0, 0))
        totals[row.language] = (
            files + 1,
            blank + row.blank,
            comment + row.comment,
            code + row.code,
        )

    rows = [
        LanguageRow(language=language, files=files, blank=blank, comment=comment, code=code)
        for language, (files, blank, comment, code) in totals.items()
    ]
    rows.sort(key=lambda row: (-row.code, row.language))
    return _language_rows_with_sum(tuple(rows))


def _language_rows_with_sum(language_rows: Sequence[LanguageRow]) -> tuple[LanguageRow, ...]:
    rows = tuple(language_rows)
    if not rows:
        return ()
    return (
        *rows,
        LanguageRow(
            language="SUM",
            files=sum(row.files for row in rows),
            blank=sum(row.blank for row in rows),
            comment=sum(row.comment for row in rows),
            code=sum(row.code for row in rows),
        ),
    )


def _language_is_included(
    language: str,
    slopscope_config: config_module.SlopscopeConfig,
) -> bool:
    if slopscope_config.include_languages and language not in slopscope_config.include_languages:
        return False
    return language not in slopscope_config.exclude_languages


def _row_is_excluded(
    row: FileRow,
    *,
    path: Path,
    slopscope_config: config_module.SlopscopeConfig,
) -> bool:
    if not slopscope_config.exclude_dirs:
        return False
    return fallback.is_excluded_path(
        _row_filter_path(row.path, root=path), slopscope_config.exclude_dirs
    )


def _row_filter_path(path: str, *, root: Path) -> Path:
    file_path = Path(path)
    if not file_path.is_absolute():
        return file_path
    try:
        return file_path.resolve().relative_to(root.resolve())
    except ValueError:
        return file_path


def _effective_fallback_excludes(
    slopscope_config: config_module.SlopscopeConfig,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (*sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS), *slopscope_config.exclude_dirs)
        )
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed
