"""Writers admit the exact UTF-8 payload accepted by the corresponding reader."""

from __future__ import annotations

import io
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from slopscope import cli, composition, composition_baseline, config, size_limits


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("overflow", [False, True])
@pytest.mark.parametrize("new", [False, True])
def test_allowlist_capacity_exact_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overflow: bool, new: bool
) -> None:
    target = tmp_path / "output" / "limits.json"
    if not new:
        target.parent.mkdir()
        target.write_bytes(b"{}\n")
    data = {
        "functions": {"src/🧪.py::f": 200, "src/café\n.py::f": 190},
        "classes": {},
        "test_files": {},
    }
    encoded = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    monkeypatch.setattr(size_limits, "MAX_ALLOWLIST_BYTES", len(encoded) - int(overflow))
    if overflow:
        with pytest.raises(size_limits.SizeLimitsError, match="exceeds"):
            size_limits.write_allowlist(target, data)
        if new:
            assert not target.parent.exists()
        else:
            assert target.read_bytes() == b"{}\n"
    else:
        size_limits.write_allowlist(target, data)
        assert target.read_bytes() == encoded
        assert size_limits.load_allowlist(target) == data


def test_all_allowlist_payloads_are_admitted_before_any_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    for target in (a, b):
        target.write_bytes(b"{}\n")
    small: size_limits.Units = {"functions": {}, "classes": {}, "test_files": {}}
    large = {"functions": {"src/app.py::" + "x" * 100: 200}}
    monkeypatch.setattr(size_limits, "MAX_ALLOWLIST_BYTES", 80)

    def unexpected(*args: object, **kwargs: object) -> Path:
        pytest.fail("oversized batch reached disk staging")

    with size_limits.allowlist_locks([a, b], action=size_limits.UPDATE) as destinations:
        monkeypatch.setattr(size_limits, "_stage_bytes", unexpected)
        with pytest.raises(size_limits.SizeLimitsError, match="exceeds"):
            size_limits.publish_allowlists([(a, small), (b, large)], destinations=destinations)
    for target in (a, b):
        assert target.read_bytes() == b"{}\n"
        assert list(tmp_path.glob(f".{target.name}.*")) == [tmp_path / f".{target.name}.lock"]


@pytest.mark.parametrize("staging_limit", [False, True])
def test_wide_allowlist_stops_encoding_at_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, staging_limit: bool
) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    prior = b"{}\n"
    for target in (a, b):
        target.write_bytes(prior)
    small: size_limits.Units = {"classes": {}, "functions": {}, "test_files": {}}
    wide = {"functions": {f"src/{'x' * 100}.py::f{i}": 200 for i in range(4000)}}
    if staging_limit:
        first_size = len(json.dumps(small, indent=2, sort_keys=True)) + 1 + len(prior)
        monkeypatch.setattr(size_limits, "MAX_ALLOWLIST_STAGING_BYTES", first_size + 128)
    else:
        monkeypatch.setattr(size_limits, "MAX_ALLOWLIST_BYTES", 128)

    encoded_chars = 0
    iterencode = json.JSONEncoder.iterencode

    def count_fragments(
        encoder: json.JSONEncoder, obj: object, _one_shot: bool = False
    ) -> Iterator[str]:
        nonlocal encoded_chars
        for fragment in iterencode(encoder, obj, _one_shot):
            encoded_chars += len(fragment)
            yield fragment

    def unexpected_stage(*args: object, **kwargs: object) -> Path:
        pytest.fail("oversized batch reached disk staging")

    match = "publication staging exceeds" if staging_limit else "allowlist .* exceeds 128 bytes"
    with size_limits.allowlist_locks([a, b], action=size_limits.UPDATE) as destinations:
        monkeypatch.setattr(json.JSONEncoder, "iterencode", count_fragments)
        monkeypatch.setattr(size_limits, "_stage_bytes", unexpected_stage)
        with pytest.raises(size_limits.SizeLimitsError, match=match):
            size_limits.publish_allowlists([(a, small), (b, wide)], destinations=destinations)

    assert encoded_chars < 256
    assert a.read_bytes() == b.read_bytes() == prior
    assert set(tmp_path.iterdir()) == {a, b, tmp_path / ".a.json.lock", tmp_path / ".b.json.lock"}


