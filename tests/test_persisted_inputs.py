"""Strict persisted JSON and snapshot destination identity regressions."""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from slopscope import cli, composition, composition_baseline, size_limits


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize(
    "text",
    [
        '{"functions": {}, "functions": {"m.py::f": 200}}',
        '{"functions": {"m.py::f": 100, "m.py::f": 200}}',
        '{"classes": {"m.py::C": 100, "m.py::C": 200}}',
        '{"test_files": {"test_m.py": 100, "test_m.py": 200}}',
        '{"functions": {}, "functions": {}}',
    ],
)
def test_allowlist_duplicate_keys_refused_before_update(tmp_path: Path, text: str) -> None:
    allowlist = tmp_path / "size-limits.json"
    allowlist.write_text(text, encoding="utf-8")
    with pytest.raises(size_limits.SizeLimitsError, match="duplicate JSON object key"):
        size_limits.load_allowlist(allowlist)
    code, out, err = run([str(tmp_path), "--size-limits", "--update-allowlist"])
    assert code == 2
    assert out == ""
    assert "duplicate JSON object key" in err
    assert allowlist.read_text(encoding="utf-8") == text


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("key", ["schema_version", "total", "physical", "name"])
def test_baseline_duplicate_keys_refused_and_snapshot_preserved(
    tmp_path: Path, multi: bool, key: str
) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    options = [str(tmp_path), "--composition"]
    if multi:
        (tmp_path / "pyproject.toml").write_text(
            '[[tool.slopscope.projects]]\nname = "équipe"\npath = "."\n', encoding="utf-8"
        )
        options += ["--project", "all"]
    baseline, snapshot = tmp_path / "baseline.json", tmp_path / "snapshot.json"
    assert run([*options, "--snapshot", str(baseline)])[0] == 0
    data = json.loads(baseline.read_text(encoding="utf-8"))
    # The valid original value comes last, so ordinary json.loads would accept it unchanged.
    text = re.sub(f'"{key}":', f'"{key}": null, "{key}":', json.dumps(data), count=1)
    baseline.write_text(text, encoding="utf-8")
    with pytest.raises(composition_baseline.BaselineError, match="duplicate JSON object key"):
        composition_baseline.load_baseline(
            baseline,
            expected_report_type="composition_projects" if multi else "composition",
            schema_version=composition.SCHEMA_VERSION,
        )
    snapshot.write_bytes(b"old snapshot")
    code, out, err = run([*options, "--baseline", str(baseline), "--snapshot", str(snapshot)])
    assert code == 2
    assert out == ""
    assert "duplicate JSON object key" in err
    assert baseline.read_text(encoding="utf-8") == text
    assert snapshot.read_bytes() == b"old snapshot"


@pytest.mark.parametrize("stage", ["analysis", "flush"])
@pytest.mark.parametrize("protected_name", ["m.py", "pyproject.toml", "baseline.json"])
def test_snapshot_symlink_retarget_after_preflight_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, protected_name: str
) -> None:
    source, config = tmp_path / "m.py", tmp_path / "pyproject.toml"
    source.write_text("x = 1\n", encoding="utf-8")
    config.write_text("[tool.slopscope]\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    assert run([str(tmp_path), "--composition", "--snapshot", str(baseline)])[0] == 0
    protected = tmp_path / protected_name
    previous = protected.read_bytes()
    original_target = tmp_path / "target.json"
    original_target.write_bytes(b"old snapshot")
    snapshot = tmp_path / "snapshot.json"
    snapshot.symlink_to(original_target)
    calls = 0

    def retarget() -> None:
        nonlocal calls
        calls += 1
        snapshot.unlink()
        snapshot.symlink_to(protected)

    original_build = composition.build_composition_report
    original_fsync = os.fsync

    def build(*args: Any, **kwargs: Any) -> Any:
        report = original_build(*args, **kwargs)
        retarget()
        return report

    def flush(fd: int) -> None:
        original_fsync(fd)
        retarget()

    if stage == "analysis":
        monkeypatch.setattr(composition, "build_composition_report", build)
    else:
        monkeypatch.setattr(os, "fsync", flush)
    code, out, err = run(
        [str(tmp_path), "--composition", "--baseline", str(baseline), "--snapshot", str(snapshot)]
    )
    assert calls == 1
    assert code == 2
    assert out == ""
    assert "snapshot target changed" in err
    assert protected.read_bytes() == previous
    assert original_target.read_bytes() == b"old snapshot"
    assert snapshot.is_symlink()
    assert list(tmp_path.glob(".target.json.*")) == []


@pytest.mark.parametrize("replace_parent", [False, True])
def test_snapshot_destination_identity_change_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_parent: bool
) -> None:
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    directory = tmp_path / "output"
    directory.mkdir()
    snapshot = directory / "snapshot.json"
    snapshot.write_bytes(b"old snapshot")
    build = composition.build_composition_report
    calls = 0

    def change(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        report = build(*args, **kwargs)
        calls += 1
        if replace_parent:
            directory.rename(tmp_path / "previous")
            directory.mkdir()
        else:
            snapshot.rename(tmp_path / "previous.json")
        snapshot.write_bytes(b"concurrent output")
        return report

    monkeypatch.setattr(composition, "build_composition_report", change)
    code, out, err = run([str(tmp_path), "--composition", "--snapshot", str(snapshot)])
    assert calls == 1
    assert code == 2
    assert out == ""
    assert "snapshot target changed" in err
    assert snapshot.read_bytes() == b"concurrent output"
    previous = (
        tmp_path / "previous" / "snapshot.json" if replace_parent else tmp_path / "previous.json"
    )
    assert previous.read_bytes() == b"old snapshot"


def test_allowlist_unicode_names_remain_valid(tmp_path: Path) -> None:
    path = tmp_path / "size-limits.json"
    path.write_text('{"functions": {"café.py::élève": 200}}', encoding="utf-8")
    assert size_limits.load_allowlist(path) == {
        "functions": {"café.py::élève": 200},
        "classes": {},
        "test_files": {},
    }


def test_duplicate_key_diagnostic_is_bounded_and_escaped(tmp_path: Path) -> None:
    path = tmp_path / "size-limits.json"
    key = "bad\n" * 10000
    field = json.dumps(key)
    path.write_text('{"functions": {' + field + ": 200," + field + ": 200}}", encoding="utf-8")
    with pytest.raises(size_limits.SizeLimitsError, match="duplicate JSON object key") as caught:
        size_limits.load_allowlist(path)
    message = str(caught.value)
    assert "\n" not in message
    assert len(message) < len(str(path)) + 400
