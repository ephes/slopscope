"""Synthetic source swaps and byte admission through the production CLI."""

from __future__ import annotations

import io
import json
import os
import stat
import tokenize
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import pytest

from slopscope import _source, cli, composition, config


def run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(args, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("mode", ["composition", "seed", "update"])
@pytest.mark.parametrize("swap", ["leaf", "parent", "regular"])
def test_discovered_source_replacement_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, swap: str
) -> None:
    root = tmp_path / "repo"
    parent = root / "pkg"
    parent.mkdir(parents=True)
    source = parent / "app.py"
    source.write_text("local = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.py").write_text("outside_sentinel = 123\n", encoding="utf-8")
    snapshot, allowlist = root / "snapshot.json", root / "size-limits.json"
    snapshot.write_bytes(b"previous snapshot")
    if mode == "update":
        allowlist.write_bytes(b"{}\n")
    original = composition.analyze_file
    swaps: list[str] = []
    analyzed: list[str] = []
    analyze_source = composition.analyze_source

    def record(text: str, **kwargs: Any) -> Any:
        analyzed.append(text)
        return analyze_source(text, **kwargs)

    def replace(root: Path, relative_path: str, **kwargs: Any) -> Any:
        swaps.append(relative_path)
        if swap == "parent":
            parent.rename(root / "previous_pkg")
            parent.symlink_to(outside, target_is_directory=True)
        else:
            source.rename(root / "previous.py")
            if swap == "leaf":
                source.symlink_to(outside / "app.py")
            else:
                source.write_text("replacement = 2\n", encoding="utf-8")
        return original(root, relative_path, **kwargs)

    monkeypatch.setattr(composition, "analyze_file", replace)
    monkeypatch.setattr(composition, "analyze_source", record)
    options = {
        "composition": ["--composition", "--snapshot", str(snapshot)],
        "seed": ["--size-limits", "--seed-allowlist"],
        "update": ["--size-limits", "--update-allowlist"],
    }[mode]
    code, out, err = run([str(root), "--format", "json", *options])
    assert swaps == ["pkg/app.py"]
    assert code != 0
    assert analyzed == []
    assert "Traceback" not in err
    assert len(err) < 500
    assert "app.py" in err or "file(s)" in err
    if mode == "composition":
        assert json.loads(out)["failures"]
    assert snapshot.read_bytes() == b"previous snapshot"
    assert (allowlist.read_bytes() if allowlist.exists() else None) == (
        b"{}\n" if mode == "update" else None
    )


def test_nonregular_opened_descriptor_is_refused_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    fstat, os_open = os.fstat, os.open
    opened: list[int] = []

    def open_file(path: Any, flags: int, **kwargs: Any) -> int:
        fd = os_open(path, flags, **kwargs)
        if path == "app.py":
            assert flags & os.O_NONBLOCK
            opened.append(fd)
        return fd

    def fifo_descriptor(fd: int) -> os.stat_result:
        result = fstat(fd)
        if fd in opened:
            # Model an opened FIFO without platform-specific named-pipe fixtures or hangs.
            fields = list(result)
            fields[0] = stat.S_IFIFO | 0o600
            return os.stat_result(fields)
        return result

    def unexpected_parse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("nonregular source reached parsing")

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "fstat", fifo_descriptor)
    monkeypatch.setattr(composition, "analyze_source", unexpected_parse)
    code, out, err = run([str(tmp_path), "--composition", "--format", "json"])
    assert opened
    assert code == 1
    assert json.loads(out)["failures"]
    assert "source changed" in err


