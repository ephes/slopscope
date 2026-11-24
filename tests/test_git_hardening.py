"""Git processes must not run commands configured by the analyzed repository."""

from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from slopscope import _git_env, cli, cloc, composition_churn, fallback
from slopscope.report import CompositionChurn

posix_git = pytest.mark.skipif(
    os.name != "posix" or shutil.which("git") is None, reason="needs git on a POSIX shell"
)

EXCLUDES = tuple(sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS))
IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's global safe.bareRepository or fsmonitor setting must not decide the outcome.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        env={**os.environ, **IDENTITY},
        capture_output=True,
        text=True,
    ).stdout


def marker_command(marker: Path) -> str:
    return f"touch {shlex.quote(str(marker))}; false"


def embedded_bare_repository_clone(tmp_path: Path, marker: Path) -> Path:
    """Clone a repository whose tracked ``evil/`` directory is laid out as a bare repository."""

    source = tmp_path / "source"
    (source / "evil" / "refs" / "heads").mkdir(parents=True)
    (source / "evil" / "objects").mkdir()
    git(source, "init", "-q")
    (source / "evil" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (source / "evil" / "config").write_text(
        "[core]\n"
        "\trepositoryformatversion = 0\n"
        "\tbare = false\n"
        "\tworktree = ..\n"
        f'\tfsmonitor = "{marker_command(marker)}"\n',
        encoding="utf-8",
    )
    (source / "evil" / "refs" / "heads" / ".keep").write_text("", encoding="utf-8")
    (source / "evil" / "objects" / ".keep").write_text("", encoding="utf-8")
    (source / "evil" / "module.py").write_text("x = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[[tool.slopscope.projects]]\nname = "core"\npath = "evil"\n', encoding="utf-8"
    )
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "init")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(source), str(clone))
    return clone


