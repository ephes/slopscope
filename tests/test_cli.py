from __future__ import annotations

import io
import json
import tomllib
from pathlib import Path

import pytest

from slopscope import cli, cloc, fallback
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


def test_cli_help_includes_profile_options() -> None:
    help_text = cli.build_parser().format_help()

    assert "--project NAME" in help_text
    assert "--profile NAME" in help_text
    assert "--total-only" in help_text
    assert "--top N" in help_text


def test_cli_project_name_selects_configured_project_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"
""".strip(),
        encoding="utf-8",
    )
    seen_paths: list[Path] = []

    def fake_build_file_rows(path: Path | str, **_kwargs: object) -> list[FileRow]:
        seen_paths.append(Path(path))
        return [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)]

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "frontend",
            "--engine",
            "python",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert seen_paths == [frontend.resolve()]
    assert data["projects"][0]["name"] == "frontend"
    assert data["snapshot_rows"][0]["code"] == 4
    assert stderr.getvalue() == ""


def test_cli_project_all_selects_all_configured_projects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"

[[tool.slopscope.projects]]
name = "backend"
path = "backend"
""".strip(),
        encoding="utf-8",
    )

    def fake_build_file_rows(path: Path | str, **_kwargs: object) -> list[FileRow]:
        name = Path(path).name
        code = 4 if name == "frontend" else 6
        return [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=code)]

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "all",
            "--engine",
            "python",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert [project["name"] for project in data["projects"]] == ["frontend", "backend"]
    assert [row["code"] for row in data["snapshot_rows"]] == [4, 6]
    assert stderr.getvalue() == ""


