"""Membership evidence must fit the capture cleanup's time and work budgets."""

from __future__ import annotations

import ctypes
import io
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from slopscope import _capture


def test_full_libproc_listing_has_a_fixed_allocation_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capacities = []

    def full(kind: int, pgid: int, buffer: Any, size: int) -> int:
        capacities.append(len(buffer))
        assert len(buffer) <= 256, "inspection exceeded its allocation ceiling"
        return size

    monkeypatch.setattr(_capture, "_MAX_GROUP_INSPECTION_ENTRIES", 256, raising=False)
    monkeypatch.setattr(_capture, "_darwin_proc_listpids", lambda: full)
    with pytest.raises(OSError, match="inspection"):
        _capture._darwin_group_members(42)
    assert capacities == [64, 128, 256]


def test_exact_libproc_capacity_requires_an_untruncated_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def listing(kind: int, pgid: int, buffer: Any, size: int) -> int:
        buffer[0], buffer[1] = 42, 43
        return 2 * ctypes.sizeof(ctypes.c_int)

    monkeypatch.setattr(_capture, "_darwin_proc_listpids", lambda: listing)
    assert _capture._darwin_group_members(42) == [42, 43]


def test_proc_inventory_is_streamed_and_counts_nonnumeric_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = Mock()
    entries.__enter__ = Mock(return_value=iter([Mock(name="entry")] * 5))
    entries.__exit__ = Mock(return_value=False)
    # Non-PID entries still consume enumeration work, but never open a stat file.
    entry = Mock()
    entry.name = "self"
    entries.__enter__.return_value = iter([entry] * 5)
    scandir = Mock(return_value=entries)
    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(os, "listdir", Mock(side_effect=AssertionError("unbounded listdir")))
    monkeypatch.setattr(_capture, "_MAX_GROUP_INSPECTION_ENTRIES", 4, raising=False)
    with pytest.raises(OSError, match="inspection"):
        _capture._linux_group_members(42)
    entries.__exit__.assert_called_once()


def test_proc_member_limit_does_not_return_partial_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for pid in range(42, 45):
        directory = tmp_path / str(pid)
        directory.mkdir()
        (directory / "stat").write_bytes(f"{pid} (synthetic) S 1 42 0".encode())
    monkeypatch.setattr(_capture, "_MAX_GROUP_INSPECTION_ENTRIES", 2, raising=False)
    with pytest.raises(OSError, match="inspection"):
        _capture._linux_group_members(42, str(tmp_path))
    monkeypatch.setattr(_capture, "_MAX_GROUP_INSPECTION_ENTRIES", 3)
    assert sorted(_capture._linux_group_members(42, str(tmp_path))) == [42, 43, 44]


def test_proc_read_finishing_after_deadline_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "stat").write_bytes(b"42 (synthetic) S 1 42 0")
    clock = iter([0.0, 0.0, 1.0])  # Before enumeration, before read, after read.
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    with pytest.raises(OSError, match="inspection deadline"):
        _capture._linux_group_members(42, str(tmp_path), deadline=0.5)


def test_proc_stat_read_has_a_byte_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = Mock()
    entry.name = "42"
    entries = Mock()
    entries.__enter__ = Mock(return_value=iter([entry]))
    entries.__exit__ = Mock(return_value=False)
    stat = Mock()
    stat.__enter__ = Mock(return_value=stat)
    stat.__exit__ = Mock(return_value=False)
    stat.read.return_value = b"x" * 4097
    monkeypatch.setattr(os, "scandir", Mock(return_value=entries))
    monkeypatch.setattr(_capture, "open", Mock(return_value=stat), raising=False)
    with pytest.raises(OSError, match="inspection stat byte limit"):
        _capture._linux_group_members(42)
    stat.read.assert_called_once_with(4097)
    stat.__exit__.assert_called_once()
    entries.__exit__.assert_called_once()


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_expired_inspection_does_no_enumeration(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 10.0)
    libproc = Mock(side_effect=AssertionError("expired inspection loaded libproc"))
    scandir = Mock(side_effect=AssertionError("expired inspection opened proc"))
    monkeypatch.setattr(_capture, "_darwin_proc_listpids", libproc)
    monkeypatch.setattr(os, "scandir", scandir)
    inspect = getattr(_capture, f"_{platform}_group_members")
    with pytest.raises(OSError, match="inspection"):
        inspect(42, deadline=10.0)
    libproc.assert_not_called()
    scandir.assert_not_called()


def test_libproc_result_after_deadline_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [0.0]

    def late(kind: int, pgid: int, buffer: Any, size: int) -> int:
        now[0] = 1.0
        buffer[0] = pgid
        return ctypes.sizeof(ctypes.c_int)

    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_capture, "_darwin_proc_listpids", lambda: late)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert _capture._group_has_other_members(42, deadline=0.5)
    now[0] = 0.0
    assert not _capture._group_has_other_members(42, deadline=0.5, unknown=False)


@pytest.mark.skipif(os.name != "posix", reason="POSIX group retirement")
@pytest.mark.parametrize("reserved", [True, False])
def test_incomplete_inspection_never_accepts_capture_or_signals_reaped_identity(
    monkeypatch: pytest.MonkeyPatch, reserved: bool
) -> None:
    now = [0.0]
    reaped = False
    signals = []
    process = Mock(pid=42, args=["synthetic"], stdout=io.BytesIO(), stderr=io.BytesIO())

    def reap(**kwargs: object) -> None:
        nonlocal reaped
        reaped = True

    def killpg(pgid: int, sig: int) -> None:
        assert not (reaped and sig), "terminating signal after reap"
        signals.append(sig)

    def full(kind: int, pgid: int, buffer: Any, size: int) -> int:
        assert len(buffer) <= 64
        return size

    def sleep(seconds: float) -> None:
        now[0] += seconds

    process.wait.side_effect = reap
    monkeypatch.setattr(_capture, "_wait_for_exit", Mock(return_value=reserved))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_capture, "_darwin_proc_listpids", lambda: full)
    monkeypatch.setattr(_capture, "_MAX_GROUP_INSPECTION_ENTRIES", 64, raising=False)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(os, "killpg", killpg)
    with pytest.raises(_capture.CaptureError, match="could not confirm synthetic"):
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    assert reaped
    assert [sig for sig in signals if sig] == ([signal.SIGTERM, signal.SIGKILL] if reserved else [])
    process.kill.assert_not_called()
