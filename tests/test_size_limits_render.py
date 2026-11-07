from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from slopscope import size_limits_render as render
from slopscope.render import OutputFormat
from slopscope.report import (
    CompositionFailure,
    SizeLimitEntry,
    SizeLimitSettings,
    SizeLimitsReport,
)


def sample_report() -> SizeLimitsReport:
    return SizeLimitsReport(
        path=Path("repo"),
        action="report",
        settings=SizeLimitSettings(),
        allowlist_path=Path("allow.json"),
        allowlist_existed=True,
        allowlist_entries=(3, 2, 1),
        units=(17, 5, 4),
        over_limit=(),
        grown=(),
        shrunk=(),
        stale=(),
        lowered=(),
        dropped=(),
        seeded=(),
        failures=(),
    )


def human(report: SizeLimitsReport, output_format: OutputFormat) -> list[str]:
    if output_format == "rich":
        pytest.importorskip("rich")
    output = render.render_size_limits_report(report, output_format=output_format)
    if output_format == "rich":
        assert "\x1b[" in output  # Exercise Rich itself, not its plain fallback.
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return [
        " ".join(line.replace("│", " ").replace("┃", " ").split()) for line in output.splitlines()
    ]


@pytest.mark.parametrize("label", ["[red]x[/red].py", "x[/red].py", "[x]雪.py", ":warning:.py"])
def test_rich_size_and_failure_labels_are_literal(label: str) -> None:
    report = replace(
        sample_report(),
        over_limit=(SizeLimitEntry("functions", f"{label}::run", 171, 150),),
        failures=(CompositionFailure(label, f"bad {label}"),),
    )
    lines = human(report, "rich")
    assert f"function 171 150 {label}::run" in lines
    assert f"{label} bad {label}" in lines


def state_report(action: str) -> SizeLimitsReport:
    return replace(
        sample_report(),
        action=action,
        over_limit=(SizeLimitEntry("functions", "new.py::f", 171, 150),),
        grown=(SizeLimitEntry("classes", "grown.py::C", 1703, 1500, 1601),),
        shrunk=(SizeLimitEntry("test_files", "test_shrunk.py", 3107, 3000, 3309),),
        stale=(
            SizeLimitEntry("functions", "gone.py::f", None, 150, 199, "missing"),
            SizeLimitEntry("classes", "small.py::C", 0, 1500, 0, "within limit"),
        ),
        seeded=(SizeLimitEntry("functions", "seed.py::f", 183, 150),) if action == "seed" else (),
        lowered=(SizeLimitEntry("classes", "lower.py::C", 1521, 1500, 1643),)
        if action == "update"
        else (),
        dropped=(SizeLimitEntry("test_files", "test_gone.py", None, 3000, 3456, "missing"),)
        if action == "update"
        else (),
        failures=(CompositionFailure("bad.py", "invalid syntax"),),
    )


@pytest.mark.parametrize("action", ["report", "seed", "update"])
def test_json_state_matrix(action: str) -> None:
    data = json.loads(render.render_size_limits_report(state_report(action), output_format="json"))
    assert data["action"] == action
    assert data["limits"] == {"functions": 150, "classes": 1500, "test_files": 3000}
    assert data["units"] == {"functions": 17, "classes": 5, "test_files": 4}
    assert data["allowlist"] == {
        "path": "allow.json",
        "existed": True,
        "entries": {"functions": 3, "classes": 2, "test_files": 1},
    }
    assert data["check_failed"] is True
    # Literal expectations deliberately do not call the renderer's row/entry helpers.
    expected: dict[str, list[tuple[object, ...]]] = {
        "over_limit": [("functions", "new.py::f", 171, 150, None, None, None)],
        "grown": [("classes", "grown.py::C", 1703, 1500, 1601, 102, None)],
        "shrunk": [("test_files", "test_shrunk.py", 3107, 3000, 3309, -202, None)],
        "stale": [
            ("functions", "gone.py::f", None, 150, 199, None, "missing"),
            ("classes", "small.py::C", 0, 1500, 0, 0, "within limit"),
        ],
        "seeded": [("functions", "seed.py::f", 183, 150, None, None, None)]
        if action == "seed"
        else [],
        "lowered": [("classes", "lower.py::C", 1521, 1500, 1643, -122, None)]
        if action == "update"
        else [],
        "dropped": [("test_files", "test_gone.py", None, 3000, 3456, None, "missing")]
        if action == "update"
        else [],
    }
    fields = ("kind", "key", "size", "limit", "recorded", "delta", "reason")
    for state, rows in expected.items():
        assert data[state] == [dict(zip(fields, row, strict=True)) for row in rows]
    assert data["failures"] == [{"path": "bad.py", "error": "invalid syntax"}]


