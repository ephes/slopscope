from __future__ import annotations

from pathlib import Path

import pytest

from slopscope import fallback
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
    def fake_discover_files(_path: Path | str) -> list[Path]:
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


def test_build_file_rows_skips_unknown_and_missing_or_unreadable_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    original_count_physical_lines = fallback.count_physical_lines

    def fake_discover_files(_path: Path | str) -> list[Path]:
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