def test_hardened_env_appends_after_existing_command_line_config() -> None:
    base = {
        "PATH": "/bin",
        "GIT_ALLOW_PROTOCOL": "file:ssh",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "user.name",
        "GIT_CONFIG_VALUE_0": "Someone",
    }

    env = _git_env.hardened_git_env(base)

    assert base["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "user.name"
    assert env["GIT_CONFIG_VALUE_0"] == "Someone"
    count = int(env["GIT_CONFIG_COUNT"])
    added = {
        env[f"GIT_CONFIG_KEY_{index}"]: env[f"GIT_CONFIG_VALUE_{index}"]
        for index in range(1, count)
    }
    assert added == {
        "safe.bareRepository": "explicit",
        "core.fsmonitor": "false",
        "log.showSignature": "false",
    }
    assert env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_ALLOW_PROTOCOL"] == ""
    assert base["GIT_ALLOW_PROTOCOL"] == "file:ssh"
    assert env["PATH"] == "/bin"
    assert env["GIT_CONFIG_PARAMETERS"] == (
        "'safe.bareRepository=explicit' 'core.fsmonitor=false' 'log.showSignature=false'"
    )


def test_hardened_env_appends_after_existing_parameters() -> None:
    base = {"GIT_CONFIG_PARAMETERS": "'core.fsmonitor=true' "}

    env = _git_env.hardened_git_env(base)

    assert env["GIT_CONFIG_PARAMETERS"].startswith("'core.fsmonitor=true' 'safe.bareRepository")
    assert env["GIT_CONFIG_PARAMETERS"].endswith("'log.showSignature=false'")


@pytest.mark.parametrize("count", ["", "x", "-2"])
def test_hardened_env_replaces_a_count_git_would_reject(count: str) -> None:
    env = _git_env.hardened_git_env({"GIT_CONFIG_COUNT": count})

    assert env["GIT_CONFIG_COUNT"] == "3"
    assert env["GIT_CONFIG_KEY_0"] == "safe.bareRepository"


@posix_git
def test_project_all_does_not_run_embedded_bare_repository_fsmonitor(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    clone = embedded_bare_repository_clone(tmp_path, marker)
    out = io.StringIO()

    code = cli.run(
        ["--config", str(clone / "pyproject.toml"), "--engine", "python", "--project", "all"],
        stdout=out,
        stderr=io.StringIO(),
    )

    assert not marker.exists()
    assert code == 0
    assert "core" in out.getvalue()


@posix_git
def test_git_discovery_ignores_repository_fsmonitor(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "module.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "config", "core.fsmonitor", marker_command(marker))

    result = fallback.run_git_ls_files(root)

    assert not marker.exists()
    assert result.returncode == 0
    assert fallback.parse_git_ls_files_output(result.stdout) == [Path("module.py")]


@posix_git
def test_parameters_alone_protect_git_without_config_count(tmp_path: Path) -> None:
    """Git before 2.31 reads only GIT_CONFIG_PARAMETERS; that part must suffice on its own."""

    marker = tmp_path / "executed"
    clone = embedded_bare_repository_clone(tmp_path, marker)
    env = {
        name: value
        for name, value in _git_env.hardened_git_env().items()
        if not name.startswith(("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }

    result = subprocess.run(
        ["git", "-C", str(clone / "evil"), "ls-files", "-z", "--", "."],
        env=env,
        capture_output=True,
        check=False,
    )

    assert not marker.exists()
    assert result.returncode != 0
    assert b"safe.bareRepository" in result.stderr


@posix_git
def test_churn_does_not_run_repository_signature_program(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "module.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "one")
    tree = git(root, "rev-parse", "HEAD^{tree}").strip()
    parent = git(root, "rev-parse", "HEAD").strip()
    raw = (
        f"tree {tree}\nparent {parent}\n"
        "author Test <test@example.com> 1767225600 +0000\n"
        "committer Test <test@example.com> 1767225600 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n \n AAAA\n -----END PGP SIGNATURE-----\n"
        "\nsigned\n"
    )
    raw_path = tmp_path / "commit.txt"
    raw_path.write_text(raw, encoding="utf-8")
    signed = git(root, "hash-object", "-t", "commit", "-w", str(raw_path)).strip()
    git(root, "update-ref", "refs/heads/main", signed)
    program = tmp_path / "gpg-program"
    program.write_text(f"#!/bin/sh\n{marker_command(marker)}\n", encoding="utf-8")
    program.chmod(0o755)
    git(root, "config", "log.showSignature", "true")
    git(root, "config", "gpg.program", str(program))

    churn = composition_churn.collect_churn(
        root, ref="main", months=2, excluded_paths=EXCLUDES, today=date(2026, 1, 20)
    )

    assert not marker.exists()
    assert churn.status == "ok"


@posix_git
def test_churn_keeps_partial_clone_transport_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only use a local transport for fixture setup. The report must override even that allowance.
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_NO_LAZY_FETCH", "0")
    monkeypatch.setenv("GIT_AUTHOR_DATE", "2026-01-15T12:00:00+00:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-01-15T12:00:00+00:00")
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    (source / "module.py").write_text("x = 1\n", encoding="utf-8")
    git(source, "add", "module.py")
    git(source, "commit", "-qm", "one")
    git(source, "config", "uploadpack.allowFilter", "true")
    blob = git(source, "rev-parse", "HEAD:module.py").strip()

    partial = tmp_path / "partial"
    git(
        tmp_path,
        "clone",
        "-q",
        "--no-checkout",
        "--filter=blob:none",
        source.as_uri(),
        str(partial),
    )
    assert (
        f"?{blob}" in git(partial, "rev-list", "--objects", "--missing=print", "HEAD").splitlines()
    )

    marker = tmp_path / "transport-started"
    upload_pack = tmp_path / "upload-pack"
    upload_pack.write_text(f"#!/bin/sh\n{marker_command(marker)}\n", encoding="utf-8")
    upload_pack.chmod(0o755)
    git(partial, "config", "remote.origin.uploadpack", shlex.quote(str(upload_pack)))
    git(partial, "config", "protocol.file.allow", "always")

    def churn(root: Path) -> CompositionChurn:
        return composition_churn.collect_churn(
            root, months=1, excluded_paths=EXCLUDES, today=date(2026, 1, 20)
        )

    complete = churn(source)
    assert complete.status == "ok"
    assert sum(complete.rows[0].added) == 1

    offline = churn(partial)
    assert not marker.exists()
    assert offline.status == "skipped"
    assert offline.reason is not None and offline.reason.startswith("git log failed:")

    # Populate the local object store with the missing blob, keeping the sentinel configured.
    assert git(partial, "hash-object", "-w", str(source / "module.py")).strip() == blob
    assert churn(partial) == complete
    assert not marker.exists()


def test_cloc_runs_with_hardened_git_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_popen(*_args: object, **kwargs: object) -> object:
        seen.update(kwargs)
        raise FileNotFoundError

    monkeypatch.setattr("slopscope.cloc.subprocess.Popen", fake_popen)

    cloc.run_command(["cloc", "."])

    env = seen["env"]
    assert isinstance(env, dict)
    assert env["GIT_CONFIG_KEY_0"] == "safe.bareRepository"
    assert env["GIT_ALLOW_PROTOCOL"] == ""


@posix_git
@pytest.mark.skipif(shutil.which("cloc") is None, reason="cloc is not installed")
def test_cloc_engine_does_not_run_embedded_bare_repository_fsmonitor(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    clone = embedded_bare_repository_clone(tmp_path, marker)

    cli.run(
        ["--config", str(clone / "pyproject.toml"), "--engine", "cloc", "--project", "all"],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert not marker.exists()
