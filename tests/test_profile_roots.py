"""Exercise the public cloc boundary and grouped profiles with real argv paths."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path

import pytest

from slopscope import cloc, config, profile
from slopscope.report import GroupedProfileReport, GroupedRow


@pytest.mark.parametrize("name", ["--help", "--out=report", "with spaces", "with\nnewline"])
@pytest.mark.parametrize("by_file", [False, True])
def test_cloc_root_is_one_absolute_operand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, by_file: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    Path(name).mkdir()
    executable = tmp_path / "capture-cloc"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "print(json.dumps(sys.argv[1:]))\n"
        "print('synthetic diagnostic', file=sys.stderr)\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    run = cloc.run_file_summary if by_file else cloc.run_language_summary
    result = run(name, executable=str(executable))
    args = json.loads(result.stdout)
    assert args[0] == str(tmp_path / name)
    assert args[1:] == ["--vcs=git", *(["--by-file"] if by_file else []), "--csv", "--quiet"]
    assert result.returncode == 7
    assert result.stderr == "synthetic diagnostic\n"


@pytest.mark.parametrize("real_cloc", [False, True], ids=["csv-fixture", "real-cloc"])
def test_grouped_profile_root_forms_keep_child_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_cloc: bool
) -> None:
    root = tmp_path / "outer" / "repo"
    for index, name in enumerate(("src/pkg", "repo/pkg")):
        directory = root / name
        directory.mkdir(parents=True)
        (directory / "app.py").write_text(f"x = {index}\ny = 2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    if real_cloc:
        if shutil.which("cloc") is None:
            pytest.skip("real cloc executable unavailable")
    else:
        executable = tmp_path / "fixture-cloc"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import csv, pathlib, sys\n"
            "writer = csv.writer(sys.stdout)\n"
            "writer.writerow(['language', 'filename', 'blank', 'comment', 'code'])\n"
            "for path in sorted(pathlib.Path(sys.argv[1]).rglob('*.py')):\n"
            "    writer.writerow(['Python', str(path), 0, 0, 2])\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        monkeypatch.setattr(
            cloc, "run_file_summary", partial(cloc.run_file_summary, executable=str(executable))
        )
    for cwd, operand in ((tmp_path, Path("outer/repo")), (root, Path(".")), (tmp_path, root)):
        monkeypatch.chdir(cwd)
        for pattern, expected in (("src/*", "src/pkg"), ("repo/*", "repo/pkg")):
            selected = config.ProfileConfig(name="grouped", group_by=pattern)
            for engine in ("cloc", "python"):
                report = profile.build_profile_report(
                    path=operand,
                    slopscope_config=config.SlopscopeConfig(),
                    selected_profile=selected,
                    engine=engine,
                )
                assert isinstance(report, GroupedProfileReport)
                assert report.rows == (GroupedRow(name=expected, files=1, code=2),)
                assert report.total == 2
