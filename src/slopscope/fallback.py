"""Pure-Python fallback file discovery and physical-line summaries."""

from __future__ import annotations

import codecs
import os
import stat
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from slopscope._capture import CaptureError, capture_process
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


GIT_DISCOVERY_TIMEOUT_SECONDS = 30.0
MAX_GIT_DISCOVERY_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_DISCOVERY_ENTRIES = 200_000
MAX_DISCOVERY_FILES = 100_000
MAX_DISCOVERY_DIRECTORIES = 20_000  # Includes the selected root and queued directories.
MAX_DISCOVERY_DEPTH = 128
PHYSICAL_READ_CHUNK = 64 * 1024
MAX_PHYSICAL_SOURCE_BYTES = 16 * 1024 * 1024
MAX_PHYSICAL_TOTAL_BYTES = 128 * 1024 * 1024


class GitDiscoveryError(RuntimeError):
    """Git discovery failed without a safe file list."""


class CountingError(RuntimeError):
    """An included source file could not be counted."""


@dataclass(frozen=True)
class GitLsFilesResult:
    """Completed git file-listing process data."""

    returncode: int
    stdout: bytes


def build_git_ls_files_command(path: Path | str, executable: str = "git") -> list[str]:
    """Build the git command used to discover tracked files under a path."""

    return [executable, "-C", str(path), "ls-files", "-z", "--", "."]


