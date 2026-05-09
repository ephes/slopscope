from __future__ import annotations

from pathlib import Path

import pytest

from slopscope import fallback


def test_build_git_ls_files_command() -> None:
    assert fallback.build_git_ls_files_command(Path(".")) == [
        "git",
        "-C",
        ".",
        "ls-files",
        "-z",
        "--",
        ".",
    ]


def test_build_git_ls_files_command_accepts_custom_executable() -> None:
    assert fallback.build_git_ls_files_command("src", executable="custom-git") == [
        "custom-git",
        "-C",
        "src",
        "ls-files",
        "-z",
        "--",
        ".",
    ]


def test_parse_git_ls_files_output_returns_relative_paths() -> None:
    output = b"README.md\0src/slopscope/cli.py\0tests/test_cli.py\0"

    assert fallback.parse_git_ls_files_output(output) == [
        Path("README.md"),
        Path("src/slopscope/cli.py"),
        Path("tests/test_cli.py"),
    ]


def test_run_git_ls_files_returns_nonzero_result_when_git_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr("slopscope.fallback.subprocess.run", fake_run)

    assert fallback.run_git_ls_files(Path(".")) == fallback.GitLsFilesResult(
        returncode=1,
        stdout=b"",
    )


def test_discover_files_prefers_git_ls_files(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(
            returncode=0,
            stdout=b"tracked.py\0src/package.py\0",
        )

    def fake_discover_filesystem_files(_path: Path | str) -> list[Path]:
        raise AssertionError("filesystem fallback should not run")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)
    monkeypatch.setattr(
        "slopscope.fallback.discover_filesystem_files",
        fake_discover_filesystem_files,
    )

    assert fallback.discover_files(Path(".")) == [
        Path("tracked.py"),
        Path("src/package.py"),
    ]


def test_discover_files_falls_back_to_filesystem_when_git_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(returncode=128, stdout=b"")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "a.py").write_text("", encoding="utf-8")

    assert fallback.discover_files(tmp_path) == [
        Path("a.py"),
        Path("src/b.py"),
    ]


def test_discover_filesystem_files_returns_sorted_relative_files_and_prunes_metadata(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "module.pyc").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")

    assert fallback.discover_filesystem_files(tmp_path) == [
        Path("README.md"),
        Path("src/a.py"),
        Path("src/b.py"),
    ]
