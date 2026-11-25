"""Synthetic opened-read and durable publication regressions."""

from __future__ import annotations

import contextlib
import errno
import os
import threading
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import MagicMock

import pytest

from slopscope import size_limits
from slopscope.report import SizeLimitSettings


@pytest.mark.skipif(os.name != "posix", reason="requires opening a directory descriptor")
def test_allowlist_directory_rejection_closes_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = os.open
    descriptors: list[int] = []

    def track_open(path: Path, flags: int) -> int:
        descriptor = real_open(path, flags)
        descriptors.append(descriptor)
        return descriptor

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", track_open)
        try:
            errors = []
            for _ in range(3):
                with pytest.raises(size_limits.SizeLimitsError) as error:
                    size_limits.load_allowlist(tmp_path)
                errors.append(str(error.value))
            assert len(descriptors) == 3
            for descriptor in descriptors:
                with pytest.raises(OSError) as closed:
                    os.fstat(descriptor)
                assert closed.value.errno == errno.EBADF
            assert errors == [f"allowlist {tmp_path} must be a regular file"] * 3
        finally:
            for descriptor in descriptors:
                with contextlib.suppress(OSError):
                    os.close(descriptor)


@pytest.mark.parametrize("operation", ["load", "update", "backup"])
@pytest.mark.parametrize("change", ["size", "mtime", "ctime"])
def test_inplace_change_during_read_refuses_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, change: str
) -> None:
    path = tmp_path / "allowlist.json"
    before = b'{"functions": {"old": 200}}'
    after = before.replace(b"200", b"100") + (b" " if change == "size" else b"")
    path.write_bytes(before)
    initial = path.stat()
    reading, written = threading.Event(), threading.Event()

    def writer() -> None:
        assert reading.wait(5)
        with path.open("r+b") as handle:
            handle.write(after)
        # Force a deterministic mtime delta; restoring mtime must still detect ctime changes.
        mtime = initial.st_mtime_ns if change == "ctime" else initial.st_mtime_ns + 1_000_000
        os.utime(path, ns=(initial.st_atime_ns, mtime))
        written.set()

    real_fdopen = os.fdopen

    def chunked_fdopen(*args: object, **kwargs: object) -> object:
        handle = real_fdopen(*args, **kwargs)  # type: ignore[call-overload]
        wrapped = MagicMock(wraps=handle)
        wrapped.__enter__.return_value = wrapped
        wrapped.__exit__.side_effect = handle.__exit__

        def read(limit: int) -> bytes:
            first = handle.read(12)
            reading.set()
            assert written.wait(5)
            return first + handle.read(limit - len(first))  # type: ignore[no-any-return]

        wrapped.read.side_effect = read
        return wrapped

    monkeypatch.setattr(os, "fdopen", chunked_fdopen)
    worker = threading.Thread(target=writer)
    worker.start()
    try:
        with pytest.raises(size_limits.SizeLimitsError, match="changed during read"):
            if operation == "load":
                size_limits.load_allowlist(path)
            elif operation == "update":
                size_limits.check(
                    tmp_path,
                    allowlist_path=path,
                    settings=SizeLimitSettings(),
                    action=size_limits.UPDATE,
                    excluded_paths=(),
                )
            else:
                size_limits.write_allowlist(path, {"functions": {"new": 180}})
    finally:
        worker.join(5)
    assert not worker.is_alive()
    assert path.stat().st_ino == initial.st_ino
    assert path.read_bytes() == after
    assert not list(tmp_path.glob("*.backup"))
    assert not list(tmp_path.glob("*.pending"))


@pytest.mark.parametrize("operation", ["load", "update", "backup"])
def test_allowlist_refuses_short_read_with_stable_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    path = tmp_path / "allowlist.json"
    content = b"{}\n  "
    path.write_bytes(content)
    real_fdopen = os.fdopen

    def fdopen(fd: int, mode: str, *, closefd: bool = True) -> BinaryIO:
        handle = cast(BinaryIO, real_fdopen(fd, mode, closefd=closefd))
        read = handle.read

        def short_read(limit: int) -> bytes:
            assert limit == size_limits.MAX_ALLOWLIST_BYTES + 1
            return read(2)

        monkeypatch.setattr(handle, "read", short_read)
        return handle

    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(size_limits.SizeLimitsError, match="changed during read"):
        if operation == "load":
            size_limits.load_allowlist(path)
        elif operation == "update":
            size_limits.check(
                tmp_path,
                allowlist_path=path,
                settings=SizeLimitSettings(),
                action=size_limits.UPDATE,
                excluded_paths=(),
            )
        else:
            size_limits.write_allowlist(path, {"functions": {"new": 180}})
    assert path.read_bytes() == content
    assert not list(tmp_path.glob("*.backup"))
    assert not list(tmp_path.glob("*.pending"))


