"""Synthetic Git history integrity and bounded capture regressions."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from slopscope import _capture, cli, fallback
from slopscope import composition_churn as churn


@pytest.mark.parametrize(
    "output",
    [
        b"@2026-03\0\n1\t0\t\0",  # missing old and new paths
        b"@2026-03\0\n1\t0\t\0old.py\0",  # missing destination
        b"@2026-03\0\n1\t0\t\0old.py\0new.py",  # missing final NUL
        b"@2026-03\0\n1\t0\t\0\0new.py\0",  # empty source
        b"@2026-03\0\n1\t0\t\0old.py\0\0",  # empty destination
        b"@2026-03\0\n1\t0\0",  # missing path field
        b"@2026-03\0\n1\t\tapp.py\0",
        b"@2026-03\0\n-1\t0\tapp.py\0",
        b"@2026-03\0\nbad\t0\tapp.py\0",
        b"@2026-03\0\n-\t1\tapp.py\0",
        b"@2026-13\0\n1\t0\tapp.py\0",
        b"@0000-01\0\n1\t0\tapp.py\0",
        b"@2026-3\0\n1\t0\tapp.py\0",
        b"1\t0\tapp.py\0",  # no month
        b"@2026-03\0garbage\0",
        b"@2026-03",  # unterminated marker
    ],
    ids=lambda output: repr(output)[:80],
)
def test_malformed_history_skips_whole_churn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: bytes
) -> None:
    def fake_git(
        executable: str, path: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            arguments, 0, output if arguments[0] == "log" else b"true\n", b""
        )

    monkeypatch.setattr(churn, "_git", fake_git)
    result = churn.collect_churn(tmp_path, excluded_paths=(), today=date(2026, 3, 20))
    assert result.status == "skipped"
    assert result.rows == ()
    assert result.reason is not None and "malformed git history" in result.reason
    assert len(result.reason) < 200


def test_valid_history_keeps_binary_exclusions_and_newline_rename_paths() -> None:
    assert churn.parse_numstat(
        b"@2026-03\0\n1\t2\t\0old\nname.py\0new\nname.py\0-\t-\t\0old.png\0new.png\0\0@2026-02\0"
    ) == [("2026-03", "new\nname.py", 1, 2)]
    assert churn.parse_numstat(b"") == []
    assert churn.parse_numstat(b"@2026-03\0") == []


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_fast_git_output_overflow_skips_churn_in_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    # Launch Python directly so the fixture requires no shell, shebang or executable bits.
    popen = subprocess.Popen
    children: list[subprocess.Popen[bytes]] = []
    code = (
        "import sys; "
        "command = sys.argv[1:]; "
        f"stream = sys.{channel}.buffer; "
        "stream.write(b'x' * 262144) if command[0] == 'log' else sys.stdout.write('true\\n')"
    )

    def fake_popen(command: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
        if command[0] == "synthetic-git":
            process = popen([sys.executable, "-c", code, *command[3:]], **kwargs)  # type: ignore[call-overload]
            children.append(process)
            return process  # type: ignore[no-any-return]
        return popen(command, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(churn, "MAX_GIT_OUTPUT_BYTES", 65536, raising=False)
    original_git = churn._git
    monkeypatch.setattr(
        churn, "_git", lambda _executable, path, args: original_git("synthetic-git", path, args)
    )
    monkeypatch.setattr(
        fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(0, b"app.py\0")
    )
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("previous snapshot", encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.run(
            [
                str(tmp_path),
                "--composition",
                "--churn",
                "--format",
                "json",
                "--snapshot",
                str(snapshot),
            ],
            stdout=out,
            stderr=err,
        )
        == 0
    )
    report = json.loads(out.getvalue())
    assert report["churn"]["status"] == "skipped"
    assert "output exceeded" in report["churn"]["reason"]
    assert "skipping churn" in err.getvalue()
    assert report["total"]["files"] == 1
    assert json.loads(snapshot.read_text(encoding="utf-8")) == report
    assert len(children) == 3  # overflow is never retried
    assert all(process.poll() is not None for process in children)
    assert all(process.stdout is not None and process.stdout.closed for process in children)
    assert all(process.stderr is not None and process.stderr.closed for process in children)


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("wrapper_exited", [False, True])
def test_capture_budget_is_combined_and_cleanup_reaps_owned_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, wrapper_exited: bool
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    reads: list[int] = []

    class Stream(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 < size <= 65536
            reads.append(size)
            return super().read(size)

    # Neither channel exceeds the budget alone; their combined output does.
    process = Mock(pid=12345, stdout=Stream(b"x" * 60), stderr=Stream(b"y" * 60))
    if wrapper_exited:
        process.kill.side_effect = ProcessLookupError
    group_kill, tree_kill = Mock(), Mock()
    monkeypatch.setattr(_capture, "os", SimpleNamespace(name=platform, killpg=group_kill))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(subprocess, "run", tree_kill)
    monkeypatch.setattr(churn, "MAX_GIT_OUTPUT_BYTES", 100)
    with pytest.raises(churn.GitCaptureError, match="output exceeded 100 bytes"):
        churn._git("git", tmp_path, ["log"])
    assert reads
    assert process.stdout.closed and process.stderr.closed
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    process.communicate.assert_not_called()
    if platform == "posix":
        group_kill.assert_called_once()
        tree_kill.assert_not_called()
    else:
        tree_kill.assert_called_once_with(
            ["taskkill", "/PID", "12345", "/T", "/F"], capture_output=True, check=False, timeout=5
        )


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_one_byte_overflow_is_detected_before_child_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    popen = subprocess.Popen
    code = (
        f'import sys,time; sys.{channel}.buffer.write(b"x" * 65537); '
        f"sys.{channel}.buffer.flush(); time.sleep(5)"
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda _command, **kwargs: popen([sys.executable, "-c", code], **kwargs),
    )
    monkeypatch.setattr(churn, "MAX_GIT_OUTPUT_BYTES", 65536)
    monkeypatch.setattr(churn, "GIT_TIMEOUT_SECONDS", 0.5)
    with pytest.raises(churn.GitCaptureError, match="output exceeded"):
        churn._git("git", tmp_path, ["log"])


@pytest.mark.parametrize("error", [ValueError("closed pipe"), MemoryError("synthetic failure")])
def test_reader_exception_never_publishes_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    class FailingPipe(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            if self.tell():
                raise error
            return super().read(size)

    # The failure follows a complete valid record, which must not become partial totals.
    process = Mock(
        pid=12345,
        args=["git", "log"],
        returncode=0,
        stdout=FailingPipe(b"@2026-03\0\n1\t0\tapp.py\0"),
        stderr=io.BytesIO(),
    )

    def fake_popen(command: list[str], **kwargs: object) -> object:
        if command[3] == "log":
            return process
        return Mock(args=command, returncode=0, stdout=io.BytesIO(b"true\n"), stderr=io.BytesIO())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(_capture, "os", SimpleNamespace(name="posix", killpg=Mock()))
    result = churn.collect_churn(tmp_path, excluded_paths=(), today=date(2026, 3, 20))
    assert result.status == "skipped"
    assert result.reason == "could not read git output"
    assert result.rows == ()
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert process.stdout.closed and process.stderr.closed
