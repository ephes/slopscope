from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from slopscope import cli, cloc
from slopscope.report import FileRow, LanguageRow, LanguageSummaryReport


def test_cli_smoke_with_cloc_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,2,3,4\nSUM,1,2,3,4\n",
            stderr="",
        )

    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,src/app.py,2,3,4\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr("slopscope.cli.cloc.run_file_summary", fake_run_file_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "cloc", "--format", "plain"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "Python" in stdout.getvalue()
    assert "SUM" in stdout.getvalue()
    assert "Repository Areas" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_auto_falls_back_to_python_when_cloc_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_is_cloc_available() -> bool:
        return False

    def fake_build_file_rows(_path: Path | str) -> list[FileRow]:
        return [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)]

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--format", "plain"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "Engine: python (physical lines)" in stdout.getvalue()
    assert "Python" in stdout.getvalue()
    assert "src" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_cloc_engine_still_fails_clearly_without_cloc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_is_cloc_available() -> bool:
        return False

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "cloc"], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "cloc engine requested, but cloc was not found" in stderr.getvalue()


def test_cli_surfaces_cloc_failure_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(returncode=7, stdout="", stderr="cloc exploded\n")

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr(
        "slopscope.cli.cloc.run_file_summary",
        lambda _path: cloc.ClocResult(returncode=0, stdout="", stderr=""),
    )
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

    assert "Language Summary" in stdout.getvalue()
    assert "Python               1        2        3        4" in stdout.getvalue()
    assert "SUM                  1        2        3        4" in stdout.getvalue()


def test_cli_python_engine_succeeds_with_physical_line_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_file_rows(_path: Path | str) -> list[FileRow]:
        return [FileRow(language="Markdown", path="README.md", blank=0, comment=0, code=2)]

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "python", "--format", "plain"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "Engine: python (physical lines)" in stdout.getvalue()
    assert "Markdown" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_json_output_has_no_human_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_build_file_rows(_path: Path | str) -> list[FileRow]:
        return [
            FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4),
            FileRow(language="Python", path="tests/test_app.py", blank=0, comment=0, code=2),
        ]

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "json"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    data = json.loads(stdout.getvalue())
    assert data["engine"] == "python"
    assert data["language_rows"][0]["language"] == "Python"
    assert data["source_test_summary"] == {
        "source_files": 1,
        "source_code": 4,
        "test_files": 1,
        "test_code": 2,
    }
    assert "Slopscope Report" not in stdout.getvalue()
    assert "Engine: python (physical lines)" not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_plain_format_works_without_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_build_file_rows(_path: Path | str) -> list[FileRow]:
        return [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)]

    def fail_import_rich(_name: str) -> object:
        raise AssertionError("plain rendering should not import rich")

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    monkeypatch.setattr("slopscope.render.importlib.import_module", fail_import_rich)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "plain"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Language Summary" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_rich_format_falls_back_to_plain_when_rich_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_file_rows(_path: Path | str) -> list[FileRow]:
        return [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)]

    def fake_import_module(name: str) -> object:
        if name.startswith("rich."):
            raise ImportError(name)
        raise AssertionError(name)

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "rich"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Slopscope Report" in stdout.getvalue()
    assert "Language Summary" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_no_color_uses_plain_human_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_build_file_rows(_path: Path | str) -> list[FileRow]:
        return [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)]

    def fail_import_rich(_name: str) -> object:
        raise AssertionError("--no-color should not import rich")

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    monkeypatch.setattr("slopscope.render.importlib.import_module", fail_import_rich)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "rich", "--no-color"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Slopscope Report" in stdout.getvalue()
    assert "\x1b[" not in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_cloc_path_invokes_language_and_file_summaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        calls.append("language")
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,0,0,10\nSUM,1,0,0,10\n",
            stderr="",
        )

    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        calls.append("file")
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,src/app.py,0,0,10\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr("slopscope.cli.cloc.run_file_summary", fake_run_file_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--format", "json"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert calls == ["language", "file"]
    assert json.loads(stdout.getvalue())["area_rows"] == [{"name": "src", "files": 1, "code": 10}]
    assert stderr.getvalue() == ""


def test_cli_cloc_file_summary_failure_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,0,0,10\nSUM,1,0,0,10\n",
            stderr="",
        )

    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(returncode=9, stdout="", stderr="file summary exploded\n")

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr("slopscope.cli.cloc.run_file_summary", fake_run_file_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "cloc"], stdout=stdout, stderr=stderr)

    assert exit_code == 9
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "file summary exploded\n"


def test_cli_cloc_empty_file_rows_render_empty_aggregate_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,0,0,10\nSUM,1,0,0,10\n",
            stderr="",
        )

    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr("slopscope.cli.cloc.run_file_summary", fake_run_file_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--format", "plain"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "(no file-level rows)" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_compatibility_entry_point_targets_same_callable() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    scripts = pyproject["project"]["scripts"]
    assert scripts["slopscope"] == "slopscope.cli:main"
    assert scripts["count-lines-of-code"] == scripts["slopscope"]
