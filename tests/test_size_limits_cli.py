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


@pytest.mark.parametrize("mode", [[], ["--strict"], ["--seed-allowlist"], ["--update-allowlist"]])
def test_deep_allowlist_is_domain_error_without_write(tmp_path: Path, mode: list[str]) -> None:
    project = make_project(tmp_path)
    allowlist = project / "size-limits.json"
    original = "[" * 10000 + "0" + "]" * 10000
    allowlist.write_text(original, encoding="utf-8")

    code, stdout, stderr = run_cli(["--size-limits", *mode, str(project)])

    assert code == 2
    assert stdout == ""
    assert "JSON nesting is too deep" in stderr or "must be a JSON object" in stderr
    assert "Traceback" not in stderr
    assert len(stderr) < 300
    assert allowlist.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("action", ["seed", "update"])
@pytest.mark.parametrize("change", ["symlink", "ancestor", "file"])
@pytest.mark.parametrize("phase", ["analysis", "fsync"])
def test_mutation_refuses_retargeted_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, change: str, phase: str
) -> None:
    import os

    from slopscope import size_limits

    project = make_project(tmp_path / "project")
    original_dir, other_dir = tmp_path / "original", tmp_path / "other"
    original_dir.mkdir()
    other_dir.mkdir()
    original, other = original_dir / "limits.json", other_dir / "limits.json"
    prior = b'{"functions": {"src/big.py::big": 20}}\n'
    if action == "update":
        original.write_bytes(prior)
    other.write_bytes(prior)
    link = tmp_path / "selected"
    if change == "symlink":
        link.symlink_to(original)
        selected = link
    elif change == "ancestor":
        link.symlink_to(original_dir, target_is_directory=True)
        selected = link / "limits.json"
    else:
        selected = original
    changed = False
    saved = tmp_path / "saved.json"

    def retarget() -> None:
        nonlocal changed
        if changed:
            return
        changed = True
        if change == "file":
            if original.exists():
                original.rename(saved)
            original.write_bytes(prior)
        else:
            link.unlink()
            link.symlink_to(
                other if change == "symlink" else other_dir,
                target_is_directory=change == "ancestor",
            )

    real_measure, real_fsync = size_limits.measure, os.fsync

    def measure(*args: object, **kwargs: object) -> object:
        result = real_measure(*args, **kwargs)  # type: ignore[arg-type]
        retarget()
        return result

    def fsync(fd: int) -> None:
        real_fsync(fd)
        retarget()

    if phase == "analysis":
        monkeypatch.setattr(size_limits, "measure", measure)
    else:
        monkeypatch.setattr(os, "fsync", fsync)
    code, stdout, stderr = run_cli(
        ["--size-limits", f"--{action}-allowlist", "--allowlist", str(selected), str(project)]
    )
    assert changed
    assert code == 2
    assert stdout == ""
    assert "target changed" in stderr
    assert other.read_bytes() == prior
    if change == "file" or action == "update":
        assert original.read_bytes() == prior
    else:
        assert not original.exists()
    if saved.exists():
        assert saved.read_bytes() == prior


