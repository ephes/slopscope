from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from slopscope import cli, composition


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli.run(argv, stdout=stdout, stderr=stderr)
    return exit_code, stdout.getvalue(), stderr.getvalue()


def make_repository(root: Path) -> Path:
    write(root, "repo/src/app.py", "import os\n\n\ndef run():\n    return os.getcwd()\n")
    write(root, "repo/tests/test_app.py", "def test_run():\n    assert True\n")
    return root / "repo"


def test_snapshot_writes_the_json_report(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "snapshot.json"

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--format", "plain", "--snapshot", str(snapshot), str(repository)]
    )
    _code, as_json, _err = run_cli(["--composition", "--format", "json", str(repository)])

    assert exit_code == 0
    assert stderr == ""
    assert stdout.startswith("Slopscope Composition\n")
    assert snapshot.read_text(encoding="utf-8") == as_json


def test_snapshot_write_failure_exits_2(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--snapshot", str(tmp_path / "missing" / "s.json"), str(repository)]
    )

    assert exit_code == 2
    assert stdout == ""
    assert "could not write snapshot" in stderr


def test_baseline_reports_deltas(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), "--format", "json", str(repository)])
    write(
        repository,
        "src/app.py",
        "import os\nimport sys\n\n\ndef run():\n    return os.getcwd()\n",
    )
    write(repository, "tests/test_more.py", "def test_more():\n    assert 1\n")

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--baseline", str(snapshot), "--format", "json", str(repository)]
    )

    assert exit_code == 0
    assert stderr == ""
    baseline = json.loads(stdout)["baseline"]
    assert baseline["path"] == str(snapshot)
    assert baseline["comparable"] is True
    assert baseline["warnings"] == []
    total = baseline["deltas"]["total"]
    assert total["files"] == {"baseline": 2, "current": 3, "delta": 1}
    assert total["categories.import"]["delta"] == 1
    assert total["constructs.tests"]["delta"] == 1
    assert baseline["deltas"]["source"]["code"]["delta"] == 1
    assert baseline["deltas"]["tests"]["code"]["delta"] == 2
    assert baseline["deltas"]["other"]["code"]["delta"] == 0
    assert list(baseline["deltas"]) == ["total", "source", "tests", "other"]


def test_baseline_plain_output_lists_changed_metrics(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(repository)])
    write(repository, "src/extra.py", "import json\n")

    _code, stdout, _stderr = run_cli(
        ["--composition", "--baseline", str(snapshot), "--format", "plain", str(repository)]
    )

    assert "Changes Since Baseline" in stdout
    assert f"Baseline: {snapshot}" in stdout
    assert "categories.import" in stdout
    assert "categories.docstring" not in stdout
    assert "duplicated_lines" in stdout


def test_baseline_can_be_overwritten_by_the_snapshot(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "rolling.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(repository)])

    exit_code, _stdout, _stderr = run_cli(
        [
            "--composition",
            "--baseline",
            str(snapshot),
            "--snapshot",
            str(snapshot),
            "--format",
            "json",
            str(repository),
        ]
    )

    assert exit_code == 0
    assert json.loads(snapshot.read_text(encoding="utf-8"))["baseline"]["comparable"] is True


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "baseline file not found"),
        ("{not json", "is not valid JSON"),
        ("[]", "is not a composition report"),
        ('{"report_type": "composition_projects", "schema_version": 4}', "has report_type"),
        ('{"engine": "python", "path": "."}', "has report_type None"),
        ('{"report_type": "composition", "schema_version": 1}', "has schema_version 1"),
    ],
)
def test_invalid_baselines_exit_2(tmp_path: Path, content: str | None, message: str) -> None:
    repository = make_repository(tmp_path)
    baseline = tmp_path / "baseline.json"
    if content is not None:
        baseline.write_text(content, encoding="utf-8")

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--baseline", str(baseline), str(repository)]
    )

    assert exit_code == 2
    assert stdout == ""
    assert message in stderr


def test_detector_version_mismatch_warns_and_marks_not_comparable(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(repository)])
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    data["detectors"][0]["version"] = 0
    data["analyzer"]["version"] = 0
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--baseline", str(snapshot), "--format", "json", str(repository)]
    )

    assert exit_code == 0
    baseline = json.loads(stdout)["baseline"]
    assert baseline["comparable"] is False
    assert "slopscope: warning: analyzer version differs" in stderr
    assert "slopscope: warning: detector qt version differs" in stderr
    assert len(baseline["warnings"]) == 2


