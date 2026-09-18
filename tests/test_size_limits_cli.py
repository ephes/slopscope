from __future__ import annotations

import importlib
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

from slopscope import cli

CONFIG = """
[tool.slopscope.size_limits]
max_function_lines = 3
max_class_lines = 8
max_test_file_code_lines = 4
"""


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.run(argv, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_project(root: Path, *, config: str = CONFIG) -> Path:
    write(root, "pyproject.toml", config)
    write(root, "src/big.py", "def big():\n" + "    x = 1\n" * 5)
    write(root, "src/fine.py", "def fine():\n    return 1\n")
    write(root, "tests/test_big.py", "".join(f"x{index} = {index}\n" for index in range(6)))
    return root


def test_report_lists_offenders_and_exits_0(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    exit_code, stdout, stderr = run_cli(["--size-limits", "--format", "plain", str(project)])

    assert exit_code == 0
    assert stderr == ""
    assert "New Over Limit (not allowlisted)" in stdout
    assert "src/big.py::big" in stdout
    assert "tests/test_big.py" in stdout
    assert "(not found; nothing is allowlisted)" in stdout
    assert "2 new over limit, 0 grown, 0 shrunk, 0 stale" in stdout
    assert "A renamed or moved unit appears as new over its limit plus a stale entry." in stdout


def test_strict_exit_codes(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    assert run_cli(["--size-limits", "--strict", str(project)])[0] == 1
    assert run_cli(["--size-limits", "--seed-allowlist", str(project)])[0] == 0
    assert run_cli(["--size-limits", "--strict", str(project)])[0] == 0

    write(project, "src/big.py", "def big():\n" + "    x = 1\n" * 7)
    exit_code, stdout, _stderr = run_cli(
        ["--size-limits", "--strict", "--format", "plain", str(project)]
    )
    assert exit_code == 1
    assert "Grown Past Allowlist" in stdout
    assert "+2" in stdout

    write(project, "src/big.py", "def big():\n" + "    x = 1\n" * 4)
    exit_code, stdout, _stderr = run_cli(
        ["--size-limits", "--strict", "--format", "plain", str(project)]
    )
    assert exit_code == 0
    assert "-1" in stdout


def test_seed_update_and_refusals_through_the_cli(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    allowlist = project / "size-limits.json"

    assert run_cli(["--size-limits", "--update-allowlist", str(project)])[0] == 2
    exit_code, stdout, _stderr = run_cli(
        ["--size-limits", "--seed-allowlist", "--format", "plain", str(project)]
    )
    assert exit_code == 0
    assert "Seeded Allowlist Entries" in stdout
    assert json.loads(allowlist.read_text(encoding="utf-8")) == {
        "classes": {},
        "functions": {"src/big.py::big": 6},
        "test_files": {"tests/test_big.py": 6},
    }

    exit_code, _stdout, stderr = run_cli(["--size-limits", "--seed-allowlist", str(project)])
    assert exit_code == 2
    assert "already exists" in stderr

    write(project, "src/big.py", "def big():\n" + "    x = 1\n" * 3)
    exit_code, stdout, _stderr = run_cli(
        ["--size-limits", "--update-allowlist", "--format", "plain", str(project)]
    )
    assert exit_code == 0
    assert "Lowered Allowlist Entries" in stdout
    assert json.loads(allowlist.read_text(encoding="utf-8"))["functions"] == {"src/big.py::big": 4}


def test_configured_allowlist_path_is_relative_to_the_project_root(tmp_path: Path) -> None:
    project = make_project(tmp_path, config=CONFIG + 'allowlist = "config/limits.json"\n')

    exit_code, _stdout, _stderr = run_cli(["--size-limits", "--seed-allowlist", str(project)])

    assert exit_code == 0
    assert (project / "config" / "limits.json").is_file()
    assert not (project / "size-limits.json").exists()


def test_allowlist_option_overrides_the_configured_path(tmp_path: Path) -> None:
    project = make_project(tmp_path / "project")
    scratch = tmp_path / "scratch" / "seeded.json"

    exit_code, stdout, _stderr = run_cli(
        [
            "--size-limits",
            "--seed-allowlist",
            "--allowlist",
            str(scratch),
            "--format",
            "json",
            str(project),
        ]
    )

    assert exit_code == 0
    assert scratch.is_file()
    assert not (project / "size-limits.json").exists()
    assert json.loads(stdout)["allowlist"]["path"] == str(scratch)


def test_json_shape(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    _code, stdout, _stderr = run_cli(["--size-limits", "--format", "json", str(project)])
    data = json.loads(stdout)

    assert list(data) == [
        "report_type",
        "schema_version",
        "path",
        "action",
        "limits",
        "allowlist",
        "units",
        "check_failed",
        "over_limit",
        "grown",
        "shrunk",
        "stale",
        "seeded",
        "lowered",
        "dropped",
        "failures",
    ]
    assert data["report_type"] == "size_limits"
    assert data["schema_version"] == 1
    assert data["action"] == "report"
    assert data["limits"] == {"functions": 3, "classes": 8, "test_files": 4}
    assert data["allowlist"]["existed"] is False
    assert data["check_failed"] is True
    assert data["over_limit"][0] == {
        "kind": "functions",
        "key": "src/big.py::big",
        "size": 6,
        "limit": 3,
        "recorded": None,
        "delta": None,
        "reason": None,
    }


def test_default_limits_apply_without_config(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", "def long():\n" + "    x = 1\n" * 150)

    _code, stdout, _stderr = run_cli(["--size-limits", "--format", "json", str(tmp_path)])
    data = json.loads(stdout)

    assert data["limits"] == {"functions": 150, "classes": 1500, "test_files": 3000}
    assert [entry["key"] for entry in data["over_limit"]] == ["src/app.py::long"]


def test_parse_failures_exit_1(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    write(project, "src/broken.py", "def broken(:\n")

    exit_code, _stdout, stderr = run_cli(["--size-limits", str(project)])

    assert exit_code == 1
    assert "could not analyze" in stderr


def test_projects_use_one_allowlist_each(tmp_path: Path) -> None:
    make_project(tmp_path / "one", config="")
    make_project(tmp_path / "two", config="")
    write(
        tmp_path,
        "pyproject.toml",
        CONFIG
        + '\n[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n'
        + '\n[[tool.slopscope.projects]]\nname = "two"\npath = "two"\n'
        + '\n[[tool.slopscope.projects]]\nname = "gone"\npath = "gone"\noptional = true\n',
    )
    (tmp_path / "two" / "size-limits.json").write_text("{}\n", encoding="utf-8")

    exit_code, _stdout, stderr = run_cli(
        ["--size-limits", "--project", "all", "--seed-allowlist", str(tmp_path)]
    )
    assert exit_code == 2
    assert "project two: allowlist" in stderr
    # The refusal happens before any project is seeded.
    assert not (tmp_path / "one" / "size-limits.json").exists()

    (tmp_path / "two" / "size-limits.json").unlink()
    exit_code, stdout, stderr = run_cli(
        ["--size-limits", "--project", "all", "--seed-allowlist", "--format", "json", str(tmp_path)]
    )
    assert exit_code == 0
    assert "skipping optional project gone" in stderr
    data = json.loads(stdout)
    assert data["report_type"] == "size_limits_projects"
    assert [project["name"] for project in data["projects"]] == ["one", "two"]
    assert (tmp_path / "one" / "size-limits.json").is_file()
    assert (tmp_path / "two" / "size-limits.json").is_file()
    assert [skipped["name"] for skipped in data["skipped_projects"]] == ["gone"]


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--size-limits", "--composition"], "--size-limits cannot be combined with --composition"),
        (["--size-limits", "--engine", "python"], "cannot be combined with --engine"),
        (["--size-limits", "--limit", "3"], "cannot be combined with --limit"),
        (["--size-limits", "--churn"], "cannot be combined with --churn"),
        (["--strict"], "--strict requires --size-limits"),
        (["--seed-allowlist"], "--seed-allowlist requires --size-limits"),
        (["--update-allowlist"], "--update-allowlist requires --size-limits"),
        (["--allowlist", "x.json"], "--allowlist requires --size-limits"),
        (
            ["--size-limits", "--seed-allowlist", "--update-allowlist"],
            "--update-allowlist cannot be combined with --seed-allowlist",
        ),
        (
            ["--size-limits", "--allowlist", "x.json", "--project", "all"],
            "--allowlist cannot be combined with --project",
        ),
    ],
)
def test_invalid_combinations(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.run(argv)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("section", "message"),
    [
        (
            "[tool.slopscope.size_limits]\nmax_class_lines = 0\n",
            "max_class_lines must be a positive",
        ),
        (
            "[tool.slopscope.size_limits]\nunknown = 1\n",
            "unknown [tool.slopscope.size_limits] field",
        ),
        ('[tool.slopscope.size_limits]\nallowlist = ""\n', "allowlist must be a non-empty string"),
    ],
)
def test_invalid_config_exits_2(tmp_path: Path, section: str, message: str) -> None:
    write(tmp_path, "pyproject.toml", section)

    exit_code, _stdout, stderr = run_cli(["--size-limits", str(tmp_path)])

    assert exit_code == 2
    assert message in stderr


def test_rich_falls_back_to_plain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _code, plain, _err = run_cli(["--size-limits", "--format", "plain", str(project)])
    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None) -> ModuleType:
        if name.startswith("rich."):
            raise ImportError(name)
        return real_import_module(name, package)

    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)

    assert run_cli(["--size-limits", str(project)])[1] == plain


def test_rich_output_when_available(tmp_path: Path) -> None:
    pytest.importorskip("rich")
    project = make_project(tmp_path)

    exit_code, output, _stderr = run_cli(["--size-limits", str(project)])

    assert exit_code == 0
    assert "\x1b[" in output
    assert "Stale Allowlist Entries" in output


def _two_projects(tmp_path: Path, extra: str = "") -> None:
    make_project(tmp_path / "one", config="")
    make_project(tmp_path / "two", config="")
    write(
        tmp_path,
        "pyproject.toml",
        CONFIG
        + extra
        + '\n[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n'
        + '\n[[tool.slopscope.projects]]\nname = "two"\npath = "two"\n',
    )


def test_a_later_project_failure_leaves_earlier_projects_unwritten(tmp_path: Path) -> None:
    _two_projects(tmp_path)
    write(tmp_path, "two/src/broken.py", "def broken(:\n")

    exit_code, _stdout, stderr = run_cli(
        ["--size-limits", "--project", "all", "--seed-allowlist", str(tmp_path)]
    )

    assert exit_code == 2
    assert "project two: cannot seed the allowlist" in stderr
    assert not (tmp_path / "one" / "size-limits.json").exists()


def test_projects_sharing_an_allowlist_cannot_seed_or_update(tmp_path: Path) -> None:
    shared = tmp_path / "shared.json"
    _two_projects(tmp_path, extra=f'allowlist = "{shared.as_posix()}"\n')

    exit_code, _stdout, stderr = run_cli(
        ["--size-limits", "--project", "all", "--seed-allowlist", str(tmp_path)]
    )

    assert exit_code == 2
    assert "projects one and two share the allowlist" in stderr
    assert not shared.exists()
    assert run_cli(["--size-limits", "--project", "all", str(tmp_path)])[0] == 0


def test_non_utf8_allowlist_exits_2(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "size-limits.json").write_bytes(b'{"functions": {"\xff": 3}}')

    exit_code, _stdout, stderr = run_cli(["--size-limits", str(project)])

    assert exit_code == 2
    assert "is not valid UTF-8" in stderr
