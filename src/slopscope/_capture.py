"""Bounded binary subprocess capture with owned process-tree cleanup."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import io
import os
import select
import signal
import subprocess
import sys
import threading
import time
from types import FrameType
from typing import Any, BinaryIO

_CAPTURE_CHUNK_BYTES = 64 * 1024
_GROUP_TERM_SECONDS = 0.2
_GROUP_KILL_SECONDS = 0.8
_MAX_GROUP_INSPECTION_ENTRIES = 65_536
_MAX_PROC_STAT_BYTES = 4096


class CaptureError(RuntimeError):
    """A command's capture could not be accepted."""


class SignalRestoreError(CaptureError):
    """Capture finished but restoring the previous SIGTERM handler failed."""


def capture_process(
    process: subprocess.Popen[bytes], *, timeout: float, max_bytes: int, label: str
) -> subprocess.CompletedProcess[bytes]:
    """Capture both pipes within one budget and retire the owned tree on every outcome.

    Callers must start a new session on POSIX. Descendants that leave that session/group
    are outside this ownership boundary; Windows tree termination remains best-effort.
    """
    owned_pipes: list[BinaryIO] = []
    started: list[threading.Thread] = []
    readers_may_run = False
    retired = False
    capture_failed = False
    cleaning = False
    termination_requested: int | None = None
    previous_handler = None

    def read_pipe(pipe: BinaryIO, target: bytearray) -> None:
        with lock:
            # A start interrupted before bootstrap can run after rollback. It must
            # leave the pipe alone once the supervisor has reclaimed ownership.
            if stopped.is_set():
                return
            owned_pipes.append(pipe)
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

    def terminate(signum: int, frame: FrameType | None) -> None:
        nonlocal cleaning, termination_requested
        termination_requested = signum
        if not cleaning:
            cleaning = True
            raise SystemExit(128 + signum)

    try:
        try:
            # Separate readers prevent a full stderr pipe from blocking stdout.
            # Both readers share the same byte budget.
            buffers = (bytearray(), bytearray())
            failures: list[CaptureError] = []
            lock = threading.Lock()
            changed = threading.Event()
            stopped = threading.Event()
            assert process.stdout is not None and process.stderr is not None
            threads = [
                threading.Thread(target=read_pipe, args=(pipe, buffer), daemon=True)
                for pipe, buffer in zip((process.stdout, process.stderr), buffers, strict=True)
            ]
        except Exception as exc:
            raise CaptureError(f"could not start {label} output readers") from exc
        deadline = time.monotonic() + timeout
        # Python permits handler installation only in the main thread. A deliberately
        # ignored SIGTERM (or a non-Python handler) stays as it is.
        if os.name == "posix" and threading.current_thread() is threading.main_thread():
            previous = signal.getsignal(signal.SIGTERM)
            if previous is not signal.SIG_IGN:
                previous_handler = previous
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, terminate)
        readers_may_run = True
        for thread in threads:
            try:
                thread.start()
            except Exception as exc:
                raise CaptureError(f"could not start {label} output readers") from exc
            finally:
                # ident survives thread exit and also identifies a reader whose start
                # was interrupted after launch, so rollback can still join it.
                if thread.ident is not None:
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
        reserved = _wait_for_exit(process, deadline=deadline, timeout=timeout, label=label)
        # A leader reaped elsewhere no longer reserves the ID: from here on the failure
        # path must not signal it. Only positively listed surviving members (which keep
        # the group ID reserved) are still retired.
        retired = not reserved
        if reserved or _group_has_other_members(
            process.pid, deadline=time.monotonic() + _GROUP_TERM_SECONDS, unknown=False
        ):
            cleanup_deadline = _retire_group(process, label=label, reserved=reserved)
        else:
            cleanup_deadline = time.monotonic() + _GROUP_KILL_SECONDS
        retired = True
        process.wait(timeout=max(0, deadline - time.monotonic()))
        if cleanup_deadline is not None:
            _confirm_group_exit(process, deadline=cleanup_deadline, label=label)
        if not reserved:
            # Popen reports a reaped-elsewhere child as 0; the real status is lost.
            raise CaptureError(f"could not observe {label} exit status")
    except BaseException:
        # First statement of the handler block: CPython runs Python signal handlers only at
        # eval-breaker checks, so a repeated TERM after this point is recorded, not raised.
        cleaning = True
        capture_failed = True
        if readers_may_run:
            stopped.set()
        if not retired and os.name == "posix":
            # Kill the group even if the wrapper has exited but a child still owns its pipes.
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
        elif not retired:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
        if not retired:
            with contextlib.suppress(OSError):
                process.kill()
        with lock if readers_may_run else contextlib.nullcontext():
            unowned_pipes = [
                pipe
                for pipe in (process.stdout, process.stderr)
                if pipe is not None and pipe not in owned_pipes
            ]
        for pipe in unowned_pipes:
            # No reader claimed this pipe before rollback. A late reader checks
            # stopped under the same lock and cannot touch the reclaimed pipe.
            with contextlib.suppress(OSError):
                pipe.close()
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
    finally:
        # A TERM arriving while the handler is restored is recorded and re-raised below.
        cleaning = True
        restore_failure = None
        if previous_handler is not None:
            try:
                signal.signal(signal.SIGTERM, previous_handler)
            except Exception as exc:
                restore_failure = exc
        if termination_requested is not None:
            # A repeated TERM during cleanup records intent without interrupting
            # escalation, draining, or reap, including cleanup of another failure.
            raise SystemExit(128 + termination_requested)
        if restore_failure is not None and not capture_failed:
            raise SignalRestoreError(
                f"could not restore SIGTERM handler after {label} capture"
            ) from restore_failure
    return subprocess.CompletedProcess(
        process.args, process.returncode, bytes(buffers[0]), bytes(buffers[1])
    )


