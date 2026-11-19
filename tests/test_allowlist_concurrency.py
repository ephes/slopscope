"""Finite cross-process regressions using only synthetic units and temporary files."""

from __future__ import annotations

import contextlib
import io
import multiprocessing
from collections.abc import Iterator
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path

import pytest

from slopscope import cli, size_limits
from slopscope.report import SizeLimitSettings


def _mutate(root: Path, size: int, action: str, use_cli: bool | str, pipe: Connection) -> None:
    original_load = size_limits.load_allowlist
    paused = False

    def paused_load(
        path: Path, *, destination: size_limits.AllowlistDestination | None = None
    ) -> size_limits.Units | None:
        nonlocal paused
        loaded = original_load(path, destination=destination)
        if not paused:
            paused = True
            pipe.send("loaded")
            assert pipe.poll(10), "parent did not release reader"
            assert pipe.recv() == "continue"
        return loaded

    def measure(*args: object, **kwargs: object) -> tuple[size_limits.Units, tuple[()]]:
        return {"functions": {"src/app.py::big": size}, "classes": {}, "test_files": {}}, ()

    size_limits.load_allowlist = paused_load
    size_limits.measure = measure
    pipe.send("started")
    try:
        if use_cli:
            args = ["--size-limits", f"--{action}-allowlist", str(root)]
            if use_cli == "projects":
                names = ["a", "b"] if size == 80 else ["b", "a"]
                for name in names:
                    args.extend(["--project", name])
            code = cli.run(
                args,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
        else:
            size_limits.check(
                root,
                allowlist_path=root / "size-limits.json",
                action=action,
                settings=SizeLimitSettings(max_function_lines=3),
                excluded_paths=(),
            )
            code = 0
    except size_limits.SizeLimitsError:
        code = 2
    pipe.send(code)
    pipe.close()


@contextlib.contextmanager
def _worker(
    root: Path, size: int, action: str, use_cli: bool | str, *, terminated: bool = False
) -> Iterator[tuple[Connection, BaseProcess]]:
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_mutate, args=(root, size, action, use_cli, child))
    process.start()
    child.close()
    try:
        assert parent.poll(10)
        assert parent.recv() == "started"
        yield parent, process
        process.join(10)
        if terminated:
            assert process.exitcode is not None and process.exitcode != 0
        else:
            assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
        parent.close()


@pytest.mark.parametrize("use_cli", [False, True, "projects"])
@pytest.mark.parametrize("action", [size_limits.UPDATE, size_limits.SEED])
def test_concurrent_mutations_revalidate_under_lock(
    tmp_path: Path, use_cli: bool | str, action: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopscope.size_limits]\nmax_function_lines = 3\n", encoding="utf-8"
    )
    targets = [tmp_path / "size-limits.json"]
    if use_cli == "projects":
        with (tmp_path / "pyproject.toml").open("a", encoding="utf-8") as config:
            config.write(
                '[[tool.slopscope.projects]]\nname = "a"\npath = "a"\n'
                '[[tool.slopscope.projects]]\nname = "b"\npath = "b"\n'
            )
        targets = [tmp_path / name / "size-limits.json" for name in ("a", "b")]
        for target in targets:
            target.parent.mkdir()
    if action == size_limits.UPDATE:
        for target in targets:
            size_limits.write_allowlist(target, {"functions": {"src/app.py::big": 100}})
    with _worker(tmp_path, 80, action, use_cli) as (first, _):
        assert first.poll(10)
        assert first.recv() == "loaded"
        with _worker(tmp_path, 90, action, use_cli) as (second, _):
            # If serialization is removed, hold the second stale read until AFTER the first write.
            stale_read = second.poll(0.5)
            if stale_read:
                assert second.recv() == "loaded"
            first.send("continue")
            assert first.poll(10)
            assert first.recv() == 0
            if not stale_read:
                assert second.poll(10)
                assert second.recv() == "loaded"
            second.send("continue")
            assert second.poll(10)
            assert second.recv() == (2 if action == size_limits.SEED else 0)
    for target in targets:
        assert size_limits.load_allowlist(target) == {
            "functions": {"src/app.py::big": 80},
            "classes": {},
            "test_files": {},
        }


