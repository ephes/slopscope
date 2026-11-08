"""Portable, synthetic command output budget regressions."""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from slopscope import cli, cloc, fallback


@pytest.mark.parametrize("command", ["cloc", "git"])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_command_output_overflow_discards_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, channel: str
) -> None:
    popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []
    code = f"import sys; sys.{channel}.buffer.write(b'x' * 65537)"

    def launch(args: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        child = popen(args if args[0] == "taskkill" else [sys.executable, "-c", code], **kwargs)  # type: ignore[call-overload]
        children.append(child)
        return child  # type: ignore[no-any-return]

    monkeypatch.setattr(subprocess, "Popen", launch)
    if command == "cloc":
        monkeypatch.setattr(cloc, "MAX_CLOC_OUTPUT_BYTES", 65536, raising=False)
        result = cloc.run_command(["synthetic-cloc"])
        assert result.returncode == 2
        assert result.stdout == ""
        assert "output exceeded 65536 bytes" in result.stderr
    else:
        monkeypatch.setattr(fallback, "MAX_GIT_DISCOVERY_OUTPUT_BYTES", 65536, raising=False)
        with pytest.raises(fallback.GitDiscoveryError, match="output exceeded 65536 bytes"):
            fallback.run_git_ls_files(tmp_path)
    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None
    assert child.stdout is not None and child.stdout.closed
    assert child.stderr is not None and child.stderr.closed


@pytest.mark.parametrize("command", ["cloc", "git"])
def test_exact_combined_budget_preserves_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    popen = subprocess.Popen
    output = b"space name.py\0line\nname.py\0"
    error = b"diagnostic"
    code = f"import sys; sys.stdout.buffer.write({output!r}); sys.stderr.buffer.write({error!r})"
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **kw: popen(
            args if args[0] == "taskkill" else [sys.executable, "-c", code], **kw
        ),
    )
    budget = len(output) + len(error)
    if command == "git":
        monkeypatch.setattr(fallback, "MAX_GIT_DISCOVERY_OUTPUT_BYTES", budget, raising=False)
        result = fallback.run_git_ls_files(tmp_path)
        assert result == fallback.GitLsFilesResult(0, output)
        assert fallback.parse_git_ls_files_output(result.stdout) == [
            Path("space name.py"),
            Path("line\nname.py"),
        ]
    else:
        monkeypatch.setattr(cloc, "MAX_CLOC_OUTPUT_BYTES", budget, raising=False)
        assert cloc.run_command(["cloc"]) == cloc.ClocResult(0, output.decode(), error.decode())


@pytest.mark.parametrize("mode", ["report", "profile", "composition", "seed", "update"])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_discovery_overflow_never_falls_back_or_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, channel: str
) -> None:
    popen = subprocess.Popen
    code = f"import sys; sys.{channel}.buffer.write(b'app.py\\0' * 1000)"
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **kw: popen(
            args if args[0] == "taskkill" else [sys.executable, "-c", code], **kw
        ),
    )
    monkeypatch.setattr(fallback, "MAX_GIT_DISCOVERY_OUTPUT_BYTES", 100, raising=False)

    def no_walk(*args: object, **kwargs: object) -> list[Path]:
        raise AssertionError("overflow must not trigger filesystem fallback")

    monkeypatch.setattr(fallback, "discover_filesystem_files", no_walk)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.profiles]]\nname = "physical"\nphysical_lines = true\n',
        encoding="utf-8",
    )
    snapshot, allowlist = tmp_path / "snapshot.json", tmp_path / "size-limits.json"
    snapshot.write_bytes(b"old snapshot")
    if mode != "seed":
        allowlist.write_bytes(b"{}\n")
    previous = allowlist.read_bytes() if allowlist.exists() else None
    options = {
        "report": ["--engine", "python"],
        "profile": ["--profile", "physical", "--total-only"],
        "composition": ["--composition", "--snapshot", str(snapshot)],
        "seed": ["--size-limits", "--seed-allowlist"],
        "update": ["--size-limits", "--update-allowlist"],
    }[mode]
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), *options], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "output exceeded 100 bytes" in err.getvalue()
    assert snapshot.read_bytes() == b"old snapshot"
    assert (allowlist.read_bytes() if allowlist.exists() else None) == previous