def _wait_for_exit(
    process: subprocess.Popen[bytes], *, deadline: float, timeout: float, label: str
) -> bool:
    """Observe exit without releasing the POSIX leader's process/group identity.

    Returns False when the leader was already reaped elsewhere (e.g. inherited
    SIGCHLD=SIG_IGN): its identity and exit status are then no longer ours.
    """

    if os.name != "posix":
        process.wait(timeout=max(0, deadline - time.monotonic()))
        return True
    waitid = getattr(os, "waitid", None)  # Some platform type stubs omit this public API.
    if waitid is None or not all(
        hasattr(os, name) for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT")
    ):
        return _wait_for_exit_kqueue(process, deadline=deadline, timeout=timeout, label=label)
    try:
        while waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(min(remaining, 0.01))
    except ChildProcessError:
        return False
    except OSError as exc:
        raise CaptureError(f"could not observe {label} process exit") from exc
    return True


def _wait_for_exit_kqueue(
    process: subprocess.Popen[bytes], *, deadline: float, timeout: float, label: str
) -> bool:
    """Older macOS Python exposes non-reaping process observation through kqueue."""

    if sys.platform != "darwin" or not hasattr(select, "kqueue"):
        raise CaptureError(f"{label} capture requires waitid/WNOWAIT or kqueue process observation")
    try:
        _observe_exit_kqueue(process, deadline=deadline, timeout=timeout, label=label)
    except OSError as exc:
        raise CaptureError(f"could not observe {label} process exit") from exc
    # kqueue cannot tell an unreaped zombie from a leader reaped elsewhere; libproc lists
    # zombies, so an absent leader was reaped. Unconfirmed presence fails closed: the
    # identity is treated as released, never signalled, and its status is refused.
    # The presence check gets its own bounded budget: an exit observed just inside the
    # capture timeout must not be misread as reaped elsewhere (matching waitid).
    try:
        return process.pid in _darwin_group_members(
            process.pid, deadline=time.monotonic() + _GROUP_TERM_SECONDS
        )
    except Exception:
        return False


def _observe_exit_kqueue(
    process: subprocess.Popen[bytes], *, deadline: float, timeout: float, label: str
) -> None:
    with contextlib.closing(select.kqueue()) as queue:
        event = select.kevent(
            process.pid,
            filter=select.KQ_FILTER_PROC,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
            fflags=select.KQ_NOTE_EXIT,
        )
        events = queue.control([event], 1, max(0, deadline - time.monotonic()))
        if not events:
            raise subprocess.TimeoutExpired(process.args, timeout)
        # Registration against an already exited child (unreaped, or reaped elsewhere)
        # reports ESRCH.
        if events[0].flags & select.KQ_EV_ERROR and events[0].data != errno.ESRCH:
            raise CaptureError(f"could not observe {label} process exit")


def _retire_group(
    process: subprocess.Popen[bytes], *, label: str, reserved: bool = True
) -> float | None:
    """Signal redirected descendants while the unreaped leader reserves the group.

    With a leader reaped elsewhere (``reserved=False``), only positively listed members
    reserve the group ID, so every TERM/KILL send first requires such a listing.
    """

    if os.name != "posix":
        # The wrapper may already be gone, so taskkill cannot always find its tree.
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        return None
    try:
        for sig, grace in (
            (signal.SIGTERM, _GROUP_TERM_SECONDS),
            (signal.SIGKILL, _GROUP_KILL_SECONDS),
        ):
            deadline = time.monotonic() + grace
            pending: int = sig
            while True:
                if (
                    pending
                    and not reserved
                    and not _group_has_other_members(process.pid, deadline=deadline, unknown=False)
                ):
                    return deadline  # Released and empty: nothing may be signalled.
                try:
                    # Send ``sig`` until it is delivered, then probe with signal 0.
                    os.killpg(process.pid, pending)
                except ProcessLookupError:
                    return deadline
                except PermissionError:
                    if sig == signal.SIGKILL:
                        # A zombie-only group can refuse KILL on macOS. Reap after
                        # this final attempt and require confirmed absence below.
                        return deadline
                    # A terminating/reparented group can be temporarily unsignalable or
                    # uninspectable (e.g. only zombies remain on macOS). Retry only
                    # within the same deadline; never infer success from EPERM.
                    pass
                else:
                    # The unreaped leader itself can keep signal-0 probes positive.
                    # Successful final KILL delivery ends our signalling authority;
                    # only then may the caller reap. Later probes send no signal.
                    if sig == signal.SIGKILL:
                        return deadline
                    pending = 0
                if sig == signal.SIGTERM and not _group_has_other_members(
                    process.pid, deadline=deadline
                ):
                    # Only the exited, unreaped leader remains: no member can use the TERM
                    # grace. The KILL attempt below still runs before the caller reaps.
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(remaining, 0.01))
    except OSError as exc:
        raise CaptureError(f"could not confirm {label} process-group cleanup") from exc
    return deadline


