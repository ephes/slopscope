from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from slopscope import cloc
from slopscope.report import FileRow, LanguageRow


def test_cloc_availability_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(executable: str) -> str:
        return f"/usr/bin/{executable}"

    monkeypatch.setattr("slopscope.cloc.shutil.which", fake_which)

    assert cloc.is_cloc_available()


def test_cloc_availability_detection_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(executable: str) -> None:
        return None

    monkeypatch.setattr("slopscope.cloc.shutil.which", fake_which)

    assert not cloc.is_cloc_available()


def test_build_language_summary_command() -> None:
    assert cloc.build_language_summary_command(Path(".")) == [
        "cloc",
        ".",
        "--vcs=git",
        "--csv",
        "--quiet",
    ]


def test_build_file_summary_command() -> None:
    assert cloc.build_file_summary_command(Path(".")) == [
        "cloc",
        ".",
        "--vcs=git",
        "--by-file",
        "--csv",
        "--quiet",
    ]


def test_build_file_summary_command_accepts_custom_executable() -> None:
    assert cloc.build_file_summary_command("src", executable="custom-cloc") == [
        "custom-cloc",
        "src",
        "--vcs=git",
        "--by-file",
        "--csv",
        "--quiet",
    ]


def test_run_file_summary_uses_file_summary_command(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run_command(command: Sequence[str]) -> cloc.ClocResult:
        calls.append(list(command))
        return cloc.ClocResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("slopscope.cloc.run_command", fake_run_command)

    result = cloc.run_file_summary("src", executable="custom-cloc")

    assert result == cloc.ClocResult(returncode=0, stdout="", stderr="")
    assert calls == [
        ["custom-cloc", "src", "--vcs=git", "--by-file", "--csv", "--quiet"],
    ]


def test_parse_language_summary_csv_preserves_sum_row() -> None:
    output = """language,filename,blank,comment,code
Python,2,10,4,90
Markdown,1,3,0,20
SUM,3,13,4,110
"""

    rows = cloc.parse_language_summary_csv(output)

    assert rows == [
        LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        LanguageRow(language="Markdown", files=1, blank=3, comment=0, code=20),
        LanguageRow(language="SUM", files=3, blank=13, comment=4, code=110),
    ]


def test_parse_language_summary_csv_accepts_files_column() -> None:
    output = (
        'files,language,blank,comment,code,"github.com/AlDanial/cloc v 2.08"\n'
        "2,Python,10,4,90\n"
        "1,TOML,3,0,20\n"
        "3,SUM,13,4,110\n"
    )

    rows = cloc.parse_language_summary_csv(output)

    assert rows == [
        LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        LanguageRow(language="TOML", files=1, blank=3, comment=0, code=20),
        LanguageRow(language="SUM", files=3, blank=13, comment=4, code=110),
    ]


def test_parse_language_summary_csv_skips_malformed_rows() -> None:
    output = """language,filename,blank,comment,code
Python,2,10,4,90
,1,1,1,1
JSON,not-an-int,0,0,10
Text,1,,0,10
SUM,2,10,4,100
"""

    rows = cloc.parse_language_summary_csv(output)

    assert rows == [
        LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        LanguageRow(language="SUM", files=2, blank=10, comment=4, code=100),
    ]


@pytest.mark.parametrize(
    "output",
    [
        "",
        "language,filename,blank,comment,code\n",
    ],
)
def test_parse_language_summary_csv_without_rows(output: str) -> None:
    assert cloc.parse_language_summary_csv(output) == []


def test_parse_file_summary_csv_returns_file_rows() -> None:
    output = """language,filename,blank,comment,code
Python,src/slopscope/cli.py,2,3,40
Markdown,README.md,4,0,20
SUM,,6,3,60
"""

    rows = cloc.parse_file_summary_csv(output)

    assert rows == [
        FileRow(language="Python", path="src/slopscope/cli.py", blank=2, comment=3, code=40),
        FileRow(language="Markdown", path="README.md", blank=4, comment=0, code=20),
    ]


def test_parse_file_summary_csv_accepts_cloc_metadata_column() -> None:
    output = (
        'language,filename,blank,comment,code,"github.com/AlDanial/cloc v 2.08"\n'
        "Python,src/slopscope/cli.py,2,3,40\n"
        "TOML,pyproject.toml,1,0,20\n"
        "SUM,,3,3,60\n"
    )

    rows = cloc.parse_file_summary_csv(output)

    assert rows == [
        FileRow(language="Python", path="src/slopscope/cli.py", blank=2, comment=3, code=40),
        FileRow(language="TOML", path="pyproject.toml", blank=1, comment=0, code=20),
    ]


def test_parse_file_summary_csv_skips_malformed_rows() -> None:
    output = """language,filename,blank,comment,code
Python,src/slopscope/cli.py,2,3,40
,src/unknown.py,1,1,1
JSON,,1,1,1
Text,docs/readme.txt,not-an-int,0,10
YAML,config.yml,1,,10
SUM,,3,4,60
Markdown,README.md,4,0,20
"""

    rows = cloc.parse_file_summary_csv(output)

    assert rows == [
        FileRow(language="Python", path="src/slopscope/cli.py", blank=2, comment=3, code=40),
        FileRow(language="Markdown", path="README.md", blank=4, comment=0, code=20),
    ]


@pytest.mark.parametrize(
    "output",
    [
        "",
        "language,filename,blank,comment,code\n",
    ],
)
def test_parse_file_summary_csv_without_rows(output: str) -> None:
    assert cloc.parse_file_summary_csv(output) == []
