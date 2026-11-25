"""Monthly churn of Python lines from Git history for the composition report."""

from __future__ import annotations

import errno
import os
import re
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from datetime import date, timedelta
from pathlib import Path

from slopscope import classify, fallback
from slopscope._capture import CaptureError, capture_process
from slopscope._display import display_text
from slopscope._git_env import hardened_git_env
from slopscope.report import CompositionChurn, CompositionChurnMonth

DEFAULT_REF = "HEAD"
DEFAULT_MONTHS = 12
MAX_MONTHS = 1200
GIT_TIMEOUT_SECONDS = 30.0
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
GitCaptureError = CaptureError


# Commits are bucketed by their own time zone, which can be up to about a day away from the
# caller's; the Git-side cutoff keeps a margin and months are filtered exactly afterwards.
_CUTOFF_MARGIN = timedelta(days=2)
_GIT_LOG_STDERR_PREVIEW_BYTES = 64
KINDS = ("source", "tests", "other")


class _MalformedHistory(ValueError):
    """The captured Git history could not be parsed."""


def collect_churn(
    path: Path,
    *,
    ref: str = DEFAULT_REF,
    months: int = DEFAULT_MONTHS,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
    today: date | None = None,
    executable: str = "git",
) -> CompositionChurn:
    """Sum Python lines added and removed per month on ``ref``'s first-parent history.

    Only paths below ``path`` count, relative to it, with the same Python, exclude, include,
    and source/test rules as the report. Renames count toward the new path.
    """

    current = today or date.today()
    months = max(1, min(months, MAX_MONTHS))
    since = _month_start(current, months - 1)

    commit: str | None = None

    def skipped(reason: str) -> CompositionChurn:
        return CompositionChurn(
            ref=ref,
            commit=commit,
            months=months,
            since=since.strftime("%Y-%m"),
            status="skipped",
            reason=reason,
            rows=(),
        )

    try:
        inside = _git(executable, path, ["rev-parse", "--is-inside-work-tree"])
        if inside.returncode != 0 or inside.stdout.strip() != b"true":
            return skipped("not a Git repository")
        verified = _git(executable, path, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        if verified.returncode != 0:
            return skipped(f"unknown branch or ref {ref!r}")

        object_id = re.fullmatch(rb"([0-9a-fA-F]{40}|[0-9a-fA-F]{64})(?:\r?\n)?", verified.stdout)
        if object_id is None:
            return skipped("malformed git history: invalid commit ID")
        commit = object_id[1].decode("ascii")
        log_arguments = [
            "log",
            commit,
            "--first-parent",
            "--diff-merges=first-parent",
            "--numstat",
            "-M",
            "--relative",
            "-z",
            "--date=format:%Y-%m",
            "--format=@%cd",
        ]
        # --since-as-filter (Git 2.37+) checks every commit instead of stopping at the first older
        # one; without it, read the whole first-parent history and filter by month here.
        cutoff = since - _CUTOFF_MARGIN if since - date.min > _CUTOFF_MARGIN else since
        result = _git(
            executable, path, [*log_arguments, f"--since-as-filter={cutoff.isoformat()}", "--"]
        )
        if result.returncode != 0:
            result = _git(executable, path, [*log_arguments, "--"])
        if result.returncode != 0:
            preview = result.stderr[:_GIT_LOG_STDERR_PREVIEW_BYTES].decode(errors="replace")
            message = display_text(preview.strip())
            if len(result.stderr) > _GIT_LOG_STDERR_PREVIEW_BYTES:
                message += "..."
            return skipped(f"git log failed: {message or 'no error output'}")

    except subprocess.TimeoutExpired:
        return skipped(f"git timed out after {GIT_TIMEOUT_SECONDS:g} seconds")
    except GitCaptureError as exc:
        return skipped(str(exc))

    first_month = since.strftime("%Y-%m")
    totals: dict[str, tuple[list[int], list[int]]] = {
        month: ([0, 0, 0], [0, 0, 0]) for month in _months(since, current)
    }
    try:
        for month, relative_path, added, removed in _iter_numstat(result.stdout):
            if month not in totals:
                continue
            if fallback.map_language(relative_path) != "Python":
                continue
            if fallback.is_excluded_path(relative_path, excluded_paths):
                continue
            if include_globs and not fallback.matches_include_globs(relative_path, include_globs):
                continue
            kind = classify.classify_source_test(
                relative_path, source_dirs=source_dirs, test_dirs=test_dirs
            )
            index = KINDS.index(kind)
            totals[month][0][index] += added
            totals[month][1][index] += removed
    except _MalformedHistory as exc:
        return skipped(f"malformed git history: {exc}")

    return CompositionChurn(
        ref=ref,
        commit=commit,
        months=months,
        since=first_month,
        status="ok",
        reason=None,
        rows=tuple(
            CompositionChurnMonth(month=month, added=tuple(added), removed=tuple(removed))
            for month, (added, removed) in sorted(totals.items())
        ),
    )


def parse_numstat(output: bytes) -> list[tuple[str, str, int, int]]:
    """Parse ``git log -z --numstat --format=@%cd`` output into (month, path, added, removed).

    A rename has an empty path field followed by the old and the new path; the new path counts.
    Binary files (``-`` counts) are skipped.
    """
    return list(_iter_numstat(output))


def _nul_fields(output: bytes) -> Iterator[str]:
    if output and not output.endswith(b"\0"):
        raise _MalformedHistory("missing final NUL")
    start = 0
    while start < len(output):
        end = output.index(b"\0", start)
        yield output[start:end].decode("utf-8", errors="surrogateescape")
        start = end + 1


def _iter_numstat(output: bytes) -> Iterator[tuple[str, str, int, int]]:
    items = enumerate(_nul_fields(output), start=1)
    month = ""
    for index, field in items:
        item = field.lstrip("\n")
        if not item:
            continue
        if "\t" not in item:
            if not re.fullmatch(r"@[0-9]{4}-(?:0[1-9]|1[0-2])", item) or item[1:5] == "0000":
                raise _MalformedHistory(f"invalid month marker at record {index}")
            month = item[1:]
            continue
        fields = item.split("\t", 2)
        if len(fields) != 3 or not month:
            raise _MalformedHistory(f"invalid numeric record at record {index}")
        added, removed, relative_path = fields
        if not relative_path:
            _, old_path = next(items, (0, ""))
            _, relative_path = next(items, (0, ""))
            if not old_path or not relative_path:
                raise _MalformedHistory(f"incomplete rename at record {index}")
        if added == removed == "-":
            continue
        if not all(value.isascii() and value.isdecimal() for value in (added, removed)):
            raise _MalformedHistory(f"invalid counters at record {index}")
        try:
            counts = int(added), int(removed)
        except ValueError as exc:
            raise _MalformedHistory(f"invalid counters at record {index}") from exc
        yield month, relative_path, *counts


def _git(
    executable: str,
    path: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[bytes]:
    try:
        process = subprocess.Popen(
            [executable, "-C", str(path), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=hardened_git_env(),
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        # ENOENT can also mean that an existing executable's interpreter is missing.
        if (
            isinstance(exc, FileNotFoundError)
            and exc.filename in (None, executable)
            and shutil.which(executable) is None
        ):
            raise GitCaptureError("git was not found") from exc
        error = errno.errorcode.get(exc.errno or 0, "unknown error")
        raise GitCaptureError(f"could not start git ({error})") from exc
    return capture_process(
        process, timeout=GIT_TIMEOUT_SECONDS, max_bytes=MAX_GIT_OUTPUT_BYTES, label="git"
    )


def _month_start(current: date, months_back: int) -> date:
    # Month index 12 is January of year 1, the earliest date Python can represent.
    month_index = max(12, current.year * 12 + current.month - 1 - months_back)
    return date(month_index // 12, month_index % 12 + 1, 1)


def _months(since: date, current: date) -> list[str]:
    months: list[str] = []
    month_index = since.year * 12 + since.month - 1
    last = current.year * 12 + current.month - 1
    while month_index <= last:
        months.append(f"{month_index // 12:04d}-{month_index % 12 + 1:02d}")
        month_index += 1
    return months
