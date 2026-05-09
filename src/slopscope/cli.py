"""Command-line interface for slopscope."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from slopscope import cloc
from slopscope.report import LanguageSummaryReport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slopscope",
        description="Print a repository language summary.",
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

    if args.engine == "python":
        print("slopscope: python engine is not implemented yet", file=err)
        return 2

    if args.engine == "auto" and not cloc.is_cloc_available():
        print(
            "slopscope: cloc is not available and the python fallback is not implemented yet",
            file=err,
        )
        return 2

    if args.engine == "cloc" and not cloc.is_cloc_available():
        print("slopscope: cloc engine requested, but cloc was not found on PATH", file=err)
        return 2

    result = cloc.run_language_summary(selected_path)
    if result.returncode != 0:
        message = result.stderr.strip() or "cloc failed without stderr output"
        print(message, file=err)
        return result.returncode

    report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=selected_path,
        language_rows=cloc.parse_language_summary_csv(result.stdout),
    )
    if not report.language_rows:
        print("slopscope: cloc returned no usable language rows", file=err)
        return 1

    _print_language_summary(report, out)
    return 0


def _print_language_summary(report: LanguageSummaryReport, out: TextIO) -> None:
    print("Language          Files    Blank  Comment     Code", file=out)
    print("--------------------------------------------------", file=out)
    for row in report.language_rows:
        print(
            f"{row.language:<16} {row.files:>5} {row.blank:>8} {row.comment:>8} {row.code:>8}",
            file=out,
        )
