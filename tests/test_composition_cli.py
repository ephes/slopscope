from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

from slopscope import cli, composition, composition_render
from slopscope.report import COMPOSITION_CATEGORIES


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repository(root: Path) -> Path:
    write(
        root,
        "src/pkg/app.py",
        '"""App."""\nimport os\n\n\n'
        "class App:\n    def run(self):\n        return [\n            1,\n        ]\n",
    )
    write(root, "tests/test_app.py", "def test_run():\n    assert True\n")
    write(root, "docs/conf.py", "project = 'demo'\n")
    write(root, "docs/guide.md", "# Guide\n")
    return root


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.run(argv, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def test_composition_json_has_distinct_stable_shape(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    exit_code, stdout, stderr = run_cli(["--composition", "--format", "json", str(repository)])

    assert exit_code == 0
    assert stderr == ""
    data = json.loads(stdout)
    assert list(data) == [
        "report_type",
        "schema_version",
        "analyzer",
        "detectors",
        "path",
        "settings",
        "categories",
        "tags",
        "markers",
        "test_placements",
        "constructs",
        "total",
        "kinds",
        "areas",
        "largest_modules",
        "largest_classes",
        "largest_functions",
        "duplicates",
        "baseline",
        "churn",
        "files",
        "failures",
    ]
    assert data["report_type"] == "composition"
    assert data["schema_version"] == composition.SCHEMA_VERSION == 4
    assert data["analyzer"] == {
        "name": "slopscope.composition",
        "version": composition.ANALYZER_VERSION,
        "python_version": composition.python_version(),
    }
    assert data["categories"] == list(COMPOSITION_CATEGORIES)
    assert data["settings"]["limit"] == composition.DEFAULT_LIMIT
    assert data["settings"]["language"] == "Python"
    total = data["total"]
    assert list(total["categories"]) == list(COMPOSITION_CATEGORIES)
    assert total["categories"]["error_handling"] == 0
    assert total["categories"]["comment"] == 0
    assert total["physical"] == sum(total["categories"].values()) == 12
    assert total["files"] == 3
    assert [kind["name"] for kind in data["kinds"]] == ["source", "tests", "other"]
    assert [area["name"] for area in data["areas"]] == ["src", "tests", "docs"]
    assert data["largest_classes"][0]["qualified_name"] == "src/pkg/app.py:App"
    assert data["largest_functions"][0]["qualified_name"] == "src/pkg/app.py:App.run"
    assert data["files"][0]["path"] == "docs/conf.py"
    assert data["failures"] == []
    assert data["detectors"] == [
        {"name": "qt", "kind": "tag", "version": 1},
        {"name": "logging", "kind": "tag", "version": 1},
        {"name": "data_shape", "kind": "tag", "version": 1},
        {"name": "compat", "kind": "marker", "version": 1},
        {"name": "test_placement", "kind": "placement", "version": 1},
        {"name": "duplication", "kind": "duplication", "version": 1},
    ]
    assert data["settings"]["min_duplicate_tokens"] == 50
    assert total["duplicated_lines"] == 0
    assert data["duplicates"] == []
    assert data["baseline"] is None
    assert data["churn"] is None
    assert total["tags"] == {"qt": 0, "logging": 0, "data_shape": 0}
    assert total["markers"] == {"compat": 0}
    assert total["test_placement"] == {
        "test": 2,
        "fixture": 0,
        "setup": 0,
        "module_level": 0,
        "helper_or_unknown": 0,
    }
    assert total["constructs"] == {
        "classes": 1,
        "functions": 2,
        "data_shape_classes": 0,
        "qt_classes": 0,
        "tests": 1,
    }


def test_composition_json_is_deterministic(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    first = run_cli(["--composition", "--format", "json", str(repository)])
    second = run_cli(["--composition", "--format", "json", str(repository)])

    assert first == second


def test_composition_plain_report_sections(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    exit_code, stdout, stderr = run_cli(["--composition", "--format", "plain", str(repository)])

    assert exit_code == 0
    assert stderr == ""
    for heading in (
        "Slopscope Composition",
        "Line Categories",
        "Statements",
        "Areas",
        "Largest Modules (code lines)",
        "Largest Classes (span lines)",
        "Largest Functions (span lines)",
    ):
        assert heading in stdout
    for category in COMPOSITION_CATEGORIES:
        assert category in stdout
    assert "src/pkg/app.py:App.run (line 6)" in stdout
    assert "Failures" not in stdout


def test_composition_limit_limits_largest_lists(tmp_path: Path) -> None:
    for index in range(3):
        write(tmp_path, f"src/mod{index}.py", f"def f{index}():\n" + "    x = 1\n" * (index + 1))

    exit_code, stdout, _stderr = run_cli(
        ["--composition", "--limit", "2", "--format", "json", str(tmp_path)]
    )

    data = json.loads(stdout)
    assert exit_code == 0
    assert [row["path"] for row in data["largest_modules"]] == ["src/mod2.py", "src/mod1.py"]
    assert len(data["largest_functions"]) == 2
    assert len(data["files"]) == 3


def test_composition_failures_go_to_stderr_and_exit_non_zero(tmp_path: Path) -> None:
    write(tmp_path, "src/good.py", "x = 1\n")
    write(tmp_path, "src/broken.py", "def broken(:\n")
    (tmp_path / "src" / "latin.py").write_bytes(b'NAME = "\xff"\n')

    exit_code, stdout, stderr = run_cli(["--composition", "--format", "json", str(tmp_path)])

    assert exit_code == 1
    data = json.loads(stdout)
    assert [failure["path"] for failure in data["failures"]] == ["src/broken.py", "src/latin.py"]
    assert data["total"]["files"] == 1
    assert f"could not analyze {tmp_path / 'src/broken.py'}: syntax error" in stderr
    assert f"could not analyze {tmp_path / 'src/latin.py'}: decode error" in stderr


def test_composition_plain_lists_failures(tmp_path: Path) -> None:
    write(tmp_path, "src/broken.py", "def broken(:\n")

    exit_code, stdout, _stderr = run_cli(["--composition", "--format", "plain", str(tmp_path)])

    assert exit_code == 1
    assert "Files: 0 analyzed, 1 failed" in stdout
    assert "src/broken.py: syntax error" in stdout


def test_composition_uses_configured_excludes_and_classification(tmp_path: Path) -> None:
    write(tmp_path, "lib/core.py", "x = 1\n")
    write(tmp_path, "checks/check_core.py", "assert True\n")
    write(tmp_path, "generated/out.py", "x = 1\n")
    write(
        tmp_path,
        "pyproject.toml",
        """
[tool.slopscope]
exclude_dirs = ["generated"]
source_dirs = ["lib"]
test_dirs = ["checks"]
""",
    )

    exit_code, stdout, _stderr = run_cli(["--composition", "--format", "json", str(tmp_path)])

    data = json.loads(stdout)
    assert exit_code == 0
    assert [(row["path"], row["kind"]) for row in data["files"]] == [
        ("checks/check_core.py", "tests"),
        ("lib/core.py", "source"),
    ]
    assert "generated" in data["settings"]["excluded_paths"]
    assert data["settings"]["source_dirs"] == ["lib"]


@pytest.mark.parametrize(
    ("extra_args", "flag"),
    [
        (["--engine", "python"], "--engine"),
        (["--engine", "auto"], "--engine"),
        (["--profile", "yaml"], "--profile"),
        (["--total-only"], "--total-only"),
        (["--top", "3"], "--top"),
    ],
)
def test_composition_rejects_conflicting_options(
    extra_args: list[str],
    flag: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.run(["--composition", *extra_args])

    assert exc_info.value.code == 2
    assert f"--composition cannot be combined with {flag}" in capsys.readouterr().err


def test_limit_requires_composition(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.run(["--limit", "3"])

    assert exc_info.value.code == 2
    assert "--limit requires --composition" in capsys.readouterr().err


def test_limit_must_be_positive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.run(["--composition", "--limit", "0"])

    assert "must be a positive integer" in capsys.readouterr().err


def test_composition_path_named_composition_is_still_a_path(tmp_path: Path) -> None:
    write(tmp_path, "composition/src/app.py", "x = 1\n")

    exit_code, stdout, _stderr = run_cli(
        ["--composition", "--format", "json", str(tmp_path / "composition")]
    )

    assert exit_code == 0
    assert json.loads(stdout)["files"][0]["path"] == "src/app.py"


def test_composition_projects_with_optional_skip(tmp_path: Path) -> None:
    write(tmp_path, "frontend/src/ui.py", "import os\n")
    write(tmp_path, "backend/src/api.py", "x = 1\ny = 2\n")
    config_path = tmp_path / "workspace.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"

[[tool.slopscope.projects]]
name = "backend"
path = "backend"

[[tool.slopscope.projects]]
name = "mobile"
path = "mobile"
optional = true
""",
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--config", str(config_path), "--project", "all", "--format", "json"]
    )

    assert exit_code == 0
    data = json.loads(stdout)
    assert data["report_type"] == "composition_projects"
    assert data["schema_version"] == composition.SCHEMA_VERSION == 4
    assert [project["name"] for project in data["projects"]] == ["frontend", "backend"]
    assert data["projects"][0]["report"]["report_type"] == "composition"
    assert data["projects"][0]["report"]["total"]["categories"]["import"] == 1
    assert data["projects"][1]["report"]["total"]["statements"] == 2
    assert [skipped["name"] for skipped in data["skipped_projects"]] == ["mobile"]
    assert "skipping optional project mobile" in stderr


def test_composition_projects_plain_and_failures(tmp_path: Path) -> None:
    write(tmp_path, "one/src/ok.py", "x = 1\n")
    write(tmp_path, "two/src/bad.py", "def broken(:\n")
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[[tool.slopscope.projects]]
name = "one"
path = "one"

[[tool.slopscope.projects]]
name = "two"
path = "two"
""",
        encoding="utf-8",
    )

    exit_code, stdout, stderr = run_cli(
        [
            "--composition",
            "--project",
            "one",
            "--project",
            "two",
            "--format",
            "plain",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    assert "Slopscope Composition Projects" in stdout
    assert "Project: one" in stdout
    assert "Project: two" in stdout
    assert "project two: could not analyze" in stderr


def test_composition_unknown_project_fails(tmp_path: Path) -> None:
    exit_code, _stdout, stderr = run_cli(["--composition", "--project", "missing", str(tmp_path)])

    assert exit_code == 2
    assert "no configured project named 'missing'" in stderr


def block_rich(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        if name.startswith("rich."):
            raise ImportError(name)
        return real_import_module(name, package)

    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)


def test_composition_rich_falls_back_to_plain_without_rich(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    _code, plain, _err = run_cli(["--composition", "--format", "plain", str(repository)])
    block_rich(monkeypatch)

    exit_code, rich_output, _stderr = run_cli(["--composition", str(repository)])

    assert exit_code == 0
    assert rich_output == plain


def test_composition_no_color_uses_plain(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    _code, plain, _err = run_cli(["--composition", "--format", "plain", str(repository)])

    _exit_code, output, _stderr = run_cli(["--composition", "--no-color", str(repository)])

    assert output == plain


def test_composition_rich_output_uses_rich_when_available(tmp_path: Path) -> None:
    pytest.importorskip("rich")
    repository = make_repository(tmp_path)

    exit_code, output, _stderr = run_cli(["--composition", str(repository)])

    assert exit_code == 0
    assert "\x1b[" in output
    assert "Line Categories" in output
    assert "Largest Functions (span lines)" in output


def test_multi_project_rich_falls_back_to_plain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    write(tmp_path, "one/src/ok.py", "x = 1\n")
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n', encoding="utf-8"
    )
    report_args = ["--composition", "--project", "all", str(tmp_path)]
    _code, plain, _err = run_cli([*report_args, "--format", "plain"])
    block_rich(monkeypatch)

    assert run_cli(report_args)[1] == plain


def test_render_composition_report_dispatches_formats(tmp_path: Path) -> None:
    report = composition.build_composition_report(
        make_repository(tmp_path), excluded_paths=(".git",)
    )

    as_json = composition_render.render_composition_report(report, output_format="json")
    as_plain = composition_render.render_composition_report(report, output_format="plain")

    assert json.loads(as_json)["report_type"] == "composition"
    assert as_plain.startswith("Slopscope Composition\n")


# Captured from the pre-composition renderer (main) on the same fixture.
DEFAULT_PLAIN_TEMPLATE = """Slopscope Report
Path: {path}
Engine: python (physical lines)

Language Summary
Language         Files    Blank  Comment     Code
-------------------------------------------------
Python               3        0        0       12
Markdown             1        0        0        1
SUM                  4        0        0       13

Source vs Tests
Kind            Files     Code    Share
---------------------------------------
Source               1        9   81.8%
Tests                1        2   18.2%
Source+Tests         2       11  100.0%

Repository Areas
Area                     Files     Code
---------------------------------------
src                          1        9
docs                         2        2
tests                        1        2

Directory Buckets
Directory                Files     Code
---------------------------------------
src/pkg                      1        9
docs                         2        2
tests                        1        2
"""


def test_default_report_is_unchanged_by_composition_mode(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    plain_exit, plain, plain_err = run_cli(
        ["--engine", "python", "--format", "plain", str(repository)]
    )
    json_exit, as_json, _json_err = run_cli(
        ["--engine", "python", "--format", "json", str(repository)]
    )

    assert (plain_exit, plain_err) == (0, "")
    assert plain == DEFAULT_PLAIN_TEMPLATE.format(path=repository)
    assert json_exit == 0
    assert json.loads(as_json) == {
        "engine": "python",
        "path": str(repository),
        "language_rows": [
            {"language": "Python", "files": 3, "blank": 0, "comment": 0, "code": 12},
            {"language": "Markdown", "files": 1, "blank": 0, "comment": 0, "code": 1},
            {"language": "SUM", "files": 4, "blank": 0, "comment": 0, "code": 13},
        ],
        "source_test_summary": {
            "source_files": 1,
            "source_code": 9,
            "test_files": 1,
            "test_code": 2,
        },
        "area_rows": [
            {"name": "src", "files": 1, "code": 9},
            {"name": "docs", "files": 2, "code": 2},
            {"name": "tests", "files": 1, "code": 2},
        ],
        "directory_rows": [
            {"name": "src/pkg", "files": 1, "code": 9},
            {"name": "docs", "files": 2, "code": 2},
            {"name": "tests", "files": 1, "code": 2},
        ],
    }


def test_console_entry_point_supports_composition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # ``slopscope`` and ``count-lines-of-code`` both point at ``slopscope.cli:main``.
    repository = make_repository(tmp_path)

    exit_code = cli.main(["--composition", "--format", "json", str(repository)])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["report_type"] == "composition"
