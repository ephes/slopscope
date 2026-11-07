from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path

import pytest

from slopscope import cli, config


@pytest.mark.parametrize("mode", [stat.S_IFIFO, stat.S_IFCHR, stat.S_IFDIR])
def test_config_checks_open_descriptor_before_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"[tool.slopscope]\n")
    real_open = os.open
    opened: list[int] = []

    def open_file(path: Path, flags: int) -> int:
        assert flags & getattr(os, "O_NONBLOCK", 0) == getattr(os, "O_NONBLOCK", 0)
        assert flags & getattr(os, "O_BINARY", 0) == getattr(os, "O_BINARY", 0)
        descriptor = real_open(path, flags)
        opened.append(descriptor)
        return descriptor

    def special_file(descriptor: int) -> os.stat_result:
        assert descriptor == opened[0]
        return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "fstat", special_file)
    with pytest.raises(config.ConfigError, match="must be a regular file"):
        config.load_config_from_pyproject(path)
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.read(opened[0], 1)


def test_config_actual_read_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.toml"
    path.write_bytes(b"#" + b"x" * 1000)
    monkeypatch.setattr(config, "MAX_CONFIG_BYTES", 64, raising=False)
    real_fstat = os.fstat
    real_read = io.BufferedReader.read
    reads: list[int] = []

    class CheckedReader(io.BufferedReader):
        def read(self, size: int | None = -1) -> bytes:
            assert size == 65
            reads.append(size)
            return real_read(self, size)

    def underreported_size(descriptor: int) -> os.stat_result:
        info = list(real_fstat(descriptor))
        info[6] = 0  # Simulate growth after fstat without timing or platform dependencies.
        return os.stat_result(info)

    def fdopen(descriptor: int, mode: str) -> CheckedReader:
        assert mode == "rb"
        return CheckedReader(io.FileIO(descriptor, mode, closefd=True))

    monkeypatch.setattr(os, "fstat", underreported_size)
    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(config.ConfigError, match="exceeds 64 bytes"):
        config.load_config_from_pyproject(path)
    assert reads == [65]


@pytest.mark.parametrize("at_limit", [False, True])
def test_config_accepts_utf8_at_byte_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, at_limit: bool
) -> None:
    path = tmp_path / "config.toml"
    content = '[tool.slopscope]\nexclude_dirs = ["généré"]\n'.encode()
    monkeypatch.setattr(config, "MAX_CONFIG_BYTES", 64, raising=False)
    path.write_bytes(content + (b" " * (64 - len(content)) if at_limit else b""))
    assert config.load_config_from_pyproject(path).exclude_dirs == ("généré",)


@pytest.mark.parametrize(
    "failure", ["oversize", "growing", "fifo", "device", "array-depth", "table-depth"]
)
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("existing_outputs", [False, True])
@pytest.mark.parametrize("mode", ["report", "snapshot", "seed", "update"])
def test_invalid_config_fails_before_analysis_or_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    explicit: bool,
    existing_outputs: bool,
    mode: str,
) -> None:
    path = tmp_path / "pyproject.toml"
    content = b"[tool.slopscope]\n#" + b"x" * 64
    deep = failure.endswith("-depth")
    recursion_limit = sys.getrecursionlimit()
    if deep:
        depth = recursion_limit * 2
        opening, closing = (b"[", b"]") if failure == "array-depth" else (b"{a=", b"}")
        content = b"private_value=" + opening * depth + b"0" + closing * depth
    path.write_bytes(content)
    monkeypatch.setattr(config, "MAX_CONFIG_BYTES", 1024 * 1024 if deep else 64, raising=False)
    if failure not in {"oversize", "array-depth", "table-depth"}:
        file_mode = {"growing": stat.S_IFREG, "fifo": stat.S_IFIFO, "device": stat.S_IFCHR}[failure]
        monkeypatch.setattr(
            os, "fstat", lambda fd: os.stat_result((file_mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        )

    snapshot = tmp_path / "snapshot.json"
    allowlist = tmp_path / "size-limits.json"
    if existing_outputs:
        snapshot.write_bytes(b"previous snapshot")
        allowlist.write_bytes(b"previous allowlist")
    before = {file: file.read_bytes() for file in tmp_path.iterdir()}
    options = {
        "report": ["--engine", "python"],
        "snapshot": ["--composition", "--snapshot", str(snapshot)],
        "seed": ["--size-limits", "--seed-allowlist"],
        "update": ["--size-limits", "--update-allowlist"],
    }[mode]
    args = [str(tmp_path), *options]
    if explicit:
        args += ["--config", str(path)]
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(args, stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    expected = "exceeds 64 bytes" if failure in {"oversize", "growing"} else "regular file"
    if deep:
        expected = "TOML nesting is too deep"
    assert expected in err.getvalue()
    assert "Traceback" not in err.getvalue()
    assert "private_value" not in err.getvalue()
    assert sys.getrecursionlimit() == recursion_limit
    assert str(path) in err.getvalue()
    assert {file: file.read_bytes() for file in tmp_path.iterdir()} == before


@pytest.mark.parametrize("value", ["[[[0]]]", "{a={b={c=0}}}"])
def test_shallow_toml_nesting_stays_valid(tmp_path: Path, value: str) -> None:
    path = tmp_path / "config.toml"
    path.write_text(f"other = {value}\n[tool.slopscope]\n", encoding="utf-8")
    assert config.load_config_from_pyproject(path) == config.SlopscopeConfig()
