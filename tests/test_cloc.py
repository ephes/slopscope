from __future__ import annotations

from pathlib import Path

import pytest

from slopscope import cloc


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


def test_parse_language_summary_csv_preserves_sum_row() -> None:
    output = """language,filename,blank,comment,code
Python,2,10,4,90
Markdown,1,3,0,20
SUM,3,13,4,110
"""

    rows = cloc.parse_language_summary_csv(output)

    assert rows == [
        cloc.LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        cloc.LanguageRow(language="Markdown", files=1, blank=3, comment=0, code=20),
        cloc.LanguageRow(language="SUM", files=3, blank=13, comment=4, code=110),
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
        cloc.LanguageRow(language="Python", files=2, blank=10, comment=4, code=90),
        cloc.LanguageRow(language="SUM", files=2, blank=10, comment=4, code=100),
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