def test_interrupted_owner_releases_lock(tmp_path: Path) -> None:
    with _worker(tmp_path, 80, size_limits.SEED, False, terminated=True) as (first, process):
        assert first.poll(10)
        assert first.recv() == "loaded"
        # terminate() intentionally ends the owner without running its context-manager cleanup.
        process.terminate()
        process.join(10)
        with _worker(tmp_path, 90, size_limits.SEED, False) as (second, _):
            assert second.poll(10)
            assert second.recv() == "loaded"
            second.send("continue")
            assert second.poll(10)
            assert second.recv() == 0


def _hold_lock(target: Path, pipe: Connection) -> None:
    with size_limits.allowlist_locks([target], action=size_limits.UPDATE):
        pipe.send("locked")
        pipe.poll(3)  # Let the regression fail finitely even with a blocking writer.
    pipe.close()


def test_contended_second_lock_releases_first_before_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    first, second = (tmp_path / name for name in ("a.json", "b.json"))
    monkeypatch.setattr(size_limits, "ALLOWLIST_LOCK_TIMEOUT_SECONDS", 0.1, raising=False)
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe()
    holder = ctx.Process(target=_hold_lock, args=(second, child))
    holder.start()
    child.close()
    try:
        assert parent.poll(10)
        assert parent.recv() == "locked"
        with (
            pytest.raises(size_limits.SizeLimitsError, match=r"timed out.*b\.json"),
            size_limits.allowlist_locks([second, first], action=size_limits.UPDATE),
        ):
            pytest.fail("the writer reached planning")
        assert holder.is_alive()
        with (tmp_path / ".a.json.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not first.exists()
        assert not second.exists()
    finally:
        parent.close()
        holder.join(5)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)


def test_uncontended_lock_is_taken_after_shared_deadline_expired(tmp_path: Path) -> None:
    import time

    target = (tmp_path / "a.json").resolve()
    with size_limits._allowlist_lock(target, deadline=time.monotonic() - 1):
        pass


def test_lock_targets_are_sorted_and_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    acquired: list[tuple[Path, float]] = []

    @contextlib.contextmanager
    def record(target: Path, *, deadline: float) -> Iterator[None]:
        acquired.append((target, deadline))
        yield

    monkeypatch.setattr(size_limits, "_allowlist_lock", record)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    with size_limits.allowlist_locks([b, a, b], action=size_limits.UPDATE):
        assert [target for target, _ in acquired] == [a.resolve(), b.resolve()]
        assert len({deadline for _, deadline in acquired}) == 1


def test_report_does_not_create_lock_files(tmp_path: Path) -> None:
    size_limits.check(
        tmp_path,
        allowlist_path=tmp_path / "missing" / "allowlist.json",
        settings=SizeLimitSettings(),
        excluded_paths=(),
    )
    assert list(tmp_path.iterdir()) == []


def test_lock_failure_is_a_cli_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    def fail(*args: object, **kwargs: object) -> int:
        raise PermissionError("injected lock failure")

    monkeypatch.setattr(os, "open", fail)
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.run(["--size-limits", "--seed-allowlist", str(tmp_path)], stdout=out, stderr=err) == 2
    )
    assert "could not lock allowlist" in err.getvalue()
    assert out.getvalue() == ""
    assert not (tmp_path / "size-limits.json").exists()


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_lock_refuses_linked_entries(tmp_path: Path, link: str) -> None:
    import os

    target = tmp_path / "size-limits.json"
    external = tmp_path / "external.lock"
    external.write_bytes(b"sentinel")
    lock = tmp_path / ".size-limits.json.lock"
    if link == "symlink":
        lock.symlink_to(external)
    else:
        os.link(external, lock)
    with (
        pytest.raises(size_limits.SizeLimitsError),
        size_limits.allowlist_locks([target], action=size_limits.SEED),
    ):
        pytest.fail("linked lock admitted")
    assert external.read_bytes() == b"sentinel"
    assert not target.exists()


def test_lock_replacement_while_waiting_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    target = tmp_path / "size-limits.json"
    lock = tmp_path / ".size-limits.json.lock"
    moved = tmp_path / "old.lock"

    def replaced_waiter(descriptor: int, operation: int) -> None:
        lock.rename(moved)
        lock.symlink_to(moved)

    monkeypatch.setattr(fcntl, "flock", replaced_waiter)
    with (
        pytest.raises(size_limits.SizeLimitsError),
        size_limits.allowlist_locks([target], action=size_limits.SEED),
    ):
        pytest.fail("replaced lock admitted")