@pytest.mark.parametrize("mode", ["composition", "seed", "update"])
def test_source_byte_limit_preserves_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    (tmp_path / "app.py").write_bytes(b"x = 12345\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopscope.composition]\nmax_source_bytes = 8\n", encoding="utf-8"
    )
    snapshot, allowlist = tmp_path / "snapshot.json", tmp_path / "size-limits.json"
    snapshot.write_bytes(b"previous snapshot")
    if mode == "update":
        allowlist.write_bytes(b"{}\n")

    def unexpected_parse(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("oversized source reached decoding or parsing")

    original_open = _source.open_source
    opened: list[str] = []

    @contextmanager
    def guarded_open(
        root: Path, relative_path: str, admission: _source.Admission | None = None
    ) -> Iterator[BinaryIO]:
        with original_open(root, relative_path, admission) as handle:
            opened.append(relative_path)
            monkeypatch.setattr(handle, "read", unexpected_parse)
            yield handle

    monkeypatch.setattr(_source, "open_source", guarded_open)
    monkeypatch.setattr(tokenize, "detect_encoding", unexpected_parse)
    options = {
        "composition": ["--composition", "--snapshot", str(snapshot)],
        "seed": ["--size-limits", "--seed-allowlist"],
        "update": ["--size-limits", "--update-allowlist"],
    }[mode]
    code, _out, err = run([str(tmp_path), *options])
    assert code != 0
    assert opened == ["app.py"]
    assert "exceeds 8 bytes" in err or "file(s)" in err
    assert snapshot.read_bytes() == b"previous snapshot"
    assert (allowlist.read_bytes() if allowlist.exists() else None) == (
        b"{}\n" if mode == "update" else None
    )


@pytest.mark.parametrize("encoding", ["utf-8", "iso-8859-1"])
def test_exact_source_byte_limit_with_encoding_and_newlines(tmp_path: Path, encoding: str) -> None:
    raw = f"# coding: {encoding}\r\nname = 'é'\r\n".encode(encoding)
    (tmp_path / "app.py").write_bytes(raw)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.slopscope.composition]\nmax_source_bytes = {len(raw)}\n", encoding="utf-8"
    )
    code, out, err = run([str(tmp_path), "--composition", "--format", "json"])
    assert (code, err) == (0, "")
    data = json.loads(out)
    assert data["total"]["physical"] == 2
    assert data["settings"]["max_source_bytes"] == len(raw)


@pytest.mark.parametrize("value", [0, -1, True, "1024", 1.5])
def test_source_byte_limit_must_be_positive_integer(value: object) -> None:
    with pytest.raises(config.ConfigError, match="max_source_bytes must be a positive integer"):
        config.parse_config_mapping({"composition": {"max_source_bytes": value}})


def test_source_opening_fails_closed_without_required_os_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    code, out, err = run([str(tmp_path), "--composition", "--format", "json"])
    assert code == 1
    assert json.loads(out)["failures"]
    assert "safe source opening is unavailable" in err


def test_growing_source_read_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "app.py"
    source.write_bytes(b"x = 1\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopscope.composition]\nmax_source_bytes = 8\n", encoding="utf-8"
    )
    fstat = os.fstat
    identity = source.stat()
    checks = 0

    def grow_after_size_check(fd: int) -> os.stat_result:
        nonlocal checks
        info = fstat(fd)
        if (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
            checks += 1
            if checks == 2:  # Descriptor admission, then the analyzer's size check.
                with source.open("ab") as handle:
                    handle.write(b"# more bytes\n")
        return info

    original_open = _source.open_source
    reads: list[int] = []

    @contextmanager
    def guarded_open(
        root: Path, relative_path: str, admission: _source.Admission | None = None
    ) -> Iterator[BinaryIO]:
        with original_open(root, relative_path, admission) as handle:
            read = handle.read

            def bounded_read(size: int = -1) -> bytes:
                reads.append(size)
                assert size == 9
                return read(size)

            monkeypatch.setattr(handle, "read", bounded_read)
            yield handle

    monkeypatch.setattr(os, "fstat", grow_after_size_check)
    monkeypatch.setattr(_source, "open_source", guarded_open)
    code, out, err = run([str(tmp_path), "--composition", "--format", "json"])
    assert checks == 2
    assert reads == [9]
    assert code == 1
    assert json.loads(out)["failures"]
    assert "exceeds 8 bytes" in err
