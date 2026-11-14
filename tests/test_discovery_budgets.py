"""Unique working-tree inputs and finite filesystem discovery."""

from __future__ import annotations

import io
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from slopscope import cli, composition, fallback, size_limits


def test_git_paths_keep_exact_identity_and_first_order() -> None:
    names = ["b.py", "A.py", "a.py", "é.py", "e\u0301.py", "b.py", "A.py"]
    raw = b"\0".join(os.fsencode(name) for name in names) + b"\0"
    assert fallback.parse_git_ls_files_output(raw) == [Path(name) for name in names[:5]]


def test_real_unmerged_index_counts_working_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(["git", "-C", str(tmp_path), *args], capture_output=True, check=check)

    git("init", "-b", "main")
    git("config", "user.name", "Synthetic Test")
    git("config", "user.email", "synthetic@example.invalid")
    source = tmp_path / "app.py"
    source.write_text("value = 0\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Base")
    git("switch", "-c", "other")
    source.write_text("value = 1\n", encoding="utf-8")
    git("commit", "-am", "Other")
    git("switch", "main")
    source.write_text("value = 2\n", encoding="utf-8")
    git("commit", "-am", "Main")
    assert git("merge", "other", check=False).returncode == 1
    assert len(git("ls-files", "-u").stdout.splitlines()) == 3
    # Working bytes can be parseable before the conflict is staged as resolved.
    source.write_text("value = 3\nprint(value)\n", encoding="utf-8")
    rows = fallback.build_file_rows(tmp_path)
    assert [(row.path, row.code) for row in rows] == [("app.py", 2)]
    report = composition.build_composition_report(tmp_path, excluded_paths=())
    assert report.total.files == 1
    assert report.total.counts.physical == 2
    analyzed: list[str] = []
    original = composition.analyze_file

    def record(root: Path, path: str, **kwargs: Any) -> Any:
        analyzed.append(path)
        return original(root, path, **kwargs)

    monkeypatch.setattr(composition, "analyze_file", record)
    _, failures = size_limits.measure(tmp_path, excluded_paths=())
    assert not failures
    assert analyzed == ["app.py"]
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.run([str(tmp_path), "--engine", "python", "--format", "json"], stdout=out, stderr=err)
        == 0
    )
    assert json.loads(out.getvalue())["language_rows"][0]["files"] == 1


@pytest.mark.parametrize(
    "budget",
    [
        "MAX_DISCOVERY_FILES",
        "MAX_DISCOVERY_DIRECTORIES",
        "MAX_DISCOVERY_DEPTH",
        "MAX_DISCOVERY_ENTRIES",
    ],
)
def test_filesystem_limits_preserve_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: str
) -> None:
    parent = tmp_path
    for index in range(4):
        parent = parent / f"d{index}"
        parent.mkdir()
        (parent / "app.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(fallback, budget, 2, raising=False)
    monkeypatch.setattr(fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(1, b""))
    snapshot, allowlist = tmp_path / "snapshot.json", tmp_path / "size-limits.json"
    snapshot.write_bytes(b"previous snapshot")
    allowlist.write_bytes(b"{}\n")
    for options in (
        ["--engine", "python"],
        ["--composition", "--snapshot", str(snapshot)],
        ["--size-limits", "--update-allowlist"],
    ):
        out, err = io.StringIO(), io.StringIO()
        assert cli.run([str(tmp_path), *options], stdout=out, stderr=err) == 2
        assert out.getvalue() == ""
        assert "discovery" in err.getvalue() and "limit" in err.getvalue()
        assert snapshot.read_bytes() == b"previous snapshot"
        assert allowlist.read_bytes() == b"{}\n"


def test_broad_directory_stops_consuming_lazy_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "app.py").touch()
    scandir = os.scandir
    consumed = 0

    @contextmanager
    def broad(path: Any) -> Iterator[Iterator[os.DirEntry[str]]]:
        def entries(entry: os.DirEntry[str]) -> Iterator[os.DirEntry[str]]:
            nonlocal consumed
            for _ in range(100):
                consumed += 1
                yield entry

        with scandir(path) as handle:
            yield entries(next(handle))

    monkeypatch.setattr(fallback, "MAX_DISCOVERY_ENTRIES", 3, raising=False)
    monkeypatch.setattr(os, "scandir", broad)
    with pytest.raises(fallback.CountingError, match="discovery.*limit"):
        fallback.discover_filesystem_files(tmp_path)
    assert consumed == 4