@pytest.mark.parametrize("engine", ["cloc", "auto"])
@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_cloc_overflow_never_publishes_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: str, profile: bool, channel: str
) -> None:
    popen = subprocess.Popen
    code = f"import sys; sys.{channel}.buffer.write(b'x' * 1000)"
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda args, **kw: popen(
            args if args[0] == "taskkill" else [sys.executable, "-c", code], **kw
        ),
    )
    monkeypatch.setattr(cloc, "MAX_CLOC_OUTPUT_BYTES", 100, raising=False)
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.profiles]]\nname = "code"\n', encoding="utf-8"
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b"old snapshot")
    options = ["--profile", "code", "--total-only"] if profile else []
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), "--engine", engine, *options], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "output exceeded 100 bytes" in err.getvalue()
    assert snapshot.read_bytes() == b"old snapshot"


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("wrapper_exited", [False, True])
@pytest.mark.parametrize("failure", ["overflow", "timeout", "interrupt", "exit"])
def test_capture_failure_kills_tree_and_reaps_wrapper(
    monkeypatch: pytest.MonkeyPatch, platform: str, wrapper_exited: bool, failure: str
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from slopscope import _capture

    process = Mock(
        pid=12345, args=["synthetic"], stdout=io.BytesIO(b"x" * 60), stderr=io.BytesIO(b"y" * 60)
    )
    if wrapper_exited:
        process.kill.side_effect = ProcessLookupError
    error = {
        "overflow": _capture.CaptureError,
        "timeout": subprocess.TimeoutExpired,
        "interrupt": KeyboardInterrupt,
        "exit": SystemExit,
    }[failure]
    if failure != "overflow":
        exception = subprocess.TimeoutExpired("synthetic", 1) if failure == "timeout" else error()
        process.wait.side_effect = [exception, 0]
    group_kill, tree_kill = Mock(), Mock()
    monkeypatch.setattr(_capture, "os", SimpleNamespace(name=platform, killpg=group_kill))
    monkeypatch.setattr(subprocess, "run", tree_kill)
    with pytest.raises(error):
        _capture.capture_process(
            process, timeout=1, max_bytes=100 if failure == "overflow" else 120, label="synthetic"
        )
    assert process.stdout.closed and process.stderr.closed
    process.kill.assert_called_once_with()
    assert process.wait.call_args.kwargs == {"timeout": 5}
    process.communicate.assert_not_called()
    if platform == "posix":
        group_kill.assert_called_once()
        tree_kill.assert_not_called()
    else:
        tree_kill.assert_called_once_with(
            ["taskkill", "/PID", "12345", "/T", "/F"], capture_output=True, check=False, timeout=5
        )


@pytest.mark.parametrize("command", ["cloc", "git"])
@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_overflow_stops_descendant_retaining_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str, channel: str
) -> None:
    import time

    ready, completed = tmp_path / "ready", tmp_path / "completed"
    holder = (
        "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); "
        "time.sleep(1); pathlib.Path(sys.argv[2]).touch()"
    )
    wrapper = (
        "import pathlib,subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], *sys.argv[2:]]); "
        "ready=pathlib.Path(sys.argv[2]); "
        "deadline=time.monotonic()+5; "
        "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
        "assert ready.exists(); "
        f"sys.{channel}.buffer.write(b'x'*65537); sys.{channel}.buffer.flush(); time.sleep(5)"
    )
    popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []

    def launch(args: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        process = popen(
            args
            if args[0] == "taskkill"
            else [sys.executable, "-c", wrapper, holder, str(ready), str(completed)],
            **kwargs,
        )  # type: ignore[call-overload]
        children.append(process)
        return process  # type: ignore[no-any-return]

    monkeypatch.setattr(subprocess, "Popen", launch)
    if command == "cloc":
        monkeypatch.setattr(cloc, "MAX_CLOC_OUTPUT_BYTES", 65536)
        assert "output exceeded" in cloc.run_command(["cloc"]).stderr
    else:
        monkeypatch.setattr(fallback, "MAX_GIT_DISCOVERY_OUTPUT_BYTES", 65536)
        with pytest.raises(fallback.GitDiscoveryError, match="output exceeded"):
            fallback.run_git_ls_files(tmp_path)
    assert ready.exists()
    assert children[0].poll() is not None
    assert children[0].stdout is not None and children[0].stdout.closed
    assert children[0].stderr is not None and children[0].stderr.closed
    # A surviving holder would finish this bounded task; the tree must stop it first.
    time.sleep(1.2)
    assert not completed.exists()
