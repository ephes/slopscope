"""Owned process identities must survive through the final cleanup signal."""

from __future__ import annotations

import io
import os
import select
import signal
import subprocess
import sys
from pathlib import Path

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


@pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor termination")
@pytest.mark.parametrize("command", ["cloc", "git"])
def test_supervisor_sigterm_retires_owned_command(tmp_path: Path, command: str) -> None:
    import contextlib
    import time

    ready = tmp_path / "child.pid"
    completed = tmp_path / "survived"
    wrapper = tmp_path / "synthetic-command"
    holder = (
        "import os,pathlib,signal,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
        "time.sleep(1.5); pathlib.Path(sys.argv[2]).touch(); time.sleep(30)"
    )
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os,pathlib,signal,subprocess,sys,time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path({str(tmp_path / 'wrapper.pid')!r}).write_text(str(os.getpid()))\n"
        f"subprocess.Popen([sys.executable, '-c', {holder!r}, {str(ready)!r}, "
        f"{str(completed)!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    supervisor_code = (
        "import sys; from pathlib import Path; from slopscope import cloc, fallback; "
        "cloc.run_command([sys.argv[1]]) if sys.argv[2] == 'cloc' else "
        "fallback.run_git_ls_files(Path(sys.argv[3]), executable=sys.argv[1])"
    )
    supervisor = subprocess.Popen(
        [sys.executable, "-c", supervisor_code, str(wrapper), command, str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    cleaned = False
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "synthetic child did not reach the capture barrier"
        started = time.monotonic()
        supervisor.send_signal(signal.SIGTERM)  # Only the supervisor receives TERM.
        out, err = supervisor.communicate(timeout=7)
        assert supervisor.returncode == 128 + signal.SIGTERM, (out, err)
        assert time.monotonic() - started < 7
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "wrapper.pid").read_text()), 0)
        time.sleep(1.7)
        assert not completed.exists(), "owned descendant survived supervisor termination"
        cleaned = True
    finally:
        if not cleaned and (tmp_path / "wrapper.pid").exists():
            with contextlib.suppress(OSError):
                os.killpg(int((tmp_path / "wrapper.pid").read_text()), signal.SIGKILL)
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.communicate(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="scoped POSIX signal handler")
@pytest.mark.parametrize("outcome", ["success", "timeout", "interrupt", "exit"])
def test_capture_restores_previous_sigterm_handler(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    from types import FrameType
    from unittest.mock import Mock

    def previous_handler(signum: int, frame: FrameType | None) -> None:
        pytest.fail("previous handler ran inside owned capture")

    original = signal.signal(signal.SIGTERM, previous_handler)
    process = Mock(
        pid=12345, args=["synthetic"], returncode=0, stdout=io.BytesIO(), stderr=io.BytesIO()
    )
    errors = {
        "timeout": subprocess.TimeoutExpired("synthetic", 1),
        "interrupt": KeyboardInterrupt(),
        "exit": SystemExit(9),
    }

    def observe(*args: object, **kwargs: object) -> None:
        assert signal.getsignal(signal.SIGTERM) is not previous_handler
        if outcome in errors:
            raise errors[outcome]

    monkeypatch.setattr(_capture, "_wait_for_exit", observe)
    monkeypatch.setattr(_capture, "_retire_group", Mock(return_value=None))
    monkeypatch.setattr(os, "killpg", Mock())
    try:
        if outcome == "success":
            _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
        else:
            with pytest.raises(type(errors[outcome])) as raised:
                _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
            assert raised.value is errors[outcome]
        assert signal.getsignal(signal.SIGTERM) is previous_handler
    finally:
        signal.signal(signal.SIGTERM, original)


@pytest.mark.skipif(os.name != "posix", reason="scoped POSIX signal handler")
@pytest.mark.parametrize("during_cleanup", [False, True])
def test_repeated_sigterm_does_not_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch, during_cleanup: bool
) -> None:
    from unittest.mock import Mock

    previous = signal.getsignal(signal.SIGTERM)
    process = Mock(pid=12345, args=["synthetic"], stdout=io.BytesIO(), stderr=io.BytesIO())

    def request_term(*args: object, **kwargs: object) -> None:
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    def observe(*args: object, **kwargs: object) -> None:
        if during_cleanup:
            raise subprocess.TimeoutExpired("synthetic", 1)
        request_term()

    monkeypatch.setattr(_capture, "_wait_for_exit", observe)
    kill_group = Mock(side_effect=request_term)
    monkeypatch.setattr(os, "killpg", kill_group)
    process.kill.side_effect = request_term
    process.wait.side_effect = request_term
    with pytest.raises(SystemExit) as raised:
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    assert raised.value.code == 128 + signal.SIGTERM
    kill_group.assert_called_once_with(process.pid, signal.SIGKILL)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert process.stdout.closed and process.stderr.closed
    assert signal.getsignal(signal.SIGTERM) is previous


def test_worker_thread_capture_leaves_signal_policy_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import Mock

    process = Mock(
        pid=12345,
        args=["synthetic"],
        returncode=0,
        stdout=io.BytesIO(b"result"),
        stderr=io.BytesIO(),
    )
    monkeypatch.setattr(_capture, "_wait_for_exit", Mock())
    monkeypatch.setattr(_capture, "_retire_group", Mock(return_value=None))
    install = Mock(side_effect=AssertionError("worker capture changed signal policy"))
    monkeypatch.setattr(signal, "signal", install)
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            _capture.capture_process, process, timeout=1, max_bytes=100, label="synthetic"
        ).result(timeout=3)
    assert result.returncode == 0 and result.stdout == b"result"
    install.assert_not_called()


@pytest.mark.skipif(not hasattr(select, "kqueue"), reason="macOS kqueue observation")
@pytest.mark.parametrize("already_exited", [False, True])
def test_kqueue_observes_exit_without_reaping(
    monkeypatch: pytest.MonkeyPatch, already_exited: bool
) -> None:
    import time

    monkeypatch.delattr(os, "waitid", raising=False)
    process = subprocess.Popen(
        [sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(.1)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if already_exited:
        time.sleep(0.2)
    waited = False
    wait = process.wait
    killpg = os.killpg

    def reap(*, timeout: float) -> int:
        nonlocal waited
        waited = True
        return wait(timeout=timeout)

    def signal_group(pid: int, sig: int) -> None:
        if sig:
            assert not waited
        killpg(pid, sig)

    monkeypatch.setattr(process, "wait", reap)
    monkeypatch.setattr(os, "killpg", signal_group)
    result = _capture.capture_process(process, timeout=3, max_bytes=100, label="synthetic")
    assert result.returncode == 0 and waited