def test_publication_syncs_names_before_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    path = tmp_path / "allowlist.json"
    path.write_text("{}")
    events: list[str] = []
    real_sync, real_replace, real_unlink = os.fsync, os.replace, Path.unlink

    def sync(fd: int) -> None:
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_sync(fd)

    def replace(src: Path, dst: Path) -> None:
        events.append("replace")
        real_replace(src, dst)

    def unlink(target: Path, missing_ok: bool = False) -> None:
        if target.suffix == ".backup":
            events.append("retire")
        real_unlink(target, missing_ok=missing_ok)

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    size_limits.write_allowlist(path, {"functions": {"new": 180}})
    assert events[:7] == [
        "file",
        "file",
        "directory",
        "replace",
        "directory",
        "retire",
        "directory",
    ]
    assert not list(tmp_path.glob("*.backup"))


@pytest.mark.parametrize("phase", ["prepare", "publish", "retire"])
def test_directory_sync_failure_preserves_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    import stat

    path = tmp_path / "allowlist.json"
    original = b'{"functions": {"old": 200}}'
    path.write_bytes(original)
    real_sync = os.fsync
    directories = 0
    fail_at = {"prepare": 1, "publish": 2, "retire": 3}[phase]

    def sync(fd: int) -> None:
        nonlocal directories
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directories += 1
            if directories >= fail_at:
                raise OSError("injected directory sync failure")
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(size_limits.SizeLimitsError) as raised:
        size_limits.write_allowlist(path, {"functions": {"new": 180}})
    if phase == "prepare":
        assert path.read_bytes() == original
        assert not list(tmp_path.glob("*.backup"))
    else:
        assert b'"new"' in path.read_bytes()
        assert "durability unconfirmed" in str(raised.value)
        backups = list(tmp_path.glob("*.backup"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == original
        assert str(backups[0]) in str(raised.value)
    assert not list(tmp_path.glob("*.pending"))


def test_compensation_directory_sync_failure_retains_old_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat

    paths = [tmp_path / name for name in ("a.json", "b.json")]
    for path in paths:
        path.write_text("{}")
    real_replace, real_sync = os.replace, os.fsync
    recovering = False

    def replace(src: Path, dst: Path) -> None:
        nonlocal recovering
        if dst == paths[1]:
            recovering = True
            raise OSError("second replacement failed")
        real_replace(src, dst)

    def sync(fd: int) -> None:
        if recovering and stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("recovery sync failed")
        real_sync(fd)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(os, "fsync", sync)
    with (
        size_limits.allowlist_locks(paths, action=size_limits.UPDATE) as destinations,
        pytest.raises(size_limits.SizeLimitsError, match="compensation failed") as raised,
    ):
        size_limits.publish_allowlists(
            [(path, {"functions": {"new": 180}}) for path in paths], destinations=destinations
        )
    assert [path.read_text() for path in paths] == ["{}", "{}"]
    backups = list(tmp_path.glob("*.backup"))
    assert len(backups) == 1
    assert backups[0].read_text() == "{}"
    assert str(backups[0]) in str(raised.value)


def test_seed_durably_creates_parent_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import stat

    path = tmp_path / "new" / "nested" / "allowlist.json"
    synced: list[int] = []
    real_sync = os.fsync

    def sync(fd: int) -> None:
        info = os.fstat(fd)
        if stat.S_ISDIR(info.st_mode):
            synced.append(info.st_ino)
        real_sync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    size_limits.write_allowlist(path, {})
    assert tmp_path.stat().st_ino in synced
    assert path.parent.parent.stat().st_ino in synced
    assert path.parent.stat().st_ino in synced


def test_missing_directory_sync_api_is_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "allowlist.json"
    path.write_bytes(b"{}")
    monkeypatch.delattr(os, "O_DIRECTORY")
    with pytest.raises(size_limits.SizeLimitsError, match="directory synchronization"):
        size_limits.write_allowlist(path, {})
    assert path.read_bytes() == b"{}"
    assert list(tmp_path.iterdir()) == [path]
