"""Synthetic CLI cloc identity and bounded-count contracts."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from slopscope import cli, cloc


def csv_files(names: list[str], *, code: int = 1) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["language", "filename", "blank", "comment", "code"])
    for name in names:
        writer.writerow(["Python", name, 0, 0, code])
    return output.getvalue()


def invoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str, *, profile: bool = False
) -> tuple[int, str, str]:
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    monkeypatch.setattr(
        cloc,
        "run_language_summary",
        lambda _path: cloc.ClocResult(
            0, "language,filename,blank,comment,code\nPython,1,0,0,1\nSUM,1,0,0,1\n", ""
        ),
    )
    monkeypatch.setattr(cloc, "run_file_summary", lambda _path: cloc.ClocResult(0, output, ""))
    options = [str(tmp_path), "--engine", "cloc", "--format", "json"]
    if profile:
        settings = tmp_path / "pyproject.toml"
        previous = settings.read_text() if settings.exists() else ""
        settings.write_text(previous + '\n[[tool.slopscope.profiles]]\nname = "all"\n')
        options += ["--profile", "all", "--total-only"]
    stdout, stderr = io.StringIO(), io.StringIO()
    code = cli.run(options, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize(
    "case", ["repeat", "alias", "dots", "outside", "foreign", "filtered", "link-parent"]
)
def test_cli_refuses_invalid_file_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool, case: str
) -> None:
    names = {
        "repeat": ["src/app.py", "src/app.py"],
        "alias": ["src/app.py", str(tmp_path / "src/app.py")],
        "dots": ["src/app.py", "src/./app.py"],
        "outside": ["../outside.py"],
        "foreign": [str(tmp_path.parent / "outside.py")],
        "filtered": ["../outside.py"],
        "link-parent": ["linked/../outside.py"],
    }[case]
    if case == "link-parent":
        (tmp_path / "linked").symlink_to(tmp_path.parent / "foreign" / "nested")
    if case == "filtered":
        (tmp_path / "pyproject.toml").write_text(
            '[tool.slopscope]\nexclude_languages = ["Python"]\n'
        )
    result, out, err = invoke(tmp_path, monkeypatch, csv_files(names), profile=profile)
    assert result == 2
    assert out == ""
    assert "file identity" in err
    assert len(err) < 200


@pytest.mark.parametrize("profile", [False, True])
def test_cli_retains_unusual_valid_file_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool
) -> None:
    result, out, err = invoke(
        tmp_path, monkeypatch, csv_files(["src/line\nbreak.py"]), profile=profile
    )
    assert result == 0, err
    if profile:
        assert out == "1\n"
    else:
        assert json.loads(out)["language_rows"][-1]["code"] == 1


def test_cli_refuses_ambiguous_csv_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result, out, err = invoke(
        tmp_path,
        monkeypatch,
        "language,filename,blank,comment,code,code\nPython,app.py,0,0,999,1\n",
    )
    assert result == 2
    assert out == ""
    assert "columns" in err


@pytest.mark.parametrize("profile", [False, True])
def test_csv_overflow_publishes_no_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool
) -> None:
    monkeypatch.setattr(cloc, "MAX_CLOC_CSV_ROWS", 2, raising=False)
    result, out, err = invoke(
        tmp_path, monkeypatch, csv_files(["a.py", "b.py", "c.py"]), profile=profile
    )
    assert result == 2
    assert out == ""
    assert "row limit" in err


@pytest.mark.parametrize("filtered", [False, True])
def test_all_counts_project_one_file_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filtered: bool
) -> None:
    captures: list[str] = []

    def language_capture(_path: Path | str) -> cloc.ClocResult:
        captures.append("language")
        return cloc.ClocResult(
            0, "language,filename,blank,comment,code\nPython,99,0,0,999\nSUM,99,0,0,999\n", ""
        )

    def file_capture(_path: Path | str) -> cloc.ClocResult:
        captures.append("file")
        return cloc.ClocResult(
            0,
            "language,filename,blank,comment,code\n"
            "Python,src/app.py,1,2,7\nPython,tests/test_app.py,2,3,4\n"
            "Markdown,README.md,0,0,3\nSUM,,999,999,999\n",
            "",
        )

    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    monkeypatch.setattr(cloc, "run_language_summary", language_capture)
    monkeypatch.setattr(cloc, "run_file_summary", file_capture)
    if filtered:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.slopscope]\ninclude_languages = ["Python"]\n'
        )
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.run([str(tmp_path), "--engine", "cloc", "--format", "json"], stdout=out, stderr=err)
        == 0
    ), err.getvalue()
    data = json.loads(out.getvalue())
    expected = 11 if filtered else 14
    assert data["language_rows"][-1] == {
        "language": "SUM",
        "files": 2 if filtered else 3,
        "blank": 3,
        "comment": 5,
        "code": expected,
    }
    assert sum(row["code"] for row in data["directory_rows"]) == expected
    assert sum(row["code"] for row in data["area_rows"]) == expected
    assert data["source_test_summary"] == {
        "source_files": 1,
        "source_code": 7,
        "test_files": 1,
        "test_code": 4,
    }
    assert captures == ["file"]