def test_python_version_difference_is_a_warning_but_still_comparable(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(repository)])
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    data["analyzer"]["python_version"] = "3.0.0"
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    _code, stdout, stderr = run_cli(
        ["--composition", "--baseline", str(snapshot), "--format", "json", str(repository)]
    )

    assert json.loads(stdout)["baseline"]["comparable"] is True
    assert "Python version differs" in stderr


def test_project_baselines_match_by_name(tmp_path: Path) -> None:
    write(tmp_path, "one/src/a.py", "x = 1\n")
    write(tmp_path, "two/src/b.py", "y = 2\n")
    config = tmp_path / "pyproject.toml"
    config.write_text(
        '[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n',
        encoding="utf-8",
    )
    snapshot = tmp_path / "projects.json"
    run_cli(["--composition", "--project", "all", "--snapshot", str(snapshot), str(tmp_path)])
    config.write_text(
        '[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n\n'
        '[[tool.slopscope.projects]]\nname = "two"\npath = "two"\n',
        encoding="utf-8",
    )
    write(tmp_path, "one/src/c.py", "z = 3\n")

    exit_code, stdout, stderr = run_cli(
        [
            "--composition",
            "--project",
            "all",
            "--baseline",
            str(snapshot),
            "--format",
            "json",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    projects = json.loads(stdout)["projects"]
    one = projects[0]["report"]["baseline"]
    two = projects[1]["report"]["baseline"]
    assert one["deltas"]["total"]["files"]["delta"] == 1
    assert two["comparable"] is False
    assert two["deltas"]["total"]["files"] == {"baseline": None, "current": 1, "delta": None}
    assert "project two is not in the baseline" in stderr


def test_single_report_baseline_is_rejected_for_projects(tmp_path: Path) -> None:
    write(tmp_path, "one/src/a.py", "x = 1\n")
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n', encoding="utf-8"
    )
    snapshot = tmp_path / "single.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(tmp_path / "one")])

    exit_code, _stdout, stderr = run_cli(
        ["--composition", "--project", "all", "--baseline", str(snapshot), str(tmp_path)]
    )

    assert exit_code == 2
    assert "expected 'composition_projects'" in stderr


@pytest.mark.parametrize("flag", ["--snapshot", "--baseline"])
def test_snapshot_and_baseline_require_composition(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.run([flag, "x.json"])

    assert exc_info.value.code == 2
    assert f"{flag} requires --composition" in capsys.readouterr().err


def test_schema_version_is_current() -> None:
    assert composition.SCHEMA_VERSION == 4


@pytest.mark.parametrize(
    "report",
    [
        {"analyzer": None},
        {"detectors": None},
        {"detectors": [1]},
        {"total": []},
        {"kinds": {"source": {}}},
    ],
)
def test_structurally_invalid_baselines_exit_2(tmp_path: Path, report: dict[str, object]) -> None:
    repository = make_repository(tmp_path)
    snapshot = tmp_path / "before.json"
    run_cli(["--composition", "--snapshot", str(snapshot), str(repository)])
    data = json.loads(snapshot.read_text(encoding="utf-8"))
    data.update(report)
    snapshot.write_text(json.dumps(data), encoding="utf-8")

    exit_code, stdout, stderr = run_cli(
        ["--composition", "--baseline", str(snapshot), str(repository)]
    )

    assert exit_code == 2
    assert stdout == ""
    assert "has an invalid report" in stderr


def test_structurally_invalid_project_baseline_exits_2(tmp_path: Path) -> None:
    write(tmp_path, "one/src/a.py", "x = 1\n")
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.projects]]\nname = "one"\npath = "one"\n', encoding="utf-8"
    )
    snapshot = tmp_path / "projects.json"
    snapshot.write_text(
        json.dumps(
            {
                "report_type": "composition_projects",
                "schema_version": composition.SCHEMA_VERSION,
                "projects": [{"name": "one", "report": {"analyzer": {}, "detectors": None}}],
            }
        ),
        encoding="utf-8",
    )

    exit_code, _stdout, stderr = run_cli(
        ["--composition", "--project", "all", "--baseline", str(snapshot), str(tmp_path)]
    )

    assert exit_code == 2
    assert "invalid projects[0].report detectors" in stderr
