from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from slopscope import cli, cloc


@pytest.mark.parametrize("engine", ["cloc", "auto"])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("file_only", [False, True])
def test_hanging_cloc_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, profile: bool, file_only: bool
) -> None:
    def timeout_capture(*args: object, **kwargs: object) -> None:
        assert kwargs["timeout"] == 0.1
        raise subprocess.TimeoutExpired("cloc", 0.1)

    monkeypatch.setattr(cloc, "capture_process", timeout_capture)
    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", lambda *a, **kw: object())
    monkeypatch.setattr(cloc, "CLOC_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    monkeypatch.setattr(cloc, "build_language_summary_command", lambda path, **kwargs: ["cloc"])
    monkeypatch.setattr(cloc, "build_file_summary_command", lambda path, **kwargs: ["cloc"])
    if file_only:
        monkeypatch.setattr(
            cloc,
            "run_language_summary",
            lambda path: cloc.ClocResult(
                0, "language,filename,blank,comment,code\nPython,1,0,0,1\n", ""
            ),
        )
    args = ["--engine", engine, str(tmp_path)]
    if profile:
        (tmp_path / "pyproject.toml").write_text(
            '[[tool.slopscope.profiles]]\nname = "yaml"\ninclude_languages = ["YAML"]\n',
            encoding="utf-8",
        )
        args += ["--profile", "yaml", "--total-only"]
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(args, stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "slopscope: cloc timed out after 0.1 seconds" in err.getvalue()


@pytest.mark.parametrize("engine", ["cloc", "auto"])
@pytest.mark.parametrize("error", [FileNotFoundError("gone"), PermissionError("denied")])
def test_cloc_startup_failure_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: OSError, engine: str
) -> None:
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)

    def fail(*args: object, **kwargs: object) -> subprocess.Popen[str]:
        raise error

    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", fail)
    out, err = io.StringIO(), io.StringIO()
    assert cli.run(["--engine", engine, str(tmp_path)], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "slopscope: could not run cloc:" in err.getvalue()


def test_cloc_supplies_finite_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import Mock

    process = Mock()
    popen = Mock(return_value=process)

    def capture(child: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert child is process
        assert kwargs == {"timeout": 30, "max_bytes": 16 * 1024 * 1024, "label": "cloc"}
        return subprocess.CompletedProcess(["cloc"], 0, b"output\r\nnext\rlast", b"")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(cloc, "capture_process", capture)
    assert cloc.run_command(["cloc"]) == cloc.ClocResult(0, "output\nnext\nlast", "")
    assert popen.call_args.kwargs == {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "start_new_session": os.name == "posix",
    }
