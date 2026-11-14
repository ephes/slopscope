"""Bounded binary subprocess capture with owned process-tree cleanup."""

from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import threading
import time
from typing import BinaryIO

_CAPTURE_CHUNK_BYTES = 64 * 1024
_GROUP_TERM_SECONDS = 0.2
_GROUP_KILL_SECONDS = 0.8


class CaptureError(RuntimeError):
    """A command could not be captured completely within its byte budget."""


def capture_process(
    process: subprocess.Popen[bytes], *, timeout: float, max_bytes: int, label: str
) -> subprocess.CompletedProcess[bytes]:
    """Capture both pipes within one budget and retire the owned tree on every outcome.

    Callers must start a new session on POSIX. Descendants that leave that session/group
    are outside this ownership boundary; Windows tree termination remains best-effort.
    """
    # Each reader retains only its share of one combined byte budget. Separate readers
    # prevent a full stderr pipe from blocking stdout (and work on Windows too).
    buffers = (bytearray(), bytearray())
    failures: list[CaptureError] = []
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
                    if len(buffers[0]) + len(buffers[1]) + len(chunk) > max_bytes:
                        failures.append(CaptureError(f"{label} output exceeded {max_bytes} bytes"))
                        stopped.set()
                        changed.set()
                        break
                    target.extend(chunk)
        except BaseException:
            # Worker failures must never turn a truncated capture into success.
            with lock:
                failures.append(CaptureError(f"could not read {label} output"))
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
    deadline = time.monotonic() + timeout
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
                raise subprocess.TimeoutExpired(process.args, timeout)
            changed.wait(min(remaining, 0.05))
            changed.clear()
        if failures:
            raise failures[0]
        process.wait(timeout=max(0, deadline - time.monotonic()))
        _retire_group(process, label=label)
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


def _retire_group(process: subprocess.Popen[bytes], *, label: str) -> None:
    """Retire redirected descendants too, even after their wrapper has exited."""

    if os.name != "posix":
        # The wrapper may already be gone, so taskkill cannot always find its tree.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        return
    try:
        for sig, grace in (
            (signal.SIGTERM, _GROUP_TERM_SECONDS),
            (signal.SIGKILL, _GROUP_KILL_SECONDS),
        ):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + grace
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return
                except PermissionError:
                    # A terminating/reparented group can be temporarily uninspectable.
                    # Retry only within the same deadline; never infer success from EPERM.
                    pass
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.01))
    except OSError as exc:
        raise CaptureError(f"could not confirm {label} process-group cleanup") from exc
    raise CaptureError(f"could not confirm {label} process-group cleanup")
