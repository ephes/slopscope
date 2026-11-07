"""Monthly churn of Python lines from Git history for the composition report."""

from __future__ import annotations

import contextlib
import io
import os
import re
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import BinaryIO

from slopscope import classify, fallback
from slopscope.report import CompositionChurn, CompositionChurnMonth

DEFAULT_REF = "HEAD"
DEFAULT_MONTHS = 12
MAX_MONTHS = 1200
GIT_TIMEOUT_SECONDS = 30.0
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
_CAPTURE_CHUNK_BYTES = 64 * 1024


class GitCaptureError(RuntimeError):
    """Git output could not be captured completely within the resource budget."""


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

    try:
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

    except subprocess.TimeoutExpired:
        return skipped(f"git timed out after {GIT_TIMEOUT_SECONDS:g} seconds")
    except GitCaptureError as exc:
        return skipped(str(exc))

    first_month = since.strftime("%Y-%m")
    totals: dict[str, tuple[list[int], list[int]]] = {
        month: ([0, 0, 0], [0, 0, 0]) for month in _months(since, current)
    }
    try:
        records = parse_numstat(result.stdout)
    except ValueError as exc:
        return skipped(f"malformed git history: {exc}")
    for month, relative_path, added, removed in records:
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

    if output and not output.endswith(b"\0"):
        raise ValueError("missing final NUL")
    items = output.decode("utf-8", errors="surrogateescape").split("\0")[:-1]
    rows: list[tuple[str, str, int, int]] = []
    month = ""
    index = 0
    while index < len(items):
        item = items[index].lstrip("\n")
        index += 1
        if not item:
            continue
        if "\t" not in item:
            if not re.fullmatch(r"@[0-9]{4}-(?:0[1-9]|1[0-2])", item) or item[1:5] == "0000":
                raise ValueError(f"invalid month marker at record {index}")
            month = item[1:]
            continue
        fields = item.split("\t", 2)
        if len(fields) != 3 or not month:
            raise ValueError(f"invalid numeric record at record {index}")
        added, removed, relative_path = fields
        if not relative_path:
            if index + 1 >= len(items) or not items[index] or not items[index + 1]:
                raise ValueError(f"incomplete rename at record {index}")
            relative_path = items[index + 1]
            index += 2
        if added == removed == "-":
            continue
        if not all(value.isascii() and value.isdecimal() for value in (added, removed)):
            raise ValueError(f"invalid counters at record {index}")
        try:
            counts = int(added), int(removed)
        except ValueError as exc:
            raise ValueError(f"invalid counters at record {index}") from exc
        rows.append((month, relative_path, *counts))
    return rows


def _git(
    executable: str,
    path: Path,
    arguments: list[str],
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        process = subprocess.Popen(
            [executable, "-C", str(path), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError:
        return None
    # Each reader retains only its share of one combined byte budget. Separate readers
    # prevent a full stderr pipe from blocking stdout (and work on Windows too).
    buffers = (bytearray(), bytearray())
    failures: list[GitCaptureError] = []
    lock = threading.Lock()
    changed = threading.Event()
    stopped = threading.Event()

    def read_pipe(pipe: BinaryIO, target: bytearray) -> None:
        try:
            # read1 returns available pipe bytes without waiting to fill the chunk.
            read = pipe.read1 if isinstance(pipe, io.BufferedReader) else pipe.read
            while not stopped.is_set():
                chunk = read(_CAPTURE_CHUNK_BYTES)
                if not chunk:
                    break
                with lock:
                    if stopped.is_set():
                        break
                    if len(buffers[0]) + len(buffers[1]) + len(chunk) > MAX_GIT_OUTPUT_BYTES:
                        failures.append(
                            GitCaptureError(f"git output exceeded {MAX_GIT_OUTPUT_BYTES} bytes")
                        )
                        stopped.set()
                        changed.set()
                        break
                    target.extend(chunk)
        except BaseException:
            # Worker failures must never turn a truncated capture into success.
            with lock:
                failures.append(GitCaptureError("could not read git output"))
                stopped.set()
        finally:
            # Only the reader closes its own pipe, after releasing its read lock.
            with contextlib.suppress(OSError):
                pipe.close()
            changed.set()

    assert process.stdout is not None and process.stderr is not None
    threads = [
        threading.Thread(target=read_pipe, args=(pipe, buffer), daemon=True)
        for pipe, buffer in zip((process.stdout, process.stderr), buffers, strict=True)
    ]
    started: list[threading.Thread] = []
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    try:
        for thread in threads:
            thread.start()
            started.append(thread)
        while any(thread.is_alive() for thread in started):
            with lock:
                if failures:
                    raise failures[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, GIT_TIMEOUT_SECONDS)
            changed.wait(min(remaining, 0.05))
            changed.clear()
        if failures:
            raise failures[0]
        process.wait(timeout=max(0, deadline - time.monotonic()))
    except BaseException:
        stopped.set()
        if os.name == "posix":
            # Kill the group even if the wrapper has exited but a child still owns its pipes.
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
        with contextlib.suppress(OSError):
            process.kill()
        # Bound reader cleanup too: a surviving descendant can retain a pipe. Never
        # close a pipe from this thread while its reader might hold the read lock.
        drain_deadline = time.monotonic() + 1
        try:
            for thread in started:
                thread.join(timeout=max(0, drain_deadline - time.monotonic()))
        finally:
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
        raise
    return subprocess.CompletedProcess(
        process.args, process.returncode, bytes(buffers[0]), bytes(buffers[1])
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