@pytest.mark.parametrize("action", ["seed", "update"])
@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_project_publication_failure_restores_all_allowlists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, operation: str
) -> None:
    import os

    _two_projects(tmp_path)
    make_project(tmp_path / "three", config="")
    with (tmp_path / "pyproject.toml").open("a", encoding="utf-8") as config:
        config.write('\n[[tool.slopscope.projects]]\nname = "three"\npath = "three"\n')
    paths = [tmp_path / name / "size-limits.json" for name in ("one", "two", "three")]
    originals = [b'{ "functions": {"src/big.py::big": 20} }\n' + b" " * i for i in range(3)]
    modes = []
    for path, content in zip(paths, originals, strict=True):
        if action == "update":
            path.write_bytes(content)
            path.chmod(0o640)
            modes.append(path.stat().st_mode)
    real_replace, real_fsync = os.replace, os.fsync
    failed = False
    synced = 0

    def replace(src: str | Path, dst: str | Path) -> None:
        nonlocal failed
        if Path(dst) == paths[1] and not failed:
            failed = True
            raise OSError("injected second replace failure")
        real_replace(src, dst)

    def fsync(fd: int) -> None:
        nonlocal synced, failed
        synced += 1
        # Update also fsyncs the first recovery backup before staging the second output.
        if synced == (3 if action == "update" else 2):
            failed = True
            raise OSError("injected staging fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, operation, replace if operation == "replace" else fsync)
    code, stdout, stderr = run_cli(
        ["--size-limits", "--project", "all", f"--{action}-allowlist", str(tmp_path)]
    )
    assert failed
    assert code == 2
    assert stdout == ""
    assert "injected" in stderr
    for i, path in enumerate(paths):
        if action == "update":
            assert path.read_bytes() == originals[i]
            assert path.stat().st_mode == modes[i]
        else:
            assert not path.exists()
        assert sorted(p.name for p in path.parent.glob(".size-limits.json.*")) == [
            ".size-limits.json.lock"
        ]


def test_failed_compensation_names_and_retains_recovery_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    _two_projects(tmp_path)
    paths = [tmp_path / name / "size-limits.json" for name in ("one", "two")]
    original = b'{ "functions": {"src/big.py::big": 20} }\n'
    for path in paths:
        path.write_bytes(original)
        path.chmod(0o640)
    original_mode = paths[0].stat().st_mode
    real_replace = os.replace
    attempts = 0

    def replace(src: str | Path, dst: str | Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts >= 2:
            raise OSError("injected publication/compensation failure")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    code, stdout, stderr = run_cli(
        ["--size-limits", "--project", "all", "--update-allowlist", str(tmp_path)]
    )
    assert attempts >= 3
    assert code == 2
    assert stdout == ""
    assert "compensation failed" in stderr
    backups = list(paths[0].parent.glob(".size-limits.json.*.backup"))
    assert len(backups) == 1
    assert str(backups[0]) in stderr
    assert backups[0].read_bytes() == original
    assert backups[0].stat().st_mode == original_mode
    assert paths[1].read_bytes() == original


def test_mutation_rejects_substituted_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    project = make_project(tmp_path / "project")
    target = project / "size-limits.json"
    other = tmp_path / "other.json"
    original = b'{"functions": {"src/big.py::big": 20}}\n'
    target.write_bytes(original)
    other.write_text("{}", encoding="utf-8")
    real_open = os.open
    substituted = False

    def open_file(path: str | Path, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal substituted
        if Path(path) == target:
            substituted = True
            return real_open(other, flags)
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", open_file)
    code, stdout, stderr = run_cli(["--size-limits", "--update-allowlist", str(project)])
    assert substituted
    assert code == 2
    assert stdout == ""
    assert "target changed" in stderr
    assert target.read_bytes() == original
    assert other.read_text(encoding="utf-8") == "{}"


def test_mutation_refuses_replaced_canonical_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from slopscope import size_limits

    project = make_project(tmp_path / "project")
    parent, moved = tmp_path / "destination", tmp_path / "moved"
    parent.mkdir()
    target = parent / "limits.json"
    prior = b'{"functions": {"src/big.py::big": 20}}\n'
    target.write_bytes(prior)
    real_measure = size_limits.measure
    changed = False

    def measure(*args: object, **kwargs: object) -> object:
        nonlocal changed
        result = real_measure(*args, **kwargs)  # type: ignore[arg-type]
        changed = True
        parent.rename(moved)
        parent.mkdir()
        target.write_bytes(prior)
        return result

    monkeypatch.setattr(size_limits, "measure", measure)
    code, stdout, stderr = run_cli(
        ["--size-limits", "--update-allowlist", "--allowlist", str(target), str(project)]
    )
    assert changed
    assert code == 2
    assert stdout == ""
    assert "target changed" in stderr
    assert target.read_bytes() == prior
    assert (moved / target.name).read_bytes() == prior


def test_interrupted_publication_retains_replaced_backups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    _two_projects(tmp_path)
    paths = [tmp_path / name / "size-limits.json" for name in ("one", "two")]
    original = b'{ "functions": {"src/big.py::big": 20} }\n'
    for path in paths:
        path.write_bytes(original)
    real_replace = os.replace

    def replace(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == paths[1]:
            raise KeyboardInterrupt
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(KeyboardInterrupt):
        run_cli(["--size-limits", "--project", "all", "--update-allowlist", str(tmp_path)])
    assert paths[0].read_bytes() != original
    backups = list(paths[0].parent.glob(".size-limits.json.*.backup"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert paths[1].read_bytes() == original
    # The interrupted replacement's outcome is unknown, so its backup is conservatively kept.
    kept = list(paths[1].parent.glob(".size-limits.json.*.backup"))
    assert len(kept) == 1
    assert kept[0].read_bytes() == original
    assert not list(paths[1].parent.glob(".size-limits.json.*.pending"))


def test_body_oserror_is_not_reported_as_lock_failure(tmp_path: Path) -> None:
    from slopscope import size_limits

    target = tmp_path / "size-limits.json"
    with (
        pytest.raises(OSError, match="body failure") as raised,
        size_limits.allowlist_locks([target], action=size_limits.UPDATE),
    ):
        raise OSError("body failure")
    assert not isinstance(raised.value, size_limits.SizeLimitsError)


def test_interrupted_compensation_retains_unrestored_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    _two_projects(tmp_path)
    paths = [tmp_path / name / "size-limits.json" for name in ("one", "two")]
    original = b'{ "functions": {"src/big.py::big": 20} }\n'
    for path in paths:
        path.write_bytes(original)
    real_replace = os.replace

    def replace(src: str | Path, dst: str | Path) -> None:
        if Path(dst) == paths[1]:
            raise OSError("injected second replace failure")
        if Path(src).name.endswith(".backup"):
            raise KeyboardInterrupt
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(KeyboardInterrupt):
        run_cli(["--size-limits", "--project", "all", "--update-allowlist", str(tmp_path)])
    assert paths[0].read_bytes() != original
    backups = list(paths[0].parent.glob(".size-limits.json.*.backup"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    assert paths[1].read_bytes() == original
    assert not list(paths[1].parent.glob(".size-limits.json.*.backup"))
