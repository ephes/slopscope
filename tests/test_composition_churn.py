from __future__ import annotations

import io
import json
import os
import shutil
import signal
import subprocess
from datetime import date
from pathlib import Path

import pytest

from slopscope import cli, composition_churn, fallback

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

EXCLUDES = tuple(sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS))
TODAY = date(2026, 3, 20)


def git(repository: Path, *arguments: str, when: str = "2026-01-15T12:00:00+00:00") -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "-C", str(repository), *arguments], check=True, env=env, capture_output=True
    )


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_history(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "trunk")
    write(root, "app/src/core.py", "a = 1\nb = 2\nc = 3\n")
    write(root, "app/tests/test_core.py", "def test_a():\n    assert True\n")
    write(root, "app/README.md", "# App\n")
    write(root, "outside.py", "x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one", when="2026-01-10T12:00:00+00:00")

    git(root, "mv", "app/src/core.py", "app/src/engine.py")
    write(root, "app/src/engine.py", "a = 1\nb = 2\nc = 3\nd = 4\n")
    write(root, "app/build/generated.py", "g = 1\n" * 5)
    write(root, "outside.py", "x = 1\ny = 2\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "two", when="2026-03-05T12:00:00+00:00")

    git(root, "checkout", "-q", "-b", "feature")
    write(
        root,
        "app/tests/test_core.py",
        "def test_a():\n    assert True\n\n\ndef test_b():\n    pass\n",
    )
    git(root, "commit", "-q", "-am", "feature", when="2026-03-06T12:00:00+00:00")
    git(root, "checkout", "-q", "trunk")
    git(
        root,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "merge feature",
        "feature",
        when="2026-03-07T12:00:00+00:00",
    )
    return root / "app"


def test_monthly_churn_on_first_parent_history_of_a_subdirectory(tmp_path: Path) -> None:
    app = make_history(tmp_path)

    churn = composition_churn.collect_churn(
        app, ref="trunk", months=3, excluded_paths=EXCLUDES, today=TODAY
    )

    assert churn.status == "ok"
    assert churn.since == "2026-01"
    rows = {row.month: (row.added, row.removed) for row in churn.rows}
    assert rows == {
        # source, tests, other
        "2026-01": ((3, 2, 0), (0, 0, 0)),
        "2026-02": ((0, 0, 0), (0, 0, 0)),
        # The rename counts only its changed line; the merge counts once, as its first-parent diff;
        # build/ output, Markdown, and files outside the analyzed directory do not count.
        "2026-03": ((1, 4, 0), (0, 0, 0)),
    }


def test_months_limit_the_window(tmp_path: Path) -> None:
    app = make_history(tmp_path)

    churn = composition_churn.collect_churn(
        app, ref="trunk", months=1, excluded_paths=EXCLUDES, today=TODAY
    )

    assert [row.month for row in churn.rows] == ["2026-03"]


def test_configured_branch_is_used(tmp_path: Path) -> None:
    app = make_history(tmp_path)
    git(tmp_path, "checkout", "-q", "-b", "other", "trunk~1")

    on_trunk = composition_churn.collect_churn(
        app, ref="trunk", months=3, excluded_paths=EXCLUDES, today=TODAY
    )
    on_head = composition_churn.collect_churn(app, months=3, excluded_paths=EXCLUDES, today=TODAY)

    assert sum(sum(row.added) for row in on_trunk.rows) == 10
    assert sum(sum(row.added) for row in on_head.rows) == 6


def test_unknown_ref_and_non_git_paths_are_skipped(tmp_path: Path) -> None:
    app = make_history(tmp_path / "repo")
    plain = tmp_path / "plain"
    plain.mkdir()

    unknown = composition_churn.collect_churn(
        app, ref="missing", excluded_paths=EXCLUDES, today=TODAY
    )
    outside = composition_churn.collect_churn(plain, excluded_paths=EXCLUDES, today=TODAY)
    no_git = composition_churn.collect_churn(
        app, excluded_paths=EXCLUDES, today=TODAY, executable="definitely-not-git"
    )

    assert (unknown.status, unknown.reason) == ("skipped", "unknown branch or ref 'missing'")
    assert (outside.status, outside.reason) == ("skipped", "not a Git repository")
    assert (no_git.status, no_git.reason) == ("skipped", "git was not found")
    assert unknown.rows == outside.rows == ()


def test_parse_numstat_handles_renames_binary_and_odd_paths() -> None:
    output = (
        b"@2026-02\0\n1\t0\t\0src/old.py\0src/new.py\0"
        b"-\t-\timage.png\0"
        b"2\t3\t@odd name.py\0"
        b"@2026-01\0\n4\t0\tsrc/a.py\0"
    )

    assert composition_churn.parse_numstat(output) == [
        ("2026-02", "src/new.py", 1, 0),
        ("2026-02", "@odd name.py", 2, 3),
        ("2026-01", "src/a.py", 4, 0),
    ]


def test_cli_churn_uses_configured_branch_and_months(tmp_path: Path) -> None:
    app = make_history(tmp_path)
    write(
        app,
        "pyproject.toml",
        '[tool.slopscope.composition]\nchurn_branch = "trunk"\nchurn_months = 600\n',
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--composition", "--churn", "--format", "json", str(app)], stdout=stdout, stderr=stderr
    )

    churn = json.loads(stdout.getvalue())["churn"]
    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert churn["ref"] == "trunk"
    assert churn["months"] == 600
    assert churn["status"] == "ok"
    totals = {row["month"]: row["net"]["total"] for row in churn["rows"] if row["net"]["total"]}
    assert totals == {"2026-01": 5, "2026-03": 5}
    assert churn["rows"][-1]["added"].keys() == {"total", "source", "tests", "other"}


def test_cli_churn_outside_git_is_skipped_with_notice(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "x = 1\n")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli.run(
        ["--composition", "--churn", "--format", "plain", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert "slopscope: skipping churn: not a Git repository" in stderr.getvalue()
    assert "(skipped: not a Git repository)" in stdout.getvalue()


def test_churn_requires_composition(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.run(["--churn"])

    assert "--churn requires --composition" in capsys.readouterr().err


def test_first_month_counts_commits_in_other_time_zones(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "trunk")
    write(root, "src/a.py", "a = 1\n")
    git(root, "add", "-A")
    # Early on the first day of the window in the commit's own zone, which is still the
    # previous day in UTC.
    git(root, "commit", "-q", "-m", "early", when="2026-01-01T01:00:00+10:00")
    write(root, "src/b.py", "b = 1\n")
    git(root, "add", "-A")
    # An older-dated commit on top must not hide the one below it.
    git(root, "commit", "-q", "-m", "backdated", when="2025-06-01T12:00:00+00:00")
    write(root, "src/c.py", "c = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "late", when="2026-03-01T12:00:00+00:00")

    churn = composition_churn.collect_churn(
        root, ref="trunk", months=3, excluded_paths=EXCLUDES, today=TODAY
    )

    added = {row.month: sum(row.added) for row in churn.rows}
    assert added == {"2026-01": 1, "2026-02": 0, "2026-03": 1}


def test_huge_month_windows_do_not_crash(tmp_path: Path) -> None:
    app = make_history(tmp_path)

    churn = composition_churn.collect_churn(
        app, ref="trunk", months=10**6, excluded_paths=EXCLUDES, today=TODAY
    )

    assert churn.status == "ok"
    assert churn.months == composition_churn.MAX_MONTHS


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group fixture")
@pytest.mark.parametrize("stage", ["inside", "verify", "log"])
def test_cli_churn_timeout_kills_children_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    import sys
    import time

    wrapper = tmp_path / "slow-git"
    child_pid = tmp_path / "child.pid"
    calls = tmp_path / "calls"
    survived = tmp_path / "survived"
    child_code = (
        f"import time; from pathlib import Path; time.sleep(1.8); Path({str(survived)!r}).touch()"
    )
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        f"stage = {stage!r}\n"
        f"with open({str(calls)!r}, 'a') as out: out.write(' '.join(sys.argv[3:]) + '\\n')\n"
        "command = sys.argv[3:]\n"
        "hang = (stage == 'inside' or (stage == 'verify' and '--verify' in command) "
        "or (stage == 'log' and command[0] == 'log'))\n"
        "if not hang:\n"
        "    print('true')\n"
        "    sys.exit(0)\n"
        f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        f"with open({str(child_pid)!r}, 'w') as out: out.write(str(child.pid))\n"
        "time.sleep(2.5)\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    original_git = composition_churn._git

    def slow_git(
        executable: str, path: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[bytes] | None:
        return original_git(str(wrapper), path, arguments)

    monkeypatch.setattr(composition_churn, "_git", slow_git)
    monkeypatch.setattr(composition_churn, "GIT_TIMEOUT_SECONDS", 1.0, raising=False)
    write(tmp_path, "a.py", "x = 1\n")
    stdout, stderr = io.StringIO(), io.StringIO()
    started = time.monotonic()
    result = cli.run(
        ["--composition", "--churn", "--format", "json", str(tmp_path)],
        stdout=stdout,
        stderr=stderr,
    )
    elapsed = time.monotonic() - started

    assert result == 0
    assert elapsed < 2.4
    report = json.loads(stdout.getvalue())
    assert report["churn"]["status"] == "skipped"
    assert report["churn"]["reason"] == "git timed out after 1 seconds"
    assert "skipping churn: git timed out" in stderr.getvalue()
    assert child_pid.exists()
    time.sleep(2)
    assert not survived.exists()
    assert sum(line.startswith("log ") for line in calls.read_text().splitlines()) == (
        1 if stage == "log" else 0
    )


def test_unsupported_since_filter_retries_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_git = composition_churn._git
    logs: list[list[str]] = []

    def old_git(
        executable: str, path: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[bytes] | None:
        if arguments[0] == "log":
            logs.append(arguments)
            if any(arg.startswith("--since-as-filter=") for arg in arguments):
                return subprocess.CompletedProcess(arguments, 129, b"", b"unsupported option")
        return original_git(executable, path, arguments)

    app = make_history(tmp_path)
    monkeypatch.setattr(composition_churn, "_git", old_git)
    result = composition_churn.collect_churn(app, excluded_paths=EXCLUDES, today=TODAY)
    assert result.status == "ok"
    assert len(logs) == 2


def test_interrupted_git_kills_group_and_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    process = Mock()
    process.pid = 12345
    process.stdout = io.BytesIO()
    process.stderr = io.BytesIO()
    process.communicate.side_effect = [KeyboardInterrupt, (b"", b"")]
    group_kill = Mock()
    monkeypatch.setattr(composition_churn, "os", SimpleNamespace(name="posix", killpg=group_kill))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))

    with pytest.raises(KeyboardInterrupt):
        composition_churn._git("git", tmp_path, ["log"])

    group_kill.assert_called_once_with(process.pid, signal.SIGKILL)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert not process.stdout.closed and not process.stderr.closed
    assert process.communicate.call_count == 2
    assert process.communicate.call_args.kwargs == {"timeout": 1}


@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_windows_tree_kill_failure_still_reaps_and_skips_churn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    process = Mock()
    process.pid = 12345
    process.stdout = io.BytesIO()
    process.stderr = io.BytesIO()
    process.communicate.side_effect = subprocess.TimeoutExpired("git", 30)
    tree_kill = Mock(
        side_effect=FileNotFoundError("taskkill")
        if failure == "missing"
        else subprocess.TimeoutExpired("taskkill", 5)
    )
    monkeypatch.setattr(composition_churn, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(subprocess, "run", tree_kill)

    result = composition_churn.collect_churn(tmp_path, excluded_paths=EXCLUDES)

    assert result.status == "skipped"
    assert result.reason == "git timed out after 30 seconds"
    tree_kill.assert_called_once_with(
        ["taskkill", "/PID", "12345", "/T", "/F"], capture_output=True, check=False, timeout=5
    )
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    assert not process.stdout.closed and not process.stderr.closed
    assert process.communicate.call_count == 2
    assert process.communicate.call_args.kwargs == {"timeout": 1}


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_long_running_git_cleanup_never_closes_busy_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock, call

    process = Mock()
    process.pid = 12345
    process.communicate.side_effect = subprocess.TimeoutExpired("git", 30)
    # Windows reader threads can hold the pipe lock even after killing Git.
    process.stdout.close.side_effect = AssertionError("blocking pipe close")
    process.stderr.close.side_effect = AssertionError("blocking pipe close")
    monkeypatch.setattr(composition_churn, "os", SimpleNamespace(name=platform, killpg=Mock()))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(subprocess, "run", Mock())

    with pytest.raises(subprocess.TimeoutExpired):
        composition_churn._git("git", tmp_path, ["log"])

    assert process.communicate.call_args_list == [call(timeout=30), call(timeout=1)]
    process.kill.assert_called_once_with()
    process.stdout.read.assert_not_called()
    process.stderr.read.assert_not_called()
    process.stdout.close.assert_not_called()
    process.stderr.close.assert_not_called()


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_git_timeout_drain_propagates_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption: type[BaseException],
    platform: str,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock, call

    process = Mock()
    process.pid = 12345
    interrupted = interruption("interrupted drain")
    process.communicate.side_effect = [subprocess.TimeoutExpired("git", 30), interrupted]
    group_kill = Mock()
    monkeypatch.setattr(composition_churn, "os", SimpleNamespace(name=platform, killpg=group_kill))
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    tree_kill = Mock()
    monkeypatch.setattr(subprocess, "run", tree_kill)

    with pytest.raises(interruption) as caught:
        composition_churn.collect_churn(tmp_path, excluded_paths=EXCLUDES)

    assert caught.value is interrupted
    assert process.communicate.call_args_list == [call(timeout=30), call(timeout=1)]
    process.kill.assert_called_once_with()
    if platform == "posix":
        group_kill.assert_called_once_with(process.pid, signal.SIGKILL)
    else:
        tree_kill.assert_called_once_with(
            ["taskkill", "/PID", "12345", "/T", "/F"], capture_output=True, check=False, timeout=5
        )
