"""Synthetic history moves and portable process decoding regressions."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from slopscope import cli, cloc, composition_churn
from slopscope.composition_render import _churn_to_dict


@pytest.mark.parametrize("fallback", [False, True])
def test_churn_pins_verified_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fallback: bool
) -> None:
    def git(*args: str) -> str:
        return (
            subprocess.check_output(
                ["git", "-C", str(tmp_path), *args],
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "Test",
                    "GIT_COMMITTER_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_EMAIL": "test@example.invalid",
                    "GIT_AUTHOR_DATE": "2026-03-01T12:00:00+00:00",
                    "GIT_COMMITTER_DATE": "2026-03-01T12:00:00+00:00",
                },
            )
            .decode()
            .strip()
        )

    git("init", "-q", "-b", "trunk")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "first")
    original = git("rev-parse", "HEAD")
    source.write_text("x = 1\ny = 2\n", encoding="utf-8")
    git("commit", "-qam", "second")
    newer = git("rev-parse", "HEAD")
    git("update-ref", "refs/heads/trunk", original)
    real_git = composition_churn._git
    logs: list[list[str]] = []

    def moved(
        executable: str, path: Path, arguments: list[str]
    ) -> subprocess.CompletedProcess[bytes] | None:
        result = real_git(executable, path, arguments)
        if "--verify" in arguments:
            git("update-ref", "refs/heads/trunk", newer)
        if arguments[0] == "log":
            logs.append(arguments)
            if fallback and len(logs) == 1:
                return subprocess.CompletedProcess(arguments, 129, b"", b"unsupported option")
        return result

    monkeypatch.setattr(composition_churn, "_git", moved)
    result = composition_churn.collect_churn(
        tmp_path, ref="trunk", months=1, today=date(2026, 3, 20), excluded_paths=()
    )
    assert result.status == "ok"
    assert sum(sum(row.added) for row in result.rows) == 1
    assert all(arguments[1] == original for arguments in logs)
    assert len(logs) == (2 if fallback else 1)
    assert _churn_to_dict(result)["commit"] == original
    assert result.ref == "trunk"


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
@pytest.mark.parametrize("payload", [b"\xff", "Python,caf\u00e9.py,0,0,1\n".encode()])
@pytest.mark.parametrize("profile", [False, True])
def test_cloc_decode_failure_is_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, channel: str, payload: bytes, profile: bool
) -> None:
    popen = subprocess.Popen
    code = f"import sys; sys.{channel}.buffer.write({payload!r})"
    monkeypatch.setattr(
        subprocess, "Popen", lambda args, **kw: popen([sys.executable, "-c", code], **kw)
    )
    # Constrain the decoder without depending on installed locales or UTF-8 mode.
    monkeypatch.setattr(io, "text_encoding", lambda _: "ascii")
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.profiles]]\nname = "code"\n', encoding="utf-8"
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b"previous snapshot")
    out, err = io.StringIO(), io.StringIO()
    options = ["--profile", "code", "--total-only"] if profile else []
    assert cli.run([str(tmp_path), "--engine", "cloc", *options], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "could not decode cloc" in err.getvalue()
    assert "Traceback" not in err.getvalue()
    assert len(err.getvalue()) < 300
    assert snapshot.read_bytes() == b"previous snapshot"
