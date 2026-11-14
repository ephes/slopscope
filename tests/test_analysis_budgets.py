"""Bounded physical reads and cumulative composition state, using synthetic sources."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from slopscope import cli, composition, composition_duplication, fallback


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 65536])
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\xff",
        b"a",
        b"a\r\nb\rc\nlast",
        b"\r\n\n\r",
        b"\xe2\x82\xac\xff\r\nx\r\xff\n",
        b"x" * 200_000,
    ],
)
def test_physical_reader_matches_utf8_universal_newlines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes, chunk: int
) -> None:
    source = tmp_path / "app.py"
    source.write_bytes(raw)
    monkeypatch.setattr(fallback, "PHYSICAL_READ_CHUNK", chunk, raising=False)
    with io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", errors="ignore") as handle:
        expected = sum(1 for _ in handle)
    assert fallback.count_physical_lines(source) == expected


def test_physical_reader_bounds_reads_even_for_growing_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[int] = []

    class Growing(io.BytesIO):
        def __iter__(self) -> Any:
            pytest.fail("physical reader must not allocate whole physical lines")

        def read(self, size: int | None = -1) -> bytes:
            assert size is not None
            requests.append(size)
            assert 0 < size <= 65536
            return b"x" * size  # Models continued growth, independent of initial file metadata.

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: Growing())
    monkeypatch.setattr(fallback, "MAX_PHYSICAL_SOURCE_BYTES", 100_000, raising=False)
    with pytest.raises(fallback.CountingError, match="source.*byte limit"):
        fallback.count_physical_lines("growing.py")
    assert sum(requests) == 100_001


@pytest.mark.parametrize("profile", [False, True])
@pytest.mark.parametrize("ceiling", ["MAX_PHYSICAL_SOURCE_BYTES", "MAX_PHYSICAL_TOTAL_BYTES"])
def test_physical_overflow_emits_no_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: bool, ceiling: str
) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_bytes(b"x = 1\n")
    (tmp_path / "pyproject.toml").write_text(
        '[[tool.slopscope.profiles]]\nname = "physical"\nphysical_lines = true\n', encoding="utf-8"
    )
    monkeypatch.setattr(
        fallback, ceiling, 10 if ceiling.endswith("TOTAL_BYTES") else 5, raising=False
    )
    options = ["--profile", "physical", "--total-only"] if profile else ["--engine", "python"]
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), *options], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "byte limit" in err.getvalue()


@pytest.mark.parametrize(
    "setting,limit",
    [("max_total_source_bytes", 25), ("max_total_tokens", 7), ("max_total_anchors", 2)],
)
def test_composition_cumulative_limit_preserves_snapshot_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str, limit: int
) -> None:
    for index in range(5):
        (tmp_path / f"{index}.py").write_text(f"unique_{index} = {index}\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.slopscope.composition]\n{setting} = {limit}\n", encoding="utf-8"
    )
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b"previous snapshot")
    analyzed: list[str] = []
    retained: list[tuple[int, int]] = []
    original = composition.analyze_source
    tokenize_segments = composition_duplication.token_segments

    def record_source(source: str, **kwargs: Any) -> Any:
        analyzed.append(kwargs["path"])
        return original(source, **kwargs)

    def record_segments(*args: Any, **kwargs: Any) -> Any:
        result = tokenize_segments(*args, **kwargs)
        retained.append((len(args[2]), sum(len(segment.ids) for segment in result)))
        return result

    monkeypatch.setattr(composition, "analyze_source", record_source)
    monkeypatch.setattr(composition_duplication, "token_segments", record_segments)
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.run(
            [str(tmp_path), "--composition", "--snapshot", str(snapshot)], stdout=out, stderr=err
        )
        == 2
    )
    assert "whole-composition" in err.getvalue()
    assert out.getvalue() == ""
    assert snapshot.read_bytes() == b"previous snapshot"
    assert len(analyzed) < 5
    assert sum(tokens for _, tokens in retained) <= (limit if setting == "max_total_tokens" else 6)
    assert all(unique <= 6 for unique, _ in retained)


def test_composition_exact_cumulative_boundaries(tmp_path: Path) -> None:
    source = b"x = 1\n"
    (tmp_path / "app.py").write_bytes(source)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.slopscope.composition]\nmax_total_source_bytes = 6\n"
        "max_total_tokens = 3\nmax_total_anchors = 1\n",
        encoding="utf-8",
    )
    out, err = io.StringIO(), io.StringIO()
    assert (
        cli.run([str(tmp_path), "--composition", "--format", "json"], stdout=out, stderr=err) == 0
    )
    report = json.loads(out.getvalue())
    assert report["settings"]["max_total_tokens"] == 3
    assert not report["failures"]


@pytest.mark.parametrize(
    "constant,limit",
    [
        ("DEFAULT_MAX_TOTAL_SOURCE_BYTES", 25),
        ("DEFAULT_MAX_TOTAL_TOKENS", 7),
        ("DEFAULT_MAX_TOTAL_ANCHORS", 2),
    ],
)
def test_whole_composition_refuses_before_retaining_all_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constant: str, limit: int
) -> None:
    for index in range(5):
        (tmp_path / f"{index}.py").write_text(f"unique_{index} = {index}\n", encoding="utf-8")
    monkeypatch.setattr(composition, constant, limit, raising=False)
    with pytest.raises(fallback.CountingError, match="whole-composition"):
        composition.build_composition_report(tmp_path, excluded_paths=())


@pytest.mark.parametrize("field", ["tokens", "anchors"])
def test_overbudget_file_does_not_mutate_interner(field: str) -> None:
    interner = {"sentinel": 0}
    budget = composition.AnalysisBudget(
        1000,
        composition_duplication.TokenBudget(
            tokens=2 if field == "tokens" else 100,
            anchors=0 if field == "anchors" else 100,
        ),
    )
    with pytest.raises(composition_duplication.BudgetExceeded, match=field[:-1]):
        composition.analyze_source("new_name = 123\n", interner=interner, budget=budget)
    assert interner == {"sentinel": 0}
    assert budget.tokens.tokens == (2 if field == "tokens" else 100)
    assert budget.tokens.anchors == (0 if field == "anchors" else 100)


def test_exact_token_anchor_budget_preserves_duplicate_detection(tmp_path: Path) -> None:
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_bytes(b"x = 1\n")
    report = composition.build_composition_report(
        tmp_path,
        excluded_paths=(),
        max_total_source_bytes=12,
        max_total_tokens=6,
        max_total_anchors=2,
        min_duplicate_tokens=3,
    )
    assert not report.failures
    assert len(report.duplicates) == 1
    assert report.total.counts.duplicated_lines == 2


@pytest.mark.parametrize(
    "setting", ["max_total_source_bytes", "max_total_tokens", "max_total_anchors"]
)
@pytest.mark.parametrize("value", [0, -1, True, "3", 1.5])
def test_cumulative_config_limits_are_positive_integers(setting: str, value: Any) -> None:
    from slopscope import config

    with pytest.raises(config.ConfigError, match=f"{setting} must be a positive integer"):
        config.parse_config_mapping({"composition": {setting: value}})


def test_physical_exact_source_and_total_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_bytes(b"x = 1\n")
    monkeypatch.setattr(fallback, "MAX_PHYSICAL_SOURCE_BYTES", 6)
    monkeypatch.setattr(fallback, "MAX_PHYSICAL_TOTAL_BYTES", 12)
    assert [row.code for row in fallback.build_file_rows(tmp_path)] == [1, 1]


def test_partial_failed_read_still_consumes_composition_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from slopscope import _source

    source = tmp_path / "app.py"
    source.write_bytes(b"x = 1\n")
    opened = _source.open_source

    # Keep descriptor admission, but fail after a successful bounded read.
    from collections.abc import Iterator
    from contextlib import contextmanager
    from typing import BinaryIO

    @contextmanager
    def fail_read(*args: Any, **kwargs: Any) -> Iterator[BinaryIO]:
        with opened(*args, **kwargs) as handle:
            original_read = handle.read
            calls = 0

            def read(size: int) -> bytes:
                nonlocal calls
                calls += 1
                if calls > 1:
                    raise OSError("synthetic failed read")
                return original_read(size)

            monkeypatch.setattr(handle, "read", read)
            yield handle

    monkeypatch.setattr(_source, "open_source", fail_read)
    budget = composition.AnalysisBudget(12, composition_duplication.TokenBudget(10, 10))
    with pytest.raises(composition.CompositionAnalysisError, match="I/O error"):
        composition.analyze_file(tmp_path, "app.py", budget=budget)
    assert budget.source_bytes == 6


@pytest.mark.parametrize("total", [False, True])
def test_physical_limit_diagnostic_escapes_and_bounds_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, total: bool
) -> None:
    path = tmp_path / "d" / ("evil\n\x1b[31m" + "n" * 230 + ".py")
    path.parent.mkdir()
    path.write_bytes(b"x = 1\n")
    monkeypatch.setattr(fallback, "MAX_PHYSICAL_SOURCE_BYTES", 100 if total else 3)
    budget = fallback.PhysicalByteBudget(remaining=3) if total else None
    with pytest.raises(fallback.CountingError) as error:
        fallback.count_physical_lines(path, budget=budget)
    message = str(error.value)
    assert "\n" not in message and "\x1b" not in message
    assert "\\n" in message and len(message) < 320
