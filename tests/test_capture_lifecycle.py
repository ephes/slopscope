"""Owned process identities must survive through the final cleanup signal."""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys

import pytest

from slopscope import _capture


@pytest.mark.skipif(os.name != "posix", reason="POSIX group identity reservation")
@pytest.mark.parametrize("returncode", [0, 7])
def test_group_identity_is_retired_before_reap(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", f"print('complete'); raise SystemExit({returncode})"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    wait, poll, killpg = process.wait, process.poll, os.killpg
    reaped = False
    signals: list[int] = []
    reused_signals: list[int] = []

    def reap(*, timeout: float | None = None) -> int:
        nonlocal reaped
        result = wait(timeout=timeout)
        reaped = True  # Model immediate reuse of the numeric process/group identity.
        return result

    def poll_and_reap() -> int | None:
        nonlocal reaped
        result = poll()
        reaped |= result is not None
        return result

    def signal_group(pid: int, sig: int) -> None:
        if reaped and sig:
            reused_signals.append(sig)
            return  # An unrelated new group would receive this signal.
        if sig:
            signals.append(sig)
        killpg(pid, sig)

    monkeypatch.setattr(process, "wait", reap)
    monkeypatch.setattr(process, "poll", poll_and_reap)
    monkeypatch.setattr(os, "killpg", signal_group)
    try:
        result = _capture.capture_process(process, timeout=3, max_bytes=100, label="synthetic")
        assert result.returncode == returncode
        assert result.stdout == b"complete\n"
        assert reaped
        assert not reused_signals, "cleanup signalled a reused process-group identity"
        assert signals and signals[-1] == signal.SIGKILL
    finally:
        if not reaped:
            killpg(process.pid, signal.SIGKILL)
            wait(timeout=3)


@pytest.mark.parametrize("returncode", [0, 7])
def test_reused_group_is_only_probed_and_refused(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    process = Mock(
        pid=12345,
        args=["synthetic"],
        returncode=None,
        stdout=io.BytesIO(b"result"),
        stderr=io.BytesIO(),
    )
    reaped = False
    terminating_signals: list[int] = []

    def reap(*, timeout: float) -> int:
        nonlocal reaped
        reaped = True
        process.returncode = returncode
        return returncode

    def signal_group(pid: int, sig: int) -> None:
        if sig:
            assert not reaped, "terminating signal sent to reused group"
            terminating_signals.append(sig)
        # After reap, model an unrelated group immediately taking the same PGID.

    process.wait.side_effect = reap
    monkeypatch.setattr(_capture, "os", SimpleNamespace(name="posix", killpg=signal_group))
    monkeypatch.setattr(_capture, "_wait_for_exit", Mock())
    monkeypatch.setattr(_capture, "_GROUP_TERM_SECONDS", 0.01)
    monkeypatch.setattr(_capture, "_GROUP_KILL_SECONDS", 0.01)
    with pytest.raises(_capture.CaptureError, match="could not confirm"):
        _capture.capture_process(process, timeout=3, max_bytes=100, label="synthetic")
    assert reaped
    assert terminating_signals == [signal.SIGTERM, signal.SIGKILL]
    process.kill.assert_not_called()
