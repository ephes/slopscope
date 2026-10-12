from __future__ import annotations

import io
from pathlib import Path

import pytest

from slopscope import cli, config


@pytest.mark.parametrize("explicit", [False, True])
def test_invalid_utf8_config_returns_usage_error(tmp_path: Path, explicit: bool) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_bytes(b"[tool.slopscope]\n# private content\xff\n")
    args = [str(tmp_path), "--engine", "python"]
    if explicit:
        args += ["--config", str(path)]
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(args, stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert err.getvalue() == f"slopscope: configuration file {path} must be UTF-8\n"


def test_non_ascii_utf8_config_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[tool.slopscope]\nexclude_dirs = ["généré"]\n', encoding="utf-8")
    assert config.load_config_from_pyproject(path).exclude_dirs == ("généré",)
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), "--engine", "python"], stdout=out, stderr=err) == 0
    assert out.getvalue()
    assert not err.getvalue()