@pytest.mark.parametrize("overflow", [False, True])
@pytest.mark.parametrize("new", [False, True])
def test_snapshot_utf8_capacity_exact_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overflow: bool, new: bool
) -> None:
    (tmp_path / "café.py").write_text("x = 1\n", encoding="utf-8")
    code, output, error = run([str(tmp_path), "--composition", "--format", "json"])
    assert (code, error) == (0, "")
    text = json.dumps(json.loads(output), indent=2, ensure_ascii=False) + "\n"
    content = text.encode("utf-8")
    assert len(content) > len(text)
    target = tmp_path / "snapshot.json"
    if not new:
        target.write_bytes(b"old snapshot")
    destination = cli._validate_snapshot_inputs(target, [], config.SlopscopeConfig(), None, None)
    monkeypatch.setattr(composition_baseline, "MAX_BASELINE_BYTES", len(content) - int(overflow))
    if overflow:

        def unexpected(*args: object, **kwargs: object) -> None:
            pytest.fail("oversized snapshot reached disk staging")

        monkeypatch.setattr(tempfile, "NamedTemporaryFile", unexpected)
        with pytest.raises(OSError, match="exceeds"):
            cli._write_snapshot(target, text, destination=destination)
        if new:
            assert not target.exists()
        else:
            assert target.read_bytes() == b"old snapshot"
    else:
        cli._write_snapshot(target, text, destination=destination)
        assert target.read_bytes() == content
        assert composition_baseline.load_baseline(
            target, expected_report_type="composition", schema_version=composition.SCHEMA_VERSION
        ) == json.loads(text)


@pytest.mark.parametrize("multi", [False, True])
def test_cli_snapshot_refusal_preserves_previous_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, multi: bool
) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    options = [str(tmp_path), "--composition", "--format", "json"]
    if multi:
        (tmp_path / "pyproject.toml").write_text(
            '[[tool.slopscope.projects]]\nname = "a"\npath = "."\n'
            '[[tool.slopscope.projects]]\nname = "b"\npath = "."\n',
            encoding="utf-8",
        )
        options += ["--project", "all"]
    code, output, error = run(options)
    assert (code, error) == (0, "")
    target = tmp_path / "snapshot.json"
    target.write_bytes(b"old snapshot")
    monkeypatch.setattr(composition_baseline, "MAX_BASELINE_BYTES", len(output.encode("utf-8")) - 1)
    code, output, error = run([*options, "--snapshot", str(target)])
    assert code == 2
    assert output == ""
    assert "exceeds" in error
    assert target.read_bytes() == b"old snapshot"
    assert list(tmp_path.glob(".snapshot.json.*")) == []


def test_cli_multi_project_seed_refuses_whole_oversized_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopscope.size_limits]\nmax_function_lines = 1\n"
        '[[tool.slopscope.projects]]\nname = "a"\npath = "a"\n'
        '[[tool.slopscope.projects]]\nname = "b"\npath = "b"\n',
        encoding="utf-8",
    )
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        filename = "app.py" if name == "a" else "long" * 20 + ".py"
        (root / filename).write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(size_limits, "MAX_ALLOWLIST_BYTES", 100)
    code, output, error = run(
        [str(tmp_path), "--size-limits", "--seed-allowlist", "--project", "all"]
    )
    assert code == 2
    assert output == ""
    assert "exceeds" in error
    for name in ("a", "b"):
        assert not (tmp_path / name / "size-limits.json").exists()
        assert list((tmp_path / name).glob("*.pending")) == []


@pytest.mark.parametrize("overflow", [False, True])
def test_cli_multi_project_update_budgets_outputs_and_recovery_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overflow: bool
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.projects]]\nname = "a"\npath = "a"\n'
        '[[tool.slopscope.projects]]\nname = "b"\npath = "b"\n',
        encoding="utf-8",
    )
    prior = b'{"functions": {"missing.py::f": 200}}\n'
    data: size_limits.Units = {"functions": {}, "classes": {}, "test_files": {}}
    candidate = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    targets = [tmp_path / name / "size-limits.json" for name in ("a", "b")]
    for target in targets:
        target.parent.mkdir()
        target.write_bytes(prior)

    monkeypatch.setattr(size_limits, "MAX_ALLOWLIST_BYTES", max(len(prior), len(candidate)))
    total = len(targets) * (len(prior) + len(candidate))
    monkeypatch.setattr(
        size_limits, "MAX_ALLOWLIST_STAGING_BYTES", total - int(overflow), raising=False
    )
    staged: list[Path] = []
    stage_bytes = size_limits._stage_bytes

    def record_stage(target: Path, *args: Any, **kwargs: Any) -> size_limits._StagedAllowlist:
        staged.append(target)
        return stage_bytes(target, *args, **kwargs)

    monkeypatch.setattr(size_limits, "_stage_bytes", record_stage)
    code, output, error = run(
        [str(tmp_path), "--size-limits", "--update-allowlist", "--project", "all"]
    )
    if overflow:
        assert code == 2
        assert output == ""
        assert f"staging exceeds {total - 1} bytes" in error
        assert staged == []  # the budget is admitted before any output or backup is staged
    else:
        assert (code, error) == (0, "")
        assert staged
    for target in targets:
        assert target.read_bytes() == (prior if overflow else candidate)
        assert list(target.parent.glob(f".{target.name}.*")) == [
            target.with_name(f".{target.name}.lock")
        ]