_PROC_PGRP_ONLY = 2  # <sys/proc_info.h>: proc_listpids filter by process group.


def _group_has_other_members(
    pgid: int, *, deadline: float | None = None, unknown: bool = True
) -> bool:
    """Whether a process besides the leader still belongs to group ``pgid``.

    Failed, truncated, expired and unsupported inspections answer ``unknown``:
    True keeps the TERM grace; False refuses authority to signal a released ID.
    """

    try:
        if sys.platform == "darwin":
            members = _darwin_group_members(pgid, deadline=deadline)
        elif sys.platform.startswith("linux"):
            members = _linux_group_members(pgid, deadline=deadline)
        else:
            return unknown
    except Exception:
        return unknown
    return any(pid != pgid for pid in members)


@functools.cache
def _darwin_proc_listpids() -> Any:
    import ctypes.util

    library = ctypes.util.find_library("proc")
    if library is None:
        raise OSError("libproc is unavailable")
    proc_listpids = ctypes.CDLL(library, use_errno=True).proc_listpids
    proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
    proc_listpids.restype = ctypes.c_int
    return proc_listpids


def _inspection_time_remaining(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise OSError("process-group inspection deadline exceeded")


def _darwin_group_members(pgid: int, *, deadline: float | None = None) -> list[int]:
    if deadline is None:
        deadline = time.monotonic() + _GROUP_TERM_SECONDS
    _inspection_time_remaining(deadline)
    proc_listpids = _darwin_proc_listpids()
    capacity = min(64, _MAX_GROUP_INSPECTION_ENTRIES)
    while True:
        _inspection_time_remaining(deadline)
        buffer = (ctypes.c_int * capacity)()
        filled = proc_listpids(_PROC_PGRP_ONLY, pgid, buffer, ctypes.sizeof(buffer))
        _inspection_time_remaining(deadline)
        if filled < 0:
            raise OSError(ctypes.get_errno(), "proc_listpids failed")
        count = filled // ctypes.sizeof(ctypes.c_int)
        if count < capacity:
            return [pid for pid in buffer[:count] if pid > 0]
        if capacity >= _MAX_GROUP_INSPECTION_ENTRIES:
            raise OSError("process-group inspection entry limit exceeded")
        # A full buffer may be truncated, including at the ceiling: never return it.
        capacity = min(capacity * 2, _MAX_GROUP_INSPECTION_ENTRIES)


def _linux_group_members(
    pgid: int, proc_root: str = "/proc", *, deadline: float | None = None
) -> list[int]:
    if deadline is None:
        deadline = time.monotonic() + _GROUP_TERM_SECONDS
    _inspection_time_remaining(deadline)
    members = []
    with os.scandir(proc_root) as entries:
        for index, entry in enumerate(entries):
            _inspection_time_remaining(deadline)
            if index >= _MAX_GROUP_INSPECTION_ENTRIES:
                raise OSError("process-group inspection entry limit exceeded")
            if not entry.name.isdigit():
                continue
            try:
                with open(os.path.join(proc_root, entry.name, "stat"), "rb") as stat:
                    data = stat.read(_MAX_PROC_STAT_BYTES + 1)
            except (FileNotFoundError, ProcessLookupError):
                continue  # Exited (and reaped) since the directory listing.
            _inspection_time_remaining(deadline)
            if len(data) > _MAX_PROC_STAT_BYTES:
                raise OSError("process-group inspection stat byte limit exceeded")
            # Fields after the parenthesized command name: state, ppid, pgrp, ...
            if int(data[data.rindex(b")") + 2 :].split()[2]) == pgid:
                members.append(int(entry.name))
    _inspection_time_remaining(deadline)
    return members


def _confirm_group_exit(process: subprocess.Popen[bytes], *, deadline: float, label: str) -> None:
    """Check absence after reap without ever signalling a reusable identity."""

    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        except OSError as exc:
            raise CaptureError(f"could not confirm {label} process-group cleanup") from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError(f"could not confirm {label} process-group cleanup")
        time.sleep(min(remaining, 0.01))
