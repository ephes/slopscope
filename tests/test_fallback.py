from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from slopscope import composition, fallback
from slopscope.report import FileRow, LanguageRow


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

    monkeypatch.setattr("slopscope.fallback.subprocess.Popen", fake_run)

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


def test_discover_files_filters_default_excludes_from_git_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(
            returncode=0,
            stdout=(
                b"src/app.py\0"
                b"node_modules/pkg/index.js\0"
                b".venv/lib/site.py\0"
                b"build/output.js\0"
                b"dist/output.js\0"
                b"htmlcov/index.html\0"
                b".coverage\0"
            ),
        )

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)

    assert fallback.discover_files(Path(".")) == [Path("src/app.py")]


def test_discover_files_filters_configured_excluded_paths_from_git_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(
            returncode=0,
            stdout=(b"src/app.py\0generated/app.py\0docs/_build/index.html\0docs/index.md\0"),
        )

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)

    assert fallback.discover_files(
        Path("."),
        excluded_paths=("generated", "docs/_build"),
    ) == [Path("src/app.py"), Path("docs/index.md")]


def test_discover_files_filters_include_globs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(
            returncode=0,
            stdout=b"src/app.py\0README.md\0pyproject.toml\0docs/index.md\0",
        )

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)

    assert fallback.discover_files(Path("."), include_globs=("*.py", "docs/*.md")) == [
        Path("src/app.py"),
        Path("docs/index.md"),
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


def test_discover_filesystem_files_returns_sorted_relative_files_and_prunes_excludes(
    tmp_path: Path,
) -> None:
    for dirname in [
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "venv",
    ]:
        (tmp_path / dirname).mkdir()
        (tmp_path / dirname / "ignored.py").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / ".coverage").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")

    assert fallback.discover_filesystem_files(tmp_path) == [
        Path("README.md"),
        Path("src/a.py"),
        Path("src/b.py"),
    ]


def test_map_language_by_suffix_and_filename() -> None:
    assert fallback.map_language(Path("src/app.py")) == "Python"
    assert fallback.map_language(Path("types/package.pyi")) == "Python"
    assert fallback.map_language(Path("README.markdown")) == "Markdown"
    assert fallback.map_language(Path("pyproject.toml")) == "TOML"
    assert fallback.map_language(Path("workflow.yml")) == "YAML"
    assert fallback.map_language(Path("package.json")) == "JSON"
    assert fallback.map_language(Path("frontend/app.tsx")) == "TypeScript"
    assert fallback.map_language(Path("frontend/app.mjs")) == "JavaScript"
    assert fallback.map_language(Path("templates/index.html")) == "HTML"
    assert fallback.map_language(Path("styles/site.css")) == "CSS"
    assert fallback.map_language(Path("scripts/run.zsh")) == "Shell"
    assert fallback.map_language(Path("notes.txt")) == "Text"
    assert fallback.map_language(Path("Dockerfile")) == "Dockerfile"
    assert fallback.map_language(Path("Makefile")) == "Makefile"
    assert fallback.map_language(Path("justfile")) == "Just"
    assert fallback.map_language(Path("image.png")) is None


def test_count_physical_lines_handles_common_text_shapes(tmp_path: Path) -> None:
    empty = tmp_path / "empty.py"
    no_final_newline = tmp_path / "no_final_newline.py"
    multiline = tmp_path / "multiline.py"
    invalid_utf8 = tmp_path / "invalid_utf8.py"

    empty.write_text("", encoding="utf-8")
    no_final_newline.write_text("print('hello')", encoding="utf-8")
    multiline.write_text("one\ntwo\nthree\n", encoding="utf-8")
    invalid_utf8.write_bytes(b"one\n\xfftwo\nthree")

    assert fallback.count_physical_lines(empty) == 0
    assert fallback.count_physical_lines(no_final_newline) == 1
    assert fallback.count_physical_lines(multiline) == 3
    assert fallback.count_physical_lines(invalid_utf8) == 3


def test_count_physical_lines_returns_none_for_missing_files(tmp_path: Path) -> None:
    assert fallback.count_physical_lines(tmp_path / "missing.py") is None


