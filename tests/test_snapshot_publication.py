"""Synthetic snapshot publication, writer exclusion, and durability regressions."""

from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from slopscope import cli, composition


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
                                       cli._snapshot_identity(path.parent),
                                       cli._snapshot_generation(path))
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
        target,
        cli._snapshot_identity(target),
        cli._snapshot_identity(target.parent),
        cli._snapshot_generation(target),
    )
    with pytest.raises(OSError):
        cli._write_snapshot(target, "new", destination=destination)
    assert target.read_text() == "old"
    assert other.read_text() == "protected"


@pytest.mark.skipif(os.name != "posix", reason="POSIX kernel locks")
def test_snapshot_lock_contention_settles_within_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("old")
    lock = tmp_path / ".snapshot.json.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, sys\n"
            "handle = open(sys.argv[1], 'a+b')\n"
            "fcntl.flock(handle, fcntl.LOCK_EX)\n"
            "print('LOCKED', flush=True)\n"
            "sys.stdin.readline()\n",
            str(lock),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        lock_identity = cli._snapshot_identity(lock)
        monkeypatch.setattr(cli, "_SNAPSHOT_LOCK_TIMEOUT_SECONDS", 0.3)
        argv = [str(repository), "--composition", "--snapshot", str(snapshot)]
        out, err = io.StringIO(), io.StringIO()
        started = time.monotonic()
        code = cli.run(argv, stdout=out, stderr=err)
        elapsed = time.monotonic() - started
        assert code == 2
        assert out.getvalue() == ""
        assert "snapshot lock busy" in err.getvalue()
        assert elapsed < 5
        assert snapshot.read_text() == "old"
        assert set(tmp_path.glob(".snapshot.json.*")) == {lock}
    finally:
        output, error = holder.communicate("release\n", timeout=10)
    assert holder.returncode == 0 and error == ""
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(argv, stdout=out, stderr=err) == 0, err.getvalue()
    assert '"report_type": "composition"' in snapshot.read_text()
    assert cli._snapshot_identity(lock) == lock_identity


@pytest.mark.parametrize("stage", ["analysis", "staging"])
def test_snapshot_refuses_in_place_target_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b"old snapshot")
    # Backdate mtime so the same-length edit is detectable even with coarse timestamps.
    os.utime(snapshot, ns=(0, 0))
    identity = cli._snapshot_identity(snapshot)
    edits = 0

    def edit() -> None:
        nonlocal edits
        edits += 1
        # Same inode, same length: only the generation (mtime/ctime) reveals the edit.
        with snapshot.open("r+b") as handle:
            handle.write(b"NEW snapshot")
        assert cli._snapshot_identity(snapshot) == identity

    original_build = composition.build_composition_report
    original_fsync = os.fsync

    def build(*args: Any, **kwargs: Any) -> Any:
        report = original_build(*args, **kwargs)
        edit()
        return report

    def fsync(fd: int) -> None:
        original_fsync(fd)
        if not stat.S_ISDIR(os.fstat(fd).st_mode) and edits == 0:
            edit()

    if stage == "analysis":
        monkeypatch.setattr(composition, "build_composition_report", build)
    else:
        monkeypatch.setattr(os, "fsync", fsync)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        [str(repository), "--composition", "--snapshot", str(snapshot)], stdout=out, stderr=err
    )
    assert edits == 1
    assert code == 2
    assert out.getvalue() == ""
    assert "snapshot target changed" in err.getvalue()
    assert snapshot.read_bytes() == b"NEW snapshot"
    assert set(tmp_path.glob(".snapshot.json.*")) <= {tmp_path / ".snapshot.json.lock"}


def test_snapshot_refuses_target_created_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n")
    snapshot = tmp_path / "snapshot.json"
    original_build = composition.build_composition_report

    def build(*args: Any, **kwargs: Any) -> Any:
        report = original_build(*args, **kwargs)
        snapshot.write_bytes(b"created concurrently")
        return report

    monkeypatch.setattr(composition, "build_composition_report", build)
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        [str(repository), "--composition", "--snapshot", str(snapshot)], stdout=out, stderr=err
    )
    assert code == 2
    assert "snapshot target changed" in err.getvalue()
    assert snapshot.read_bytes() == b"created concurrently"


@pytest.mark.parametrize("existing", [False, True])
def test_snapshot_publishes_unchanged_target(tmp_path: Path, existing: bool) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "app.py").write_text("x = 1\n")
    snapshot = tmp_path / "snapshot.json"
    if existing:
        snapshot.write_bytes(b"old snapshot")
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(
        [str(repository), "--composition", "--snapshot", str(snapshot)], stdout=out, stderr=err
    )
    assert code == 0, err.getvalue()
    assert '"report_type": "composition"' in snapshot.read_text()
