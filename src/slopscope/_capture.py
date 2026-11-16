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
    retired = False
    cleaning = False
    termination_requested: int | None = None

    def terminate(signum: int, frame: FrameType | None) -> None:
        nonlocal cleaning, termination_requested
        termination_requested = signum
        if not cleaning:
            cleaning = True
            raise SystemExit(128 + signum)

    # Python permits handler installation only in the main thread. CLI captures
    # own this scoped handler; library calls in other threads leave it untouched.
    handle_sigterm = os.name == "posix" and threading.current_thread() is threading.main_thread()
    # Read the prior handler first and install inside ``try``: a TERM arriving right after
    # installation then still reaches owned-tree cleanup and the handler restore below.
    # A deliberately ignored SIGTERM (or a non-Python handler) stays as it is.
    previous_handler = signal.getsignal(signal.SIGTERM) if handle_sigterm else None
    if previous_handler is signal.SIG_IGN:
        previous_handler = None
    try:
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, terminate)
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
        reserved = _wait_for_exit(process, deadline=deadline, timeout=timeout, label=label)
        if reserved or _group_has_other_members(process.pid):
            # A leader reaped elsewhere no longer reserves the ID; surviving members
            # (or an unlistable group) still do, so their retirement goes ahead.
            cleanup_deadline = _retire_group(process, label=label)
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
        if previous_handler is not None:
            signal.signal(signal.SIGTERM, previous_handler)
        if termination_requested is not None:
            # A repeated TERM during cleanup records intent without interrupting
            # escalation, draining, or reap, including cleanup of another failure.
            raise SystemExit(128 + termination_requested)
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
    # zombies, so an absent leader was reaped. An unlistable group keeps the usual path.
    try:
        return process.pid in _darwin_group_members(process.pid)
    except Exception:
        return True


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


def _retire_group(process: subprocess.Popen[bytes], *, label: str) -> float | None:
    """Signal redirected descendants while the unreaped leader reserves the group."""

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
                if sig == signal.SIGTERM and not _group_has_other_members(process.pid):
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


def _group_has_other_members(pgid: int) -> bool:
    """Whether a process besides the leader may still belong to group ``pgid``.

    Listing failures and unsupported platforms answer True, keeping the full TERM grace.
    """

    try:
        if sys.platform == "darwin":
            members = _darwin_group_members(pgid)
        elif sys.platform.startswith("linux"):
            members = _linux_group_members(pgid)
        else:
            return True
    except Exception:
        return True
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


def _darwin_group_members(pgid: int) -> list[int]:
    proc_listpids = _darwin_proc_listpids()
    capacity = 64
    while True:
        buffer = (ctypes.c_int * capacity)()
        filled = proc_listpids(_PROC_PGRP_ONLY, pgid, buffer, ctypes.sizeof(buffer))
        if filled < 0:
            raise OSError(ctypes.get_errno(), "proc_listpids failed")
        count = filled // ctypes.sizeof(ctypes.c_int)
        if count < capacity:
            return [pid for pid in buffer[:count] if pid > 0]
        capacity *= 2  # A full buffer may be truncated; list again with more room.


def _linux_group_members(pgid: int, proc_root: str = "/proc") -> list[int]:
    members = []
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        try:
            with open(os.path.join(proc_root, entry, "stat"), "rb") as stat:
                data = stat.read()
        except (FileNotFoundError, ProcessLookupError):
            continue  # Exited (and reaped) since the directory listing.
        # Fields after the parenthesized command name: state, ppid, pgrp, ...
        if int(data[data.rindex(b")") + 2 :].split()[2]) == pgid:
            members.append(int(entry))
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