@pytest.mark.parametrize("output_format", ["plain", "rich"])
@pytest.mark.parametrize("action", ["report", "seed", "update"])
def test_human_state_matrix(action: str, output_format: OutputFormat) -> None:
    lines = human(state_report(action), output_format)
    for title, heading, row in (
        ("New Over Limit (not allowlisted)", "Kind Size Limit Unit", "function 171 150 new.py::f"),
        (
            "Grown Past Allowlist",
            "Kind Recorded Size Delta Unit",
            "class 1601 1703 +102 grown.py::C",
        ),
        (
            "Shrunk Below Allowlist",
            "Kind Recorded Size Delta Unit",
            "test file 3309 3107 -202 test_shrunk.py",
        ),
        (
            "Stale Allowlist Entries",
            "Kind Recorded Size Reason Unit",
            "function 199 - missing gone.py::f",
        ),
    ):
        assert title in lines
        start = lines.index(title)
        section = lines[start + 1 :]
        section = section[: section.index("")]
        assert heading in section
        assert row in section
    assert "class 0 0 within limit small.py::C" in lines
    assert "Failures (units in these files were not measured)" in " ".join(lines)
    assert "1 new over limit, 1 grown, 1 shrunk, 2 stale" in lines
    if output_format == "rich":
        assert "bad.py invalid syntax" in lines
    else:
        assert "bad.py: invalid syntax" in lines
    if action == "seed":
        assert "Seeded Allowlist Entries" in lines
        assert "function 183 150 seed.py::f" in lines
    else:
        assert "Seeded Allowlist Entries" not in lines
    if action == "update":
        assert "Lowered Allowlist Entries" in lines
        assert "Kind Was Now Unit" in lines
        assert "class 1643 1521 lower.py::C" in lines
        assert "Dropped Allowlist Entries" in lines
        assert "Kind Was Reason Unit" in lines
        assert "test file 3456 missing test_gone.py" in lines
    else:
        assert "Lowered Allowlist Entries" not in lines
        assert "Dropped Allowlist Entries" not in lines


@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_failure_only_report(output_format: OutputFormat) -> None:
    report = replace(sample_report(), failures=(CompositionFailure("bad.py", "invalid syntax"),))
    lines = human(report, output_format)
    assert "0 new over limit, 0 grown, 0 shrunk, 0 stale" in lines
    assert "Failures (units in these files were not measured)" in " ".join(lines)
    data = json.loads(render.render_size_limits_json(report))
    expected_failure = (
        "bad.py invalid syntax" if output_format == "rich" else "bad.py: invalid syntax"
    )
    assert expected_failure in lines
    assert data["check_failed"] is False
    assert data["failures"] == [{"path": "bad.py", "error": "invalid syntax"}]
    assert data["over_limit"] == data["grown"] == data["shrunk"] == data["stale"] == []


@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_known_zero_delta_is_signed(output_format: OutputFormat) -> None:
    # Exercise the presentation boundary even when a caller supplies an unchanged entry.
    report = replace(
        sample_report(), grown=(SizeLimitEntry("functions", "same.py::f", 171, 150, 171),)
    )
    assert "function 171 171 +0 same.py::f" in human(report, output_format)
