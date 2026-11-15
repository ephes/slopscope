"""Synthetic snapshot publication, writer exclusion, and durability regressions."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from slopscope import cli


@pytest.mark.skipif(os.name != "posix", reason="POSIX kernel locks")
def test_snapshot_writers_share_a_kernel_lock(tmp_path: Path) -> None:
    import fcntl

    target = tmp_path / "snapshot.json"
    target.write_text("old")
    script = """
import sys
from pathlib import Path
from slopscope import cli
path = Path(sys.argv[1])
destination = cli._SnapshotDestination(path, cli._snapshot_identity(path),
                                       cli._snapshot_identity(path.parent))
print('ADMITTED', flush=True)
sys.stdin.readline()
if sys.argv[2] == 'first':
    original = cli._verify_snapshot_destination
    calls = 0
    def verify(path, destination):
        global calls
        original(path, destination)
        calls += 1
        if calls == 2:
            print('VERIFIED', flush=True)
            sys.stdin.readline()
    cli._verify_snapshot_destination = verify
try:
    cli._write_snapshot(path, sys.argv[2], destination=destination)
    print('COMMITTED', flush=True)
except OSError as exc:
    print(str(exc), flush=True)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(target), label],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for label in ("first", "stale")
    ]
    first, stale = children
    try:
        for child in children:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "ADMITTED"
        assert first.stdin is not None and first.stdout is not None
        first.stdin.write("start\n")
        first.stdin.flush()
        assert first.stdout.readline().strip() == "VERIFIED"
        # Independently observe exclusion while the writer is between verification and replace.
        with (
            target.with_name(".snapshot.json.lock").open("rb") as lock,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert stale.stdin is not None
        stale.stdin.write("start\n")
        stale.stdin.flush()
        output, error = first.communicate("release\n", timeout=10)
        assert first.returncode == 0 and error == ""
        assert output.strip() == "COMMITTED"
        output, error = stale.communicate(timeout=10)
        assert stale.returncode == 0 and error == ""
        assert "changed after preflight" in output
        assert target.read_text() == "first"
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


@pytest.mark.parametrize("failure", ["none", "file-fsync", "replace", "directory-fsync"])
def test_snapshot_durability_order_and_error_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("old")
    real_sync, real_replace = os.fsync, os.replace
    events: list[str] = []

    def fsync(fd: int) -> None:
        event = "directory-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file-fsync"
        events.append(event)
        if failure == event:
            raise OSError("injected " + event)
        real_sync(fd)

    def replace(source: Path, destination: Path) -> None:
        events.append("replace")
        if failure == "replace":
            raise OSError("injected replace")
        real_replace(source, destination)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        [str(repository), "--composition", "--snapshot", str(snapshot)], stdout=out, stderr=err
    )
    order = ["file-fsync", "replace", "directory-fsync"]
    assert events == (order if failure == "none" else order[: order.index(failure) + 1])
    assert code == (0 if failure == "none" else 2)
    if failure in ("none", "directory-fsync"):
        assert '"report_type": "composition"' in snapshot.read_text()
    else:
        assert snapshot.read_text() == "old"
    if failure == "directory-fsync":
        assert out.getvalue() == ""
        assert "published" in err.getvalue() and "durability unconfirmed" in err.getvalue()
    assert set(tmp_path.glob(".snapshot.json.*")) <= {tmp_path / ".snapshot.json.lock"}


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_snapshot_refuses_unsafe_lock(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "snapshot.json"
    target.write_text("old")
    lock = tmp_path / ".snapshot.json.lock"
    other = tmp_path / "other"
    other.write_text("protected")
    if kind == "symlink":
        lock.symlink_to(other)
    elif kind == "hardlink":
        lock.hardlink_to(other)
    else:
        os.mkfifo(lock)
    destination = cli._SnapshotDestination(
        target, cli._snapshot_identity(target), cli._snapshot_identity(target.parent)
    )
    with pytest.raises(OSError):
        cli._write_snapshot(target, "new", destination=destination)
    assert target.read_text() == "old"
    assert other.read_text() == "protected"
