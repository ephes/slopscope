"""File discovery helpers for the planned pure-Python fallback."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PRUNED_DIR_NAMES = frozenset({".git", ".hg", ".svn", "__pycache__"})


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
        return parse_git_ls_files_output(git_result.stdout)
    return discover_filesystem_files(root)


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
            if file_path.is_file():
                files.append(file_path.relative_to(root))

    return files