def test_build_language_summary_aggregates_known_files_and_skips_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(returncode=128, stdout=b"")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "src" / "types.pyi").write_text("one\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "ignored.bin").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    report = fallback.build_language_summary(tmp_path)

    assert report.engine == "python"
    assert report.path == tmp_path
    assert report.language_rows == (
        LanguageRow(language="Python", files=2, blank=0, comment=0, code=4),
        LanguageRow(language="Markdown", files=1, blank=0, comment=0, code=2),
        LanguageRow(language="SUM", files=3, blank=0, comment=0, code=6),
    )


def test_build_language_summary_order_is_deterministic_for_equal_line_counts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(returncode=128, stdout=b"")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)
    (tmp_path / "b.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "a.md").write_text("one\n", encoding="utf-8")

    report = fallback.build_language_summary(tmp_path)

    assert [row.language for row in report.language_rows] == ["Markdown", "Python", "SUM"]


def test_build_language_summary_skips_missing_discovered_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_discover_files(_path: Path | str, **_kwargs: object) -> list[Path]:
        return [Path("missing.py")]

    monkeypatch.setattr("slopscope.fallback.discover_files", fake_discover_files)

    report = fallback.build_language_summary(tmp_path)

    assert report.language_rows == ()


def test_build_language_summary_from_file_rows_reuses_counted_rows(tmp_path: Path) -> None:
    rows = [
        FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=2),
        FileRow(language="Markdown", path="README.md", blank=0, comment=0, code=1),
    ]

    report = fallback.build_language_summary_from_file_rows(path=tmp_path, file_rows=rows)

    assert report.language_rows == (
        LanguageRow(language="Python", files=1, blank=0, comment=0, code=2),
        LanguageRow(language="Markdown", files=1, blank=0, comment=0, code=1),
        LanguageRow(language="SUM", files=2, blank=0, comment=0, code=3),
    )


def test_build_file_rows_uses_mapped_languages_and_physical_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(returncode=128, stdout=b"")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("one\n", encoding="utf-8")

    assert fallback.build_file_rows(tmp_path) == [
        FileRow(language="Markdown", path="README.md", blank=0, comment=0, code=1),
        FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=2),
    ]


def test_build_file_rows_applies_configured_filters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_run_git_ls_files(_path: Path | str) -> fallback.GitLsFilesResult:
        return fallback.GitLsFilesResult(returncode=128, stdout=b"")

    monkeypatch.setattr("slopscope.fallback.run_git_ls_files", fake_run_git_ls_files)
    (tmp_path / "src").mkdir()
    (tmp_path / "generated").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "app.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "generated" / "skip.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "docs" / "index.md").write_text("one\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("one\n", encoding="utf-8")

    rows = fallback.build_file_rows(
        tmp_path,
        excluded_paths=("generated",),
        include_globs=("*.py", "docs/*.md"),
        include_languages=("Python", "Markdown"),
        exclude_languages=("Markdown",),
    )

    assert rows == [FileRow(language="Python", path="src/app.py", blank=0, comment=0, code=2)]


def test_build_file_rows_skips_unknown_and_missing_or_unreadable_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_count_physical_lines = fallback.count_physical_lines

    def fake_discover_files(_path: Path | str, **_kwargs: object) -> list[Path]:
        return [
            Path("known.py"),
            Path("unknown.bin"),
            Path("missing.py"),
            Path("unreadable.py"),
        ]

    def fake_count_physical_lines(path: Path | str) -> int | None:
        if Path(path).name == "unreadable.py":
            return None
        return original_count_physical_lines(path)

    monkeypatch.setattr("slopscope.fallback.discover_files", fake_discover_files)
    monkeypatch.setattr("slopscope.fallback.count_physical_lines", fake_count_physical_lines)
    (tmp_path / "known.py").write_text("one\n", encoding="utf-8")

    assert fallback.build_file_rows(tmp_path) == [
        FileRow(language="Python", path="known.py", blank=0, comment=0, code=1),
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX FIFO fixture")
@pytest.mark.parametrize("use_git", [False, True])
def test_source_symlinks_are_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, use_git: bool
) -> None:
    if use_git and shutil.which("git") is None:
        pytest.skip("Git fixture requires git")

    root = tmp_path / "repo"
    root.mkdir()
    (root / "regular.py").write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("external = 1\n", encoding="utf-8")
    special = tmp_path / "pipe"
    os.mkfifo(special)
    (root / "outside.py").symlink_to(outside)
    (root / "inside.py").symlink_to(root / "regular.py")
    (root / "broken.py").symlink_to(tmp_path / "missing")
    (root / "special.py").symlink_to(special)
    (root / "directory.py").symlink_to(tmp_path, target_is_directory=True)
    (root / "linked_dir").symlink_to(tmp_path, target_is_directory=True)
    if use_git:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    else:
        monkeypatch.setattr(
            fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(1, b"")
        )

    def fail_read(path: Path | str) -> int:
        assert Path(path) == root / "regular.py"
        return 1

    monkeypatch.setattr(fallback, "count_physical_lines", fail_read)
    assert fallback.discover_files(root) == [Path("regular.py")]
    assert fallback.discover_filesystem_files(root) == [Path("regular.py")]
    assert composition.discover_python_files(root, excluded_paths=()) == ["regular.py"]
    assert [row.path for row in fallback.build_file_rows(root)] == ["regular.py"]


def test_git_discovery_skips_linked_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "mod.py").write_text("outside = 1\n", encoding="utf-8")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pkg").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(0, b"pkg/mod.py\0")
    )
    assert fallback.discover_files(root) == []
    assert composition.build_composition_report(root, excluded_paths=()).files == ()


