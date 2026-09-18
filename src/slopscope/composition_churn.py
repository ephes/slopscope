"""Monthly churn of Python lines from Git history for the composition report."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

from slopscope import classify, fallback
from slopscope.report import CompositionChurn, CompositionChurnMonth

DEFAULT_REF = "HEAD"
DEFAULT_MONTHS = 12
MAX_MONTHS = 1200
# Commits are bucketed by their own time zone, which can be up to about a day away from the
# caller's; the Git-side cutoff keeps a margin and months are filtered exactly afterwards.
_CUTOFF_MARGIN = timedelta(days=2)
KINDS = ("source", "tests", "other")


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

    def skipped(reason: str) -> CompositionChurn:
        return CompositionChurn(
            ref=ref,
            months=months,
            since=since.strftime("%Y-%m"),
            status="skipped",
            reason=reason,
            rows=(),
        )

    inside = _git(executable, path, ["rev-parse", "--is-inside-work-tree"])
    if inside is None:
        return skipped("git was not found")
    if inside.returncode != 0 or inside.stdout.strip() != b"true":
        return skipped("not a Git repository")
    verified = _git(executable, path, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    if verified is None or verified.returncode != 0:
        return skipped(f"unknown branch or ref {ref!r}")

    log_arguments = [
        "log",
        ref,
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
    if result is not None and result.returncode != 0:
        result = _git(executable, path, [*log_arguments, "--"])
    if result is None or result.returncode != 0:
        message = "" if result is None else result.stderr.decode(errors="replace").strip()
        return skipped(f"git log failed: {message or 'no error output'}")

    first_month = since.strftime("%Y-%m")
    totals: dict[str, tuple[list[int], list[int]]] = {
        month: ([0, 0, 0], [0, 0, 0]) for month in _months(since, current)
    }
    for month, relative_path, added, removed in parse_numstat(result.stdout):
        if month < first_month or month not in totals:
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

    return CompositionChurn(
        ref=ref,
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

    items = output.decode("utf-8", errors="surrogateescape").split("\0")
    rows: list[tuple[str, str, int, int]] = []
    month = ""
    index = 0
    while index < len(items):
        item = items[index].lstrip("\n")
        index += 1
        if not item:
            continue
        if "\t" not in item:
            if item.startswith("@"):
                month = item[1:]
            continue
        fields = item.split("\t", 2)
        if len(fields) != 3:
            continue
        added, removed, relative_path = fields
        if not relative_path:
            if index + 1 >= len(items):
                break
            relative_path = items[index + 1]
            index += 2
        if added == "-" or removed == "-":
            continue
        try:
            rows.append((month, relative_path, int(added), int(removed)))
        except ValueError:
            continue
    return rows


def _git(
    executable: str,
    path: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            [executable, "-C", str(path), *arguments],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None


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
