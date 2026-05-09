from __future__ import annotations

import io
import tomllib
from pathlib import Path

import pytest

from slopscope import cli, cloc
from slopscope.report import LanguageRow, LanguageSummaryReport


def test_cli_smoke_with_cloc_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,2,3,4\nSUM,1,2,3,4\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "cloc"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "Python" in stdout.getvalue()
    assert "SUM" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_auto_fails_clearly_without_cloc(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return False

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run([], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "python fallback is not implemented yet" in stderr.getvalue()


def test_cli_surfaces_cloc_failure_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(returncode=7, stdout="", stderr="cloc exploded\n")

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run([], stdout=stdout, stderr=stderr)

    assert exit_code == 7
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "cloc exploded\n"


def test_cli_fails_when_cloc_returns_no_usable_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run([], stdout=stdout, stderr=stderr)

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert "no usable language rows" in stderr.getvalue()


def test_language_summary_rendering_uses_report_rows() -> None:
    stdout = io.StringIO()
    language_report = LanguageSummaryReport.from_rows(
        engine="cloc",
        path=Path("."),
        language_rows=[
            LanguageRow(language="Python", files=1, blank=2, comment=3, code=4),
            LanguageRow(language="SUM", files=1, blank=2, comment=3, code=4),
        ],
    )

    cli._print_language_summary(language_report, stdout)

    assert stdout.getvalue() == (
        "Language          Files    Blank  Comment     Code\n"
        "--------------------------------------------------\n"
        "Python               1        2        3        4\n"
        "SUM                  1        2        3        4\n"
    )


def test_cli_python_engine_fails_clearly() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "python"], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "python engine is not implemented yet" in stderr.getvalue()


def test_compatibility_entry_point_targets_same_callable() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    scripts = pyproject["project"]["scripts"]
    assert scripts["slopscope"] == "slopscope.cli:main"
    assert scripts["count-lines-of-code"] == scripts["slopscope"]
