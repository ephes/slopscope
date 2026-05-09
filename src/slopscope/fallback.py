"""Pure-Python fallback file discovery and physical-line summaries."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from slopscope.report import FileRow, LanguageRow, LanguageSummaryReport

DEFAULT_EXCLUDED_PATH_SEGMENTS = frozenset(
    {
        ".coverage",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "venv",
    },
)
# Filesystem discovery prunes the same path segments that fallback summaries exclude.
DEFAULT_PRUNED_DIR_NAMES = DEFAULT_EXCLUDED_PATH_SEGMENTS

LANGUAGES_BY_SUFFIX = {
    ".bash": "Shell",
    ".cjs": "JavaScript",
    ".css": "CSS",
    ".cts": "TypeScript",
    ".htm": "HTML",
    ".html": "HTML",
    ".js": "JavaScript",
    ".json": "JSON",
    ".jsx": "JavaScript",
    ".markdown": "Markdown",
    ".md": "Markdown",
    ".mjs": "JavaScript",
    ".mts": "TypeScript",
    ".py": "Python",
    ".pyi": "Python",
    ".sh": "Shell",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".txt": "Text",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".zsh": "Shell",
}
LANGUAGES_BY_FILENAME = {
    "dockerfile": "Dockerfile",
    "justfile": "Just",
    "makefile": "Makefile",
}


@dataclass(frozen=True)
class GitLsFilesResult:
    """Completed git file-listing process data."""

    returncode: int
    stdout: bytes


def build_git_ls_files_command(path: Path | str, executable: str = "git") -> list[str]:
    """Build the git command used to discover tracked files under a path."""

    return [executable, "-C", str(path), "ls-files", "-z", "--", "."]


def run_git_ls_files(path: Path | str, executable: str = "git") -> GitLsFilesResult:
    """Run git file discovery for the selected path."""

    try:
        completed = subprocess.run(
            build_git_ls_files_command(path, executable=executable),
            capture_output=True,
            check=False,
        )
    except OSError:
        return GitLsFilesResult(returncode=1, stdout=b"")
    return GitLsFilesResult(returncode=completed.returncode, stdout=completed.stdout)


def parse_git_ls_files_output(output: bytes) -> list[Path]:
    """Parse NUL-delimited git file output as relative paths."""

    return [Path(os.fsdecode(part)) for part in output.split(b"\0") if part]


def discover_files(path: Path | str) -> list[Path]:
    """Discover repository files using git when available, then filesystem traversal."""

    root = Path(path)
    git_result = run_git_ls_files(root)
    if git_result.returncode == 0:
        return filter_excluded_paths(parse_git_ls_files_output(git_result.stdout))
    return discover_filesystem_files(root)


def filter_excluded_paths(
    paths: Iterable[Path],
    *,
    excluded_path_segments: Iterable[str] = DEFAULT_EXCLUDED_PATH_SEGMENTS,
) -> list[Path]:
    """Filter files whose relative path contains a default excluded segment."""

    excluded_segments = set(excluded_path_segments)
    return [path for path in paths if not is_excluded_path(path, excluded_segments)]


def is_excluded_path(path: Path | str, excluded_path_segments: Iterable[str]) -> bool:
    """Return whether a relative path contains an excluded segment."""

    excluded_segments = set(excluded_path_segments)
    return any(part in excluded_segments for part in Path(path).parts)


def discover_filesystem_files(
    path: Path | str,
    *,
    pruned_dir_names: Iterable[str] = DEFAULT_PRUNED_DIR_NAMES,
) -> list[Path]:
    """Discover files below a path by filesystem traversal."""

    root = Path(path)
    pruned_names = set(pruned_dir_names)
    files: list[Path] = []

    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in pruned_names)
        current_path = Path(current)
        for filename in sorted(filenames):
            file_path = current_path / filename
            # Keep only regular files; this also skips broken symlinks from os.walk.
            if file_path.is_file() and filename not in pruned_names:
                relative_path = file_path.relative_to(root)
                files.append(relative_path)

    return files


def map_language(path: Path | str) -> str | None:
    """Map a fallback file path to a language name, or None when unknown."""

    file_path = Path(path)
    filename_language = LANGUAGES_BY_FILENAME.get(file_path.name.lower())
    if filename_language is not None:
        return filename_language
    return LANGUAGES_BY_SUFFIX.get(file_path.suffix.lower())


def count_physical_lines(path: Path | str) -> int | None:
    """Count physical lines as UTF-8 text, ignoring decode errors.

    Missing or unreadable files are skipped by returning None.
    """

    try:
        with Path(path).open(encoding="utf-8", errors="ignore") as handle:
            return sum(1 for _line in handle)
    except OSError:
        return None


def build_language_summary(path: Path | str) -> LanguageSummaryReport:
    """Build a deterministic fallback language summary for a repository path."""

    root = Path(path)
    return build_language_summary_from_file_rows(path=root, file_rows=build_file_rows(root))


def build_language_summary_from_file_rows(
    *,
    path: Path | str,
    file_rows: Iterable[FileRow],
) -> LanguageSummaryReport:
    """Build a deterministic fallback language summary from counted file rows."""

    root = Path(path)
    files_by_language: dict[str, int] = {}
    lines_by_language: dict[str, int] = {}

    for row in file_rows:
        files_by_language[row.language] = files_by_language.get(row.language, 0) + 1
        lines_by_language[row.language] = lines_by_language.get(row.language, 0) + row.code

    rows = [
        LanguageRow(
            language=language,
            files=files_by_language[language],
            blank=0,
            comment=0,
            code=lines_by_language[language],
        )
        for language in files_by_language
    ]
    rows.sort(key=lambda row: (-row.code, row.language))

    total_files = sum(row.files for row in rows)
    total_lines = sum(row.code for row in rows)
    if rows:
        rows.append(
            LanguageRow(language="SUM", files=total_files, blank=0, comment=0, code=total_lines)
        )

    return LanguageSummaryReport.from_rows(
        engine="python",
        path=root,
        language_rows=rows,
    )


def build_file_rows(path: Path | str) -> list[FileRow]:
    """Build fallback file-level rows using physical line counts."""

    root = Path(path)
    rows: list[FileRow] = []

    for relative_path in discover_files(root):
        language = map_language(relative_path)
        if language is None:
            continue

        physical_lines = count_physical_lines(root / relative_path)
        if physical_lines is None:
            continue

        rows.append(
            FileRow(
                language=language,
                path=relative_path.as_posix(),
                blank=0,
                comment=0,
                code=physical_lines,
            )
        )

    return rows
