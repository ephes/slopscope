from __future__ import annotations

import io
import os
import signal
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
    class Process:
        pid = 12345

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.calls = 0

        def communicate(self, *, timeout: float) -> tuple[str, str]:
            self.calls += 1
            if self.calls == 1:
                assert timeout == 0.1
                raise subprocess.TimeoutExpired("cloc", timeout)
            assert timeout == 1
            return "", ""

        def kill(self) -> None:
            pass

        def wait(self, *, timeout: float) -> int:
            assert timeout == 5
            return -9

    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", Process)
    monkeypatch.setattr("slopscope.cloc.os.killpg", lambda pid, sig: None, raising=False)
    monkeypatch.setattr(cloc, "CLOC_TIMEOUT_SECONDS", 0.1, raising=False)
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
    class Process:
        returncode = 0

        def __init__(self, *args: object, **kwargs: object) -> None:
            assert kwargs["start_new_session"] == (os.name == "posix")

        def communicate(self, *, timeout: float) -> tuple[str, str]:
            assert timeout == 30
            return "output", ""

    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", Process)
    assert cloc.run_command(["cloc"]) == cloc.ClocResult(0, "output", "")


@pytest.mark.parametrize("wrapper_exits", [False, True])
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group guarantee")
def test_cloc_timeout_terminates_descendants(
    monkeypatch: pytest.MonkeyPatch, wrapper_exits: bool
) -> None:
    events: list[str] = []

    class Process:
        pid = 12345
        returncode = 0 if wrapper_exits else None

        def __init__(self, *args: object, **kwargs: object) -> None:
            assert kwargs["start_new_session"] is True

        def communicate(self, *, timeout: float) -> tuple[str, str]:
            if not events:
                assert timeout == cloc.CLOC_TIMEOUT_SECONDS
                raise subprocess.TimeoutExpired("cloc", timeout)
            assert events == ["kill group", "kill wrapper"]
            assert timeout == 1
            events.append("drain pipes")
            return "partial output", "partial error"

        def kill(self) -> None:
            events.append("kill wrapper")
            if wrapper_exits:
                raise ProcessLookupError

        def wait(self, *, timeout: float) -> int:
            assert timeout == 5
            events.append("reap wrapper")
            return 0 if wrapper_exits else -9

    def kill_group(pid: int, sig: int) -> None:
        assert pid == Process.pid
        assert sig == signal.SIGKILL
        events.append("kill group")

    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", Process)
    monkeypatch.setattr("slopscope.cloc.os.killpg", kill_group)
    assert cloc.run_command(["cloc"]) == cloc.ClocResult(
        2, "", "slopscope: cloc timed out after 30 seconds"
    )
    assert events == ["kill group", "kill wrapper", "drain pipes", "reap wrapper"]
