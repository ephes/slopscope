from __future__ import annotations

import io
import subprocess
import sys
import time
from pathlib import Path

import pytest

from slopscope import cli, cloc


@pytest.mark.parametrize("engine", ["cloc", "auto"])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("file_only", [False, True])
def test_hanging_cloc_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, profile: bool, file_only: bool
) -> None:
    script = tmp_path / "hang.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(cloc, "CLOC_TIMEOUT_SECONDS", 0.1, raising=False)
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    monkeypatch.setattr(
        cloc, "build_language_summary_command", lambda path, **kwargs: [sys.executable, str(script)]
    )
    monkeypatch.setattr(
        cloc, "build_file_summary_command", lambda path, **kwargs: [sys.executable, str(script)]
    )
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
    started = time.monotonic()
    assert cli.run(args, stdout=out, stderr=err) == 2
    assert time.monotonic() - started < 5
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
            assert kwargs["start_new_session"] == (sys.platform != "win32")

        def communicate(self, *, timeout: float) -> tuple[str, str]:
            assert timeout == 30
            return "output", ""

    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", Process)
    assert cloc.run_command(["cloc"]) == cloc.ClocResult(0, "output", "")


@pytest.mark.parametrize("wrapper_exits", [False, True])
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group guarantee")
def test_cloc_timeout_terminates_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wrapper_exits: bool
) -> None:
    survived = tmp_path / "survived"
    ready = tmp_path / "ready"
    child = (
        f"import time; from pathlib import Path; time.sleep(0.8); Path({str(survived)!r}).touch()"
    )
    script = tmp_path / "wrapper.py"
    script.write_text(
        "import subprocess, sys, time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"Path({str(ready)!r}).touch()\n" + ("" if wrapper_exits else "time.sleep(60)\n"),
        encoding="utf-8",
    )
    monkeypatch.setattr(cloc, "CLOC_TIMEOUT_SECONDS", 0.3)
    assert cloc.run_command([sys.executable, str(script)]).returncode == 2
    assert ready.exists()
    time.sleep(1)
    assert not survived.exists()
