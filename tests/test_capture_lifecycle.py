"""Owned process identities must survive through the final cleanup signal."""

from __future__ import annotations

import contextlib
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
    # Fake PIDs: keep membership unknown so the probe semantics stay under test.
    monkeypatch.setattr(_capture, "_group_has_other_members", lambda pgid, **_: True)
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

    def observe(*args: object, **kwargs: object) -> bool:
        assert signal.getsignal(signal.SIGTERM) is not previous_handler
        if outcome in errors:
            raise errors[outcome]
        return True  # The exited leader still reserves its identity.

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

    def observe(*args: object, **kwargs: object) -> bool:
        if during_cleanup:
            raise subprocess.TimeoutExpired("synthetic", 1)
        request_term()
        return True

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

    sent: list[int] = []

    def signal_group(pid: int, sig: int) -> None:
        if sig:
            assert not waited
            sent.append(sig)
        killpg(pid, sig)

    monkeypatch.setattr(process, "wait", reap)
    monkeypatch.setattr(os, "killpg", signal_group)
    result = _capture.capture_process(process, timeout=3, max_bytes=100, label="synthetic")
    assert result.returncode == 0 and waited
    # The kqueue fallback keeps the identity reserved through the final KILL attempt.
    assert sent and sent[-1] == signal.SIGKILL


@pytest.mark.skipif(
    not (sys.platform == "darwin" or sys.platform.startswith("linux")),
    reason="group membership listing is implemented for macOS and Linux",
)
def test_leader_only_group_skips_term_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    monkeypatch.setattr(_capture, "_GROUP_TERM_SECONDS", 5.0)
    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    started = time.monotonic()
    result = _capture.capture_process(process, timeout=10, max_bytes=100, label="synthetic")
    assert result.returncode == 0
    assert time.monotonic() - started < 2.5


@pytest.mark.skipif(
    not (sys.platform == "darwin" or sys.platform.startswith("linux")),
    reason="group membership listing is implemented for macOS and Linux",
)
def test_group_membership_reports_redirected_descendant_until_it_exits() -> None:
    import time

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys; subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(30)'], stdout=subprocess.DEVNULL, "
            "stderr=subprocess.DEVNULL)",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        process.communicate(timeout=10)
        assert _capture._group_has_other_members(process.pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while _capture._group_has_other_members(process.pid) and time.monotonic() < deadline:
            time.sleep(0.01)
    assert not _capture._group_has_other_members(process.pid)


def test_group_membership_is_unknown_when_listing_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    failing = Mock(side_effect=OSError("listing failed"))
    monkeypatch.setattr(_capture, "_darwin_group_members", failing)
    monkeypatch.setattr(_capture, "_linux_group_members", failing)
    for platform in ("darwin", "linux", "freebsd14"):
        monkeypatch.setattr(sys, "platform", platform)
        assert _capture._group_has_other_members(4242)
        assert not _capture._group_has_other_members(4242, unknown=False)


def test_linux_group_members_parse_proc_stat(tmp_path: Path) -> None:
    entries = {
        "100": b"100 (leader) Z 1 100 100 0",
        "101": b"101 (odd ) name) S 1 100 100 0",
        "102": b"102 (other) S 1 555 555 0",
        "self": b"ignored",
    }
    for name, stat in entries.items():
        (tmp_path / name).mkdir()
        (tmp_path / name / "stat").write_bytes(stat)
    (tmp_path / "103").mkdir()  # Exited between listing and reading.
    assert sorted(_capture._linux_group_members(100, str(tmp_path))) == [100, 101]


@pytest.mark.skipif(os.name != "posix", reason="POSIX auto-reap semantics")
@pytest.mark.parametrize("observer", ["waitid", "kqueue"])
def test_auto_reaped_leader_still_retires_lingering_descendants(
    monkeypatch: pytest.MonkeyPatch, observer: str
) -> None:
    if observer == "kqueue":
        if not hasattr(select, "kqueue"):
            pytest.skip("macOS kqueue observation")
        monkeypatch.delattr(os, "waitid", raising=False)
    elif not hasattr(os, "waitid"):
        pytest.skip("waitid unavailable")
    child = (
        "import signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(30)"
    )
    # The wrapper exits only once its redirected child ignores TERM, so KILL must retire it.
    wrapper = (
        "import subprocess,sys; child = subprocess.Popen([sys.executable, '-c', "
        f"{child!r}], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL); "
        "child.stdout.readline()"
    )
    previous = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", wrapper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            # The kernel reaped the leader, so its real exit status is unknown: refuse it.
            with pytest.raises(_capture.CaptureError, match="could not observe synthetic exit"):
                _capture.capture_process(process, timeout=10, max_bytes=100, label="synthetic")
            # The surviving member still reserved the group, so it was retired first.
            with pytest.raises(ProcessLookupError):
                os.killpg(process.pid, 0)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)  # Cleanup only if the test failed.
    finally:
        signal.signal(signal.SIGCHLD, previous)


