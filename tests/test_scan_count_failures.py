"""Synthetic discovery and successful-but-invalid cloc regression fixtures."""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from slopscope import cli, cloc, fallback


@pytest.mark.parametrize("mode", ["report", "profile", "composition", "seed", "update"])
def test_traversal_failure_preserves_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    blocked = tmp_path / "included"
    blocked.mkdir()
    (blocked / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.profiles]]\nname = "physical"\nphysical_lines = true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(1, b""))
    scandir = os.scandir
    attempts = []

    def fail_scandir(
        path: str | os.PathLike[str],
    ) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        if Path(path) == blocked:
            attempts.append(path)
            raise PermissionError(13, "denied", str(blocked))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", fail_scandir)
    snapshot, allowlist = tmp_path / "snapshot.json", tmp_path / "size-limits.json"
    snapshot.write_text("previous snapshot", encoding="utf-8")
    if mode != "seed":
        allowlist.write_text("{}\n", encoding="utf-8")
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
    assert attempts
    assert out.getvalue() == ""
    assert "could not traverse" in err.getvalue()
    assert "included" in err.getvalue()
    assert snapshot.read_text(encoding="utf-8") == "previous snapshot"
    assert (allowlist.read_bytes() if allowlist.exists() else None) == previous


@pytest.mark.parametrize("profile,stage", [(False, "language"), (False, "file"), (True, "file")])
@pytest.mark.parametrize(
    "bad", ["not-an-int", "", "-1", "9" * 5000, "x" * (csv.field_size_limit() + 1)]
)
def test_invalid_cloc_output_is_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool, stage: str, bad: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.profiles]]\nname = "code"\n', encoding="utf-8"
    )
    header = "language,filename,blank,comment,code\n"
    monkeypatch.setattr(cloc, "is_cloc_available", lambda: True)
    monkeypatch.setattr(
        cloc,
        "run_language_summary",
        lambda _: cloc.ClocResult(
            0, header + "Python,1,0,0," + (bad if stage == "language" else "1") + "\n", ""
        ),
    )
    monkeypatch.setattr(
        cloc,
        "run_file_summary",
        lambda _: cloc.ClocResult(
            0, header + "Python,good.py,0,0,1\nPython,bad.py,0,0," + bad + "\n", ""
        ),
    )
    out, err = io.StringIO(), io.StringIO()
    options = ["--profile", "code", "--total-only"] if profile else []
    assert cli.run([str(tmp_path), "--engine", "cloc", *options], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "invalid cloc CSV" in err.getvalue()
    assert len(err.getvalue()) < 200


@pytest.mark.parametrize("excluded", [".venv", "private", "docs/private"])
def test_excluded_unreadable_directory_is_pruned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excluded: str
) -> None:
    blocked = tmp_path / excluded
    blocked.mkdir(parents=True)
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.slopscope]\nexclude_dirs = ["' + excluded + '"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(fallback, "run_git_ls_files", lambda _: fallback.GitLsFilesResult(1, b""))
    scandir = os.scandir
    scanned: list[Path] = []

    def fail_scandir(
        path: str | os.PathLike[str],
    ) -> AbstractContextManager[Iterator[os.DirEntry[str]]]:
        scanned.append(Path(path))
        if Path(path) == blocked:
            raise PermissionError(13, "denied", str(blocked))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", fail_scandir)
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), "--engine", "python"], stdout=out, stderr=err) == 0
    assert tmp_path in scanned
    assert blocked not in scanned
    assert err.getvalue() == ""