def test_cli_project_repeatable_selection_uses_config_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    docs = tmp_path / "docs"
    frontend.mkdir()
    backend.mkdir()
    docs.mkdir()
    config_path = tmp_path / "pyproject.toml"
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
name = "docs"
path = "docs"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "slopscope.cli.fallback.build_file_rows",
        lambda _path, **_kwargs: [
            FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "docs",
            "--project",
            "frontend",
            "--engine",
            "python",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert [project["name"] for project in data["projects"]] == ["frontend", "docs"]
    assert stderr.getvalue() == ""


def test_cli_project_unknown_name_returns_usage_error(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--config", str(config_path), "--project", "backend", "--engine", "python"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: no configured project named 'backend'" in stderr.getvalue()


def test_cli_project_without_configured_projects_returns_usage_error(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--project", "frontend", "--engine", "python", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: no configured project named 'frontend'" in stderr.getvalue()


def test_cli_project_required_missing_path_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "backend"
path = "backend"
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--config", str(config_path), "--project", "backend", "--engine", "python"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: project backend path not found:" in stderr.getvalue()


def test_cli_project_optional_missing_path_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"

[[tool.slopscope.projects]]
name = "docs"
path = "docs"
optional = true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "slopscope.cli.fallback.build_file_rows",
        lambda _path, **_kwargs: [
            FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "all",
            "--engine",
            "python",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert [project["name"] for project in data["projects"]] == ["frontend"]
    assert data["skipped_projects"] == [
        {
            "name": "docs",
            "path": str((tmp_path / "docs").resolve()),
            "reason": "missing optional project path",
        }
    ]
    assert "slopscope: skipping optional project docs:" in stderr.getvalue()


def test_cli_project_all_optional_missing_paths_render_json_success(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "docs"
path = "docs"
optional = true
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "all",
            "--engine",
            "python",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert data["projects"] == []
    assert data["snapshot_rows"] == []
    assert data["skipped_projects"] == [
        {
            "name": "docs",
            "path": str((tmp_path / "docs").resolve()),
            "reason": "missing optional project path",
        }
    ]
    assert "slopscope: skipping optional project docs:" in stderr.getvalue()


def test_cli_project_with_profile_is_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--config", str(config_path), "--project", "frontend", "--profile", "yaml"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: --project cannot be combined with --profile" in stderr.getvalue()


def test_cli_project_plain_output_includes_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "slopscope.cli.fallback.build_file_rows",
        lambda _path, **_kwargs: [
            FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "frontend",
            "--engine",
            "python",
            "--format",
            "plain",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Slopscope Projects" in output
    assert "Project Snapshot" in output
    assert "Per-Project Reports" in output
    assert "Project: frontend" in output
    assert stderr.getvalue() == ""


def test_cli_project_cloc_engine_uses_cloc_for_each_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"

[[tool.slopscope.projects]]
name = "backend"
path = "backend"
""".strip(),
        encoding="utf-8",
    )
    language_calls: list[Path] = []
    file_calls: list[Path] = []

    def fake_run_language_summary(path: Path | str) -> cloc.ClocResult:
        language_calls.append(Path(path))
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,0,0,10\nSUM,1,0,0,10\n",
            stderr="",
        )

    def fake_run_file_summary(path: Path | str) -> cloc.ClocResult:
        file_calls.append(Path(path))
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,src/app.py,0,0,10\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: True)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr("slopscope.cli.cloc.run_file_summary", fake_run_file_summary)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "all",
            "--engine",
            "cloc",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert language_calls == [frontend.resolve(), backend.resolve()]
    assert file_calls == [frontend.resolve(), backend.resolve()]
    assert json.loads(stdout.getvalue())["engine"] == "cloc"
    assert stderr.getvalue() == ""


def test_cli_project_cloc_engine_fails_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: False)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--config", str(config_path), "--project", "frontend", "--engine", "cloc"],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "cloc engine requested, but cloc was not found" in stderr.getvalue()


def test_cli_project_cloc_failure_includes_project_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    frontend.mkdir()
    backend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"

[[tool.slopscope.projects]]
name = "backend"
path = "backend"
""".strip(),
        encoding="utf-8",
    )

    def fake_run_language_summary(path: Path | str) -> cloc.ClocResult:
        if Path(path).name == "backend":
            return cloc.ClocResult(returncode=7, stdout="", stderr="cloc exploded\n")
        return cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,1,0,0,10\nSUM,1,0,0,10\n",
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: True)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr(
        "slopscope.cli.cloc.run_file_summary",
        lambda _path: cloc.ClocResult(
            returncode=0,
            stdout="language,filename,blank,comment,code\nPython,src/app.py,0,0,10\n",
            stderr="",
        ),
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "all",
            "--engine",
            "cloc",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 7
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "cloc exploded\nslopscope: project backend failed\n"


def test_cli_project_auto_falls_back_to_python_when_cloc_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.projects]]
name = "frontend"
path = "frontend"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: False)
    monkeypatch.setattr(
        "slopscope.cli.fallback.build_file_rows",
        lambda _path, **_kwargs: [
            FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=4)
        ],
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--config", str(config_path), "--project", "frontend", "--format", "json"],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert data["engine"] == "python"
    assert data["projects"][0]["report"]["engine"] == "python"
    assert stderr.getvalue() == ""


def test_cli_project_top_level_classification_applies_to_each_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "slopscope.fallback.run_git_ls_files",
        lambda _path: fallback.GitLsFilesResult(returncode=128, stdout=b""),
    )
    app = tmp_path / "app"
    app.mkdir()
    (app / "pkg").mkdir()
    (app / "checks").mkdir()
    (app / "pkg" / "main.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (app / "checks" / "test_main.py").write_text("one\ntwo\n", encoding="utf-8")
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]
source_dirs = ["pkg"]
test_dirs = ["checks"]
areas = ["pkg", "checks"]
include_languages = ["Python"]

[[tool.slopscope.projects]]
name = "app"
path = "app"
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--config",
            str(config_path),
            "--project",
            "app",
            "--engine",
            "python",
            "--format",
            "json",
        ],
        stdout=stdout,
        stderr=stderr,
    )

    summary = json.loads(stdout.getvalue())["projects"][0]["report"]["source_test_summary"]
    assert exit_code == 0
    assert summary == {
        "source_files": 1,
        "source_code": 3,
        "test_files": 1,
        "test_code": 2,
    }
    assert stderr.getvalue() == ""


def test_cli_missing_profile_returns_usage_error(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--profile", "yaml", "--format", "plain", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: no configured profile named 'yaml'" in stderr.getvalue()


def test_cli_total_only_requires_profile() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--total-only"], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: --total-only requires --profile" in stderr.getvalue()


def test_cli_yaml_profile_total_only_prints_integer_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "slopscope.fallback.run_git_ls_files",
        lambda _path: fallback.GitLsFilesResult(returncode=128, stdout=b""),
    )
    (tmp_path / "a.yml").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("one\n\nthree\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("one\n", encoding="utf-8")
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
include_globs = ["*.yml", "*.yaml"]
physical_lines = true
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--profile", "yaml", "--total-only", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "5\n"
    assert stderr.getvalue() == ""


def test_cli_cloc_profile_total_uses_cloc_code_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: True)
    monkeypatch.setattr(
        "slopscope.profile.cloc.run_file_summary",
        lambda _path: cloc.ClocResult(
            returncode=0,
            stdout=(
                "language,filename,blank,comment,code\n"
                "YAML,a.yml,1,2,7\n"
                "YAML,b.yaml,0,1,11\n"
                "Markdown,README.md,0,0,5\n"
            ),
            stderr="",
        ),
    )
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
include_globs = ["*.yml", "*.yaml"]
physical_lines = false
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--profile", "yaml", "--total-only", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "18\n"
    assert stderr.getvalue() == ""