@pytest.mark.skipif(os.name != "posix", reason="POSIX auto-reap semantics")
@pytest.mark.parametrize("observer", ["waitid", "kqueue"])
def test_auto_reaped_leader_without_members_is_never_signalled(
    monkeypatch: pytest.MonkeyPatch, observer: str
) -> None:
    if observer == "kqueue":
        if not hasattr(select, "kqueue"):
            pytest.skip("macOS kqueue observation")
        monkeypatch.delattr(os, "waitid", raising=False)
    elif not hasattr(os, "waitid"):
        pytest.skip("waitid unavailable")
    killpg = os.killpg
    sent: list[int] = []

    def signal_group(pid: int, sig: int) -> None:
        sent.append(sig)
        killpg(pid, sig)

    previous = signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        process = subprocess.Popen(
            [sys.executable, "-c", "raise SystemExit(7)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        monkeypatch.setattr(os, "killpg", signal_group)
        # Exit 7 is lost to the auto-reap; it must not turn into an accepted status 0.
        with pytest.raises(_capture.CaptureError, match="could not observe synthetic exit"):
            _capture.capture_process(process, timeout=10, max_bytes=100, label="synthetic")
    finally:
        signal.signal(signal.SIGCHLD, previous)
    assert set(sent) <= {0}  # The released identity only ever sees absence probes.


def test_unobservable_exit_is_bounded_capture_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import errno
    from unittest.mock import Mock

    if not hasattr(os, "waitid"):
        pytest.skip("waitid unavailable")
    process = Mock(pid=12345, args=["synthetic"], stdout=io.BytesIO(), stderr=io.BytesIO())
    monkeypatch.setattr(os, "waitid", Mock(side_effect=OSError(errno.EINVAL, "invalid")))
    group_kill = Mock()
    monkeypatch.setattr(os, "killpg", group_kill)
    with pytest.raises(_capture.CaptureError, match="could not observe synthetic process exit"):
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    # The leader was never reaped, so the failure cleanup may still kill its group.
    group_kill.assert_called_once_with(process.pid, signal.SIGKILL)


def test_ignored_sigterm_policy_is_left_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
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
    previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        install = Mock(side_effect=AssertionError("ignored SIGTERM was overridden"))
        monkeypatch.setattr(signal, "signal", install)
        result = _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
        monkeypatch.undo()
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert result.returncode == 0 and result.stdout == b"result"
    install.assert_not_called()


@pytest.mark.skipif(os.name != "posix", reason="POSIX scoped SIGTERM handling")
def test_term_during_handler_restore_still_restores_and_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def previous(signum: int, frame: object) -> None:
        raise AssertionError("unexpected")

    original_handler = signal.signal(signal.SIGTERM, previous)
    real_signal = signal.signal

    def install(signum: int, handler: object) -> object:
        if handler is previous:
            # Simulate TERM landing at the eval-breaker check of the restore call.
            current = signal.getsignal(signal.SIGTERM)
            assert callable(current)
            current(signal.SIGTERM, None)
        return real_signal(signum, handler)  # type: ignore[arg-type]

    try:
        monkeypatch.setattr(signal, "signal", install)
        with pytest.raises(SystemExit) as raised:
            _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
        assert raised.value.code == 128 + signal.SIGTERM
        assert signal.getsignal(signal.SIGTERM) is previous
    finally:
        real_signal(signal.SIGTERM, original_handler)


@pytest.mark.parametrize("interruption", ["term", "error"])
def test_released_identity_is_not_killed_by_failure_cleanup(
    monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    from unittest.mock import Mock

    process = Mock(pid=12345, args=["synthetic"], stdout=io.BytesIO(), stderr=io.BytesIO())
    monkeypatch.setattr(_capture, "_wait_for_exit", Mock(return_value=False))
    failure: BaseException = (
        SystemExit(128 + signal.SIGTERM)
        if interruption == "term"
        else _capture.CaptureError("could not confirm synthetic process-group cleanup")
    )
    monkeypatch.setattr(_capture, "_group_has_other_members", lambda pgid, **_: True)
    monkeypatch.setattr(_capture, "_retire_group", Mock(side_effect=failure))
    group_kill = Mock()
    monkeypatch.setattr(os, "killpg", group_kill)
    with pytest.raises(type(failure)):
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    group_kill.assert_not_called()  # The reaped leader's ID is no longer ours to signal.
    process.kill.assert_not_called()


@pytest.mark.skipif(not hasattr(select, "kqueue"), reason="macOS kqueue observation")
def test_kqueue_unconfirmed_leader_presence_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    import time
    from unittest.mock import Mock

    process = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        monkeypatch.setattr(
            _capture, "_darwin_group_members", Mock(side_effect=OSError("listing failed"))
        )
        assert not _capture._wait_for_exit_kqueue(
            process, deadline=time.monotonic() + 5, timeout=5, label="x"
        )
    finally:
        process.communicate(timeout=5)


def test_released_group_gets_no_kill_after_members_exit_on_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    process = Mock(pid=12345)
    group_kill = Mock()  # The group still answers signal 0 while its last member exits.
    monkeypatch.setattr(os, "killpg", group_kill)
    # Listed before TERM; empty right after delivery, and still empty before KILL.
    membership = Mock(side_effect=[True, False, False])
    monkeypatch.setattr(_capture, "_group_has_other_members", membership)
    assert _capture._retire_group(process, label="synthetic", reserved=False) is not None
    # The released, now empty group never sees KILL.
    group_kill.assert_called_once_with(process.pid, signal.SIGTERM)
    assert membership.call_count == 3
