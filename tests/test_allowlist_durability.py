"""Synthetic opened-read and durable publication regressions."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from slopscope import size_limits
from slopscope.report import SizeLimitSettings


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