def test_cli_cloc_profile_failure_remains_clear(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: True)
    monkeypatch.setattr(
        "slopscope.profile.cloc.run_file_summary",
        lambda _path: cloc.ClocResult(returncode=8, stdout="", stderr="file summary failed\n"),
    )
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--profile", "yaml", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 8
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "file summary failed\n"


def test_cli_auto_falls_back_to_python_when_cloc_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_is_cloc_available() -> bool:
        return False

    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
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


def test_cli_cloc_profile_still_fails_clearly_without_cloc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", lambda: False)
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "yaml"
include_languages = ["YAML"]
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--profile", "yaml", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

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
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
        return [FileRow(language="Markdown", path="README.md", blank=0, comment=0, code=2)]

    monkeypatch.setattr("slopscope.cli.fallback.build_file_rows", fake_build_file_rows)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--engine", "python", "--format", "plain"], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert "Engine: python (physical lines)" in stdout.getvalue()
    assert "Markdown" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_cli_uses_defaults_when_default_pyproject_is_absent(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\n", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "json", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    languages = {row["language"] for row in json.loads(stdout.getvalue())["language_rows"]}
    assert "Python" in languages
    assert stderr.getvalue() == ""


def test_cli_uses_defaults_when_pyproject_has_no_slopscope_table(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "sample"\n', encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "json", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    languages = {row["language"] for row in json.loads(stdout.getvalue())["language_rows"]}
    assert "Python" in languages
    assert stderr.getvalue() == ""


def test_cli_explicit_config_path_applies_fallback_filters(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "generated").mkdir()
    (repo / "docs").mkdir()
    (repo / "src" / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    (repo / "generated" / "skip.py").write_text("one\n", encoding="utf-8")
    (repo / "docs" / "index.md").write_text("one\n", encoding="utf-8")
    config_path = tmp_path / "slopscope.toml"
    config_path.write_text(
        """
[tool.slopscope]
include_languages = ["Python"]
exclude_languages = ["JSON"]
exclude_dirs = ["generated"]
include_globs = ["*.py", "docs/*.md"]
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "json", "--config", str(config_path), str(repo)],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert data["language_rows"] == [
        {"language": "Python", "files": 1, "blank": 0, "comment": 0, "code": 2},
        {"language": "SUM", "files": 1, "blank": 0, "comment": 0, "code": 2},
    ]
    assert data["directory_rows"] == [{"name": "src", "files": 1, "code": 2}]
    assert stderr.getvalue() == ""


def test_cli_default_pyproject_config_applies_classification(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "checks").mkdir()
    (tmp_path / "infra").mkdir()
    (tmp_path / "app" / "main.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "checks" / "test_main.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "infra" / "deploy.yml").write_text("one\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.slopscope]
source_dirs = ["app"]
test_dirs = ["checks"]
areas = ["infra"]
nested_bucket_dirs = ["app"]
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "json", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert data["source_test_summary"] == {
        "source_files": 1,
        "source_code": 3,
        "test_files": 1,
        "test_code": 2,
    }
    assert {"name": "infra", "files": 1, "code": 1} in data["area_rows"]
    assert stderr.getvalue() == ""


def test_cli_missing_explicit_config_returns_usage_error(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--config", str(tmp_path / "missing.toml")], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: configuration file not found" in stderr.getvalue()


def test_cli_invalid_toml_returns_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "broken.toml"
    config_path.write_text("[tool.slopscope\n", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--config", str(config_path)], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "slopscope: invalid TOML" in stderr.getvalue()


def test_cli_invalid_config_field_returns_usage_error(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.toml"
    config_path.write_text('[tool.slopscope]\nexclude_dirs = "build"\n', encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(["--config", str(config_path)], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "exclude_dirs must be an array of strings" in stderr.getvalue()


def test_cli_json_output_has_no_human_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
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
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
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
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
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
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
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


def test_cli_cloc_report_applies_configured_filters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_is_cloc_available() -> bool:
        return True

    def fake_run_language_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout=(
                "language,filename,blank,comment,code\n"
                "Python,2,0,0,15\n"
                "Markdown,1,0,0,2\n"
                "SUM,3,0,0,17\n"
            ),
            stderr="",
        )

    def fake_run_file_summary(_path: Path | str) -> cloc.ClocResult:
        return cloc.ClocResult(
            returncode=0,
            stdout=(
                "language,filename,blank,comment,code\n"
                "Python,src/app.py,0,0,10\n"
                "Python,generated/app.py,0,0,5\n"
                "Markdown,README.md,0,0,2\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("slopscope.cli.cloc.is_cloc_available", fake_is_cloc_available)
    monkeypatch.setattr("slopscope.cli.cloc.run_language_summary", fake_run_language_summary)
    monkeypatch.setattr("slopscope.cli.cloc.run_file_summary", fake_run_file_summary)
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]
include_languages = ["Python"]
exclude_languages = ["JSON"]
exclude_dirs = ["generated"]
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "cloc", "--format", "json", "--config", str(config_path), str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert data["language_rows"] == [
        {"language": "Python", "files": 1, "blank": 0, "comment": 0, "code": 10},
        {"language": "SUM", "files": 1, "blank": 0, "comment": 0, "code": 10},
    ]
    assert data["area_rows"] == [{"name": "src", "files": 1, "code": 10}]
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


def test_cli_grouped_profile_plain_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
        return [
            FileRow(language="YAML", path="roles/web/tasks.yml", blank=0, comment=0, code=8),
            FileRow(language="YAML", path="roles/web/meta.yml", blank=0, comment=0, code=2),
            FileRow(language="YAML", path="roles/db/tasks.yml", blank=0, comment=0, code=5),
            FileRow(language="YAML", path="playbook.yml", blank=0, comment=0, code=20),
        ]

    monkeypatch.setattr("slopscope.profile.fallback.build_file_rows", fake_build_file_rows)
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "roles"
include_languages = ["YAML"]
group_by = "roles/*"
top = 20
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "plain", "--profile", "roles", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    output = stdout.getvalue()
    assert exit_code == 0
    assert "Slopscope Profile" in output
    assert "Group: roles/*" in output
    assert "roles/web                    2       10" in output
    assert "roles/db                     1        5" in output
    assert "playbook.yml" not in output
    assert stderr.getvalue() == ""


def test_cli_grouped_profile_json_and_top_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
        return [
            FileRow(language="YAML", path="roles/web/tasks.yml", blank=0, comment=0, code=8),
            FileRow(language="YAML", path="roles/db/tasks.yml", blank=0, comment=0, code=5),
            FileRow(language="YAML", path="roles/api/tasks.yml", blank=0, comment=0, code=3),
        ]

    monkeypatch.setattr("slopscope.profile.fallback.build_file_rows", fake_build_file_rows)
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "roles"
include_languages = ["YAML"]
group_by = "roles/*"
top = 20
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        [
            "--engine",
            "python",
            "--format",
            "json",
            "--profile",
            "roles",
            "--top",
            "1",
            str(tmp_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )

    data = json.loads(stdout.getvalue())
    assert exit_code == 0
    assert data == {
        "engine": "python",
        "path": str(tmp_path),
        "profile": "roles",
        "group_by": "roles/*",
        "top": 1,
        "total": 16,
        "physical_lines": False,
        "rows": [{"name": "roles/web", "files": 1, "code": 8}],
    }
    assert stderr.getvalue() == ""


def test_cli_grouped_profile_total_only_sums_all_grouped_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
        return [
            FileRow(language="YAML", path="roles/web/tasks.yml", blank=0, comment=0, code=8),
            FileRow(language="YAML", path="roles/db/tasks.yml", blank=0, comment=0, code=5),
            FileRow(language="YAML", path="playbook.yml", blank=0, comment=0, code=20),
        ]

    monkeypatch.setattr("slopscope.profile.fallback.build_file_rows", fake_build_file_rows)
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "roles"
include_languages = ["YAML"]
group_by = "roles/*"
top = 1
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--profile", "roles", "--total-only", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == "13\n"
    assert stderr.getvalue() == ""


def test_cli_grouped_rich_output_falls_back_to_plain_when_rich_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_build_file_rows(_path: Path | str, **_kwargs: object) -> list[FileRow]:
        return [FileRow(language="YAML", path="roles/web/tasks.yml", blank=0, comment=0, code=8)]

    def fake_import_module(name: str) -> object:
        if name.startswith("rich."):
            raise ImportError(name)
        raise AssertionError(name)

    monkeypatch.setattr("slopscope.profile.fallback.build_file_rows", fake_build_file_rows)
    monkeypatch.setattr("slopscope.render.importlib.import_module", fake_import_module)
    config_path = tmp_path / "pyproject.toml"
    config_path.write_text(
        """
[tool.slopscope]

[[tool.slopscope.profiles]]
name = "roles"
include_languages = ["YAML"]
group_by = "roles/*"
""".strip(),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--engine", "python", "--format", "rich", "--profile", "roles", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Slopscope Profile" in stdout.getvalue()
    assert "Grouped Lines" in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_compatibility_entry_point_targets_same_callable() -> None:
    with Path("pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    scripts = pyproject["project"]["scripts"]
    assert scripts["slopscope"] == "slopscope.cli:main"
    assert scripts["count-lines-of-code"] == scripts["slopscope"]