def test_git_discovery_skips_uninspectable_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(0, b"blocked/mod.py\0")
    )
    inspected: list[Path] = []

    def unreadable(path: Path) -> bool:
        inspected.append(path)
        raise PermissionError("unsearchable directory")

    monkeypatch.setattr(Path, "is_symlink", unreadable)
    assert fallback.discover_files(tmp_path) == []
    assert inspected


def test_git_discovery_has_finite_deadline_and_preserves_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = b"space name.py\0line\nname.py\0"

    class Process:
        returncode = 0

        def __init__(self, command: list[str], **kwargs: object) -> None:
            assert command == fallback.build_git_ls_files_command(".", executable="custom-git")
            assert kwargs == {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "start_new_session": os.name == "posix",
            }

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            assert timeout == 30.0
            return output, b""

    monkeypatch.setattr("slopscope.fallback.subprocess.Popen", Process)
    result = fallback.run_git_ls_files(".", executable="custom-git")
    assert result == fallback.GitLsFilesResult(0, output)
    assert fallback.parse_git_ls_files_output(result.stdout) == [
        Path("space name.py"),
        Path("line\nname.py"),
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
@pytest.mark.parametrize("wrapper_exited", [False, True])
def test_git_timeout_cleanup_is_bounded(
    monkeypatch: pytest.MonkeyPatch, wrapper_exited: bool
) -> None:
    import signal

    events: list[str] = []

    class Process:
        pid = 12345

        def __init__(self, *args: object, **kwargs: object) -> None:
            assert kwargs["start_new_session"] is True

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            if not events:
                assert timeout == 30.0
            else:
                assert events == ["group", "kill"]
                assert timeout == 1
                events.append("drain")
            raise subprocess.TimeoutExpired("git", timeout, output=b"partial.py\0")

        def kill(self) -> None:
            events.append("kill")
            if wrapper_exited:
                raise ProcessLookupError

        def wait(self, *, timeout: float) -> int:
            assert timeout == 5
            events.append("wait")
            raise subprocess.TimeoutExpired("git", timeout)

    def kill_group(pid: int, sig: int) -> None:
        assert (pid, sig) == (Process.pid, signal.SIGKILL)
        events.append("group")

    monkeypatch.setattr("slopscope.fallback.subprocess.Popen", Process)
    monkeypatch.setattr("slopscope.fallback.os.killpg", kill_group)
    with pytest.raises(fallback.GitDiscoveryError, match="30 seconds: selected-root"):
        fallback.run_git_ls_files("selected-root")
    assert events == ["group", "kill", "drain", "wait"]


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_git_interruption_cleans_up_and_propagates(
    monkeypatch: pytest.MonkeyPatch, platform: str, interruption: type[BaseException]
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock, call

    process = Mock(pid=12345)
    interrupted = interruption("interrupted")
    process.communicate.side_effect = [interrupted, (b"", b"")]
    group_kill, tree_kill = Mock(), Mock()
    monkeypatch.setattr(fallback, "os", SimpleNamespace(name=platform, killpg=group_kill))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(subprocess, "run", tree_kill)
    with pytest.raises(interruption) as caught:
        fallback.run_git_ls_files(".")
    assert caught.value is interrupted
    assert process.communicate.call_args_list == [call(timeout=30), call(timeout=1)]
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    if platform == "posix":
        group_kill.assert_called_once()
        tree_kill.assert_not_called()
    else:
        tree_kill.assert_called_once_with(
            ["taskkill", "/PID", "12345", "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=5,
        )


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("taskkill", 5)])
def test_git_windows_cleanup_failure_remains_bounded(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock, call

    process = Mock(pid=12345)
    process.communicate.side_effect = subprocess.TimeoutExpired("git", 30)
    process.wait.side_effect = subprocess.TimeoutExpired("git", 5)
    monkeypatch.setattr(fallback, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    tree_kill = Mock(side_effect=error)
    monkeypatch.setattr(subprocess, "run", tree_kill)
    with pytest.raises(fallback.GitDiscoveryError):
        fallback.run_git_ls_files(".")
    assert process.communicate.call_args_list == [call(timeout=30), call(timeout=1)]
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert tree_kill.call_args.kwargs["timeout"] == 5
