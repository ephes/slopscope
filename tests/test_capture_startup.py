"""Partially started capture readers must unwind all owned resources."""

from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import Mock

import pytest

from slopscope import _capture, cli, cloc


class TrackedPipe(io.BytesIO):
    def __init__(self, terminated: threading.Event) -> None:
        super().__init__()
        self.terminated = terminated
        self.close_calls = 0
        self.closed_by: threading.Thread | None = None
        self.reading = threading.Event()

    def read(self, size: int | None = -1) -> bytes:
        self.reading.set()
        assert self.terminated.wait(2), "reader still blocked after startup rollback"
        return b"partial output must be discarded"

    def close(self) -> None:
        self.close_calls += 1
        self.closed_by = threading.current_thread()
        super().close()


def fail_start(
    monkeypatch: pytest.MonkeyPatch, fail_at: int
) -> tuple[Mock, tuple[TrackedPipe, TrackedPipe], list[threading.Thread]]:
    terminated = threading.Event()
    pipes = (TrackedPipe(terminated), TrackedPipe(terminated))
    process = Mock(pid=12345, args=["synthetic"], stdout=pipes[0], stderr=pipes[1])
    process.kill.side_effect = terminated.set
    monkeypatch.setattr(os, "killpg", Mock(side_effect=lambda *args: terminated.set()))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=lambda *a, **k: terminated.set()))
    real_start = threading.Thread.start
    attempts = 0
    started = []

    def start(thread: threading.Thread) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == fail_at:
            raise RuntimeError("synthetic private startup detail")
        real_start(thread)
        assert pipes[attempts - 1].reading.wait(1), "reader did not claim its pipe"
        started.append(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    return process, pipes, started


# Patching threading.Thread globally would also break taskkill capture on Windows.
@pytest.mark.skipif(os.name != "posix", reason="POSIX group rollback")
@pytest.mark.parametrize("fail_at", [1, 2])
def test_reader_construction_failure_closes_pipes_and_reaps_child(
    monkeypatch: pytest.MonkeyPatch, fail_at: int
) -> None:
    reader = Mock()
    failure = RuntimeError("synthetic private construction detail")
    construct = Mock(side_effect=[reader] * (fail_at - 1) + [failure])
    monkeypatch.setattr(threading, "Thread", construct)
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    ) as process:
        try:
            with pytest.raises(
                _capture.CaptureError, match="^could not start synthetic output readers$"
            ) as raised:
                _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
            assert raised.value.__cause__ is failure
            assert process.returncode is not None and process.returncode != 0
            assert process.stdout is not None and process.stdout.closed
            assert process.stderr is not None and process.stderr.closed
            reader.start.assert_not_called()
        finally:
            if process.returncode is None:
                process.kill()


@pytest.mark.parametrize("fail_at", [1, 2])
def test_partial_reader_start_closes_every_pipe_once_and_reaps(
    monkeypatch: pytest.MonkeyPatch, fail_at: int
) -> None:
    process, pipes, started = fail_start(monkeypatch, fail_at)
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(_capture.CaptureError, match="^could not start synthetic output readers$"):
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    assert len(started) == fail_at - 1
    assert all(not thread.is_alive() for thread in started)
    for index, pipe in enumerate(pipes):
        assert pipe.closed and pipe.close_calls == 1
        assert pipe.closed_by is (
            started[index] if index < len(started) else threading.current_thread()
        )
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    process.communicate.assert_not_called()
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.parametrize("fail_at", [1, 2])
@pytest.mark.parametrize("engine", ["cloc", "auto", "python"])
def test_reader_start_failure_is_a_controlled_cli_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_at: int, engine: str
) -> None:
    process, pipes, started = fail_start(monkeypatch, fail_at)
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(["--engine", engine, str(tmp_path)], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    diagnostic = err.getvalue()
    if engine == "python":
        assert "could not start Git file discovery output readers" in diagnostic
    else:
        assert diagnostic == "slopscope: could not start cloc output readers\n"
    assert "synthetic private startup detail" not in diagnostic
    assert all(pipe.closed and pipe.close_calls == 1 for pipe in pipes)
    assert all(not thread.is_alive() for thread in started)
    process.wait.assert_called_once_with(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="POSIX supervisor SIGTERM handling")
def test_term_during_partial_start_rollback_keeps_supervisor_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process, pipes, started = fail_start(monkeypatch, 2)
    previous = signal.getsignal(signal.SIGTERM)
    original_kill = process.kill.side_effect

    def terminate() -> None:
        original_kill()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)

    process.kill.side_effect = terminate
    with pytest.raises(SystemExit) as raised:
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    assert raised.value.code == 143
    assert all(pipe.closed and pipe.close_calls == 1 for pipe in pipes)
    assert all(not thread.is_alive() for thread in started)
    process.wait.assert_called_once_with(timeout=5)
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(42)])
def test_interrupted_start_before_bootstrap_cannot_reclaim_closed_pipe(
    monkeypatch: pytest.MonkeyPatch, interruption: BaseException
) -> None:
    process, pipes, _ = fail_start(monkeypatch, 1)
    readers = []

    def deferred_reader(**kwargs: object) -> Mock:
        thread = Mock(ident=None)
        thread.start.side_effect = interruption
        readers.append(kwargs)
        return thread

    monkeypatch.setattr(threading, "Thread", deferred_reader)
    with pytest.raises(type(interruption)) as raised:
        _capture.capture_process(process, timeout=1, max_bytes=100, label="synthetic")
    assert raised.value is interruption
    assert all(pipe.closed and pipe.close_calls == 1 for pipe in pipes)
    # Model a native thread whose Python bootstrap only runs after rollback returned.
    reader = readers[0]
    target = reader["target"]
    args = reader["args"]
    assert callable(target) and isinstance(args, tuple)
    target(*args)
    assert all(pipe.close_calls == 1 for pipe in pipes)
    process.wait.assert_called_once_with(timeout=5)