def run_git_ls_files(path: Path | str, executable: str = "git") -> GitLsFilesResult:
    """Run bounded Git discovery; unavailable/nonzero Git permits filesystem fallback."""

    try:
        process = subprocess.Popen(
            build_git_ls_files_command(path, executable=executable),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError:
        return GitLsFilesResult(returncode=1, stdout=b"")
    try:
        result = capture_process(
            process,
            timeout=GIT_DISCOVERY_TIMEOUT_SECONDS,
            max_bytes=MAX_GIT_DISCOVERY_OUTPUT_BYTES,
            label="Git file discovery",
        )
    except subprocess.TimeoutExpired as exc:
        raise GitDiscoveryError(
            f"Git file discovery timed out after {GIT_DISCOVERY_TIMEOUT_SECONDS:g} seconds: {path}"
        ) from exc
    except CaptureError as exc:
        raise GitDiscoveryError(f"{exc}: {path}") from exc
    return GitLsFilesResult(returncode=result.returncode, stdout=result.stdout)


def parse_git_ls_files_output(output: bytes) -> list[Path]:
    """Parse NUL-delimited git file output as relative paths."""

    # Deduplicate the exact spelling, without case folding or Unicode normalization.
    return [Path(os.fsdecode(part)) for part in dict.fromkeys(output.split(b"\0")) if part]


def discover_files(
    path: Path | str,
    *,
    excluded_paths: Iterable[str] = DEFAULT_EXCLUDED_PATH_SEGMENTS,
    include_globs: Iterable[str] = (),
) -> list[Path]:
    """Discover repository files using git when available, then filesystem traversal."""

    root = Path(path)
    excluded_paths_tuple = tuple(excluded_paths)
    git_result = run_git_ls_files(root)
    if git_result.returncode == 0:
        selected = filter_included_paths(
            filter_excluded_paths(
                parse_git_ls_files_output(git_result.stdout),
                excluded_path_segments=excluded_paths_tuple,
            ),
            include_globs=include_globs,
        )
        return [path for path in selected if not _has_source_symlink(root, path)]
    return filter_included_paths(
        filter_excluded_paths(
            discover_filesystem_files(root, pruned_dir_names=excluded_paths_tuple),
            excluded_path_segments=excluded_paths_tuple,
        ),
        include_globs=include_globs,
    )


def filter_excluded_paths(
    paths: Iterable[Path],
    *,
    excluded_path_segments: Iterable[str] = DEFAULT_EXCLUDED_PATH_SEGMENTS,
) -> list[Path]:
    """Filter files whose relative path contains a default excluded segment."""

    excluded_segments = set(excluded_path_segments)
    return [path for path in paths if not is_excluded_path(path, excluded_segments)]


def is_excluded_path(path: Path | str, excluded_path_segments: Iterable[str]) -> bool:
    """Return whether a relative path contains an excluded path."""

    parts = _path_parts(path)
    for excluded in excluded_path_segments:
        excluded_parts = _path_parts(excluded)
        if not excluded_parts:
            continue
        if len(excluded_parts) == 1:
            if excluded_parts[0] in parts:
                return True
        elif _contains_subpath(parts, excluded_parts):
            return True
    return False


def filter_included_paths(
    paths: Iterable[Path],
    *,
    include_globs: Iterable[str] = (),
) -> list[Path]:
    """Filter files by configured include globs when any are provided."""

    globs = tuple(include_globs)
    if not globs:
        return list(paths)
    return [path for path in paths if matches_include_globs(path, globs)]


def matches_include_globs(path: Path | str, include_globs: Iterable[str]) -> bool:
    """Return whether a relative path matches any configured include glob."""

    relative_path = Path(path)
    path_text = relative_path.as_posix()
    return any(
        relative_path.match(pattern) or fnmatch(path_text, pattern) for pattern in include_globs
    )


def _has_source_symlink(root: Path, relative_path: Path) -> bool:
    """Reject source links, including linked directories below the selected root."""

    try:
        return any(
            (root / part).is_symlink()
            for part in (relative_path, *relative_path.parents)
            if part != Path(".")
        )
    except OSError:
        # An uninspectable source cannot be admitted safely.
        return True


def discover_filesystem_files(
    path: Path | str,
    *,
    pruned_dir_names: Iterable[str] = DEFAULT_PRUNED_DIR_NAMES,
) -> list[Path]:
    """Discover files below a path by filesystem traversal."""

    root = Path(path)
    pruned_names = set(pruned_dir_names)
    files: list[Path] = []

    pending = [root]
    entries = 0
    directories = 1
    while pending:
        current = pending.pop()
        subdirs: list[Path] = []
        current_files: list[Path] = []
        try:
            with os.scandir(current) as listing:
                for entry in listing:
                    entries += 1
                    if entries > MAX_DISCOVERY_ENTRIES:
                        raise CountingError(
                            "incomplete analysis: filesystem discovery entry limit exceeded"
                        )
                    relative = (current / entry.name).relative_to(root)
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if is_excluded_path(relative, pruned_names):
                            continue
                        directories += 1
                        if directories > MAX_DISCOVERY_DIRECTORIES:
                            raise CountingError(
                                "incomplete analysis: filesystem discovery directory limit exceeded"
                            )
                        if len(relative.parts) > MAX_DISCOVERY_DEPTH:
                            raise CountingError(
                                "incomplete analysis: filesystem discovery depth limit exceeded"
                            )
                        subdirs.append(current / entry.name)
                    elif entry.is_file(follow_symlinks=False) and entry.name not in pruned_names:
                        if len(files) + len(current_files) >= MAX_DISCOVERY_FILES:
                            raise CountingError(
                                "incomplete analysis: filesystem discovery file limit exceeded"
                            )
                        current_files.append(relative)
        except OSError as error:
            affected = repr(str(error.filename or current))[:240]
            raise CountingError(f"incomplete analysis: could not traverse {affected}") from error
        files.extend(sorted(current_files))
        pending.extend(sorted(subdirs, reverse=True))

    return files


def map_language(path: Path | str) -> str | None:
    """Map a fallback file path to a language name, or None when unknown."""

    file_path = Path(path)
    filename_language = LANGUAGES_BY_FILENAME.get(file_path.name.lower())
    if filename_language is not None:
        return filename_language
    return LANGUAGES_BY_SUFFIX.get(file_path.suffix.lower())


@dataclass
class PhysicalByteBudget:
    """Remaining raw source bytes for one physical-line report."""

    remaining: int = field(default_factory=lambda: MAX_PHYSICAL_TOTAL_BYTES)


def count_physical_lines(
    path: Path | str, *, budget: PhysicalByteBudget | None = None
) -> int | None:
    """Count UTF-8/ignored-error universal-newline lines in bounded binary chunks.

    None signals an I/O failure; exceeding a source or report byte budget raises CountingError.
    """

    source_bytes = 0
    lines = 0
    tail = False
    previous_cr = False
    decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    try:
        with Path(path).open("rb") as handle:
            while True:
                allowance = MAX_PHYSICAL_SOURCE_BYTES - source_bytes
                if budget is not None:
                    allowance = min(allowance, budget.remaining)
                chunk = handle.read(min(PHYSICAL_READ_CHUNK, allowance + 1))
                source_bytes += len(chunk)
                if source_bytes > MAX_PHYSICAL_SOURCE_BYTES:
                    affected = repr(str(path))[:240]
                    raise CountingError(
                        f"incomplete analysis: source byte limit exceeded: {affected}"
                    )
                if budget is not None:
                    if len(chunk) > budget.remaining:
                        affected = repr(str(path))[:240]
                        raise CountingError(
                            f"incomplete analysis: total source byte limit exceeded: {affected}"
                        )
                    budget.remaining -= len(chunk)
                text = decoder.decode(chunk, final=not chunk)
                if text:
                    # CR already ended a line in the previous chunk; consume only its LF partner.
                    if previous_cr and text.startswith("\n"):
                        text = text[1:]
                    previous_cr = text.endswith("\r")
                    if text:
                        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
                        lines += normalized.count("\n")
                        tail = not normalized.endswith("\n")
                if not chunk:
                    return lines + int(tail)
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


def build_file_rows(
    path: Path | str,
    *,
    excluded_paths: Iterable[str] = DEFAULT_EXCLUDED_PATH_SEGMENTS,
    include_globs: Iterable[str] = (),
    include_languages: Iterable[str] = (),
    exclude_languages: Iterable[str] = (),
) -> list[FileRow]:
    """Build fallback file-level rows using physical line counts."""

    root = Path(path)
    rows: list[FileRow] = []
    budget = PhysicalByteBudget()
    included_languages = set(include_languages)
    excluded_languages = set(exclude_languages)

    for relative_path in discover_files(
        root,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
    ):
        language = map_language(relative_path)
        if language is None:
            continue
        if included_languages and language not in included_languages:
            continue
        if language in excluded_languages:
            continue

        file_path = root / relative_path
        # Tracked paths absent from the working tree (deletions, sparse checkouts) and
        # non-regular paths (directories, submodules, special files) are skipped unopened.
        if _is_missing_or_not_regular(file_path):
            continue
        physical_lines = count_physical_lines(file_path, budget=budget)
        if physical_lines is None:
            if _is_missing_or_not_regular(file_path):
                # Removed or replaced while counting: same as absent at discovery.
                continue
            raise CountingError(f"incomplete analysis: could not read included file {file_path}")

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


def _is_missing_or_not_regular(path: Path) -> bool:
    """Return True only for a path known to be absent or not a regular file.

    A path that cannot be inspected is not known to be either, so it is reported.
    """

    try:
        return not stat.S_ISREG(path.lstat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False


def _path_parts(path: Path | str) -> tuple[str, ...]:
    file_path = Path(path)
    return tuple(part for part in file_path.parts if part not in ("", ".", file_path.anchor))


def _contains_subpath(parts: tuple[str, ...], subpath: tuple[str, ...]) -> bool:
    if len(subpath) > len(parts):
        return False
    return any(
        parts[index : index + len(subpath)] == subpath
        for index in range(len(parts) - len(subpath) + 1)
    )
