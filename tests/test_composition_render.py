from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from slopscope import composition, composition_baseline
from slopscope import composition_render as render
from slopscope.render import OutputFormat
from slopscope.report import (
    CompositionChurn,
    CompositionChurnMonth,
    CompositionClassRow,
    CompositionDuplicateBlock,
    CompositionDuplicateOccurrence,
    CompositionFailure,
    CompositionFileRow,
    CompositionFunctionRow,
    CompositionReport,
)


@pytest.fixture
def report(tmp_path: Path) -> CompositionReport:
    # The directory is empty; all renderer inputs below are synthetic report data.
    return replace(
        composition.build_composition_report(tmp_path, excluded_paths=()), path=Path("repo")
    )


def human(report: CompositionReport, output_format: OutputFormat) -> list[str]:
    if output_format == "rich":
        pytest.importorskip("rich")
    output = render.render_composition_report(report, output_format=output_format)
    if output_format == "rich":
        assert "\x1b[" in output
    output = re.sub(r"\x1b\[[0-9;]*m", "", output)
    return [
        " ".join(line.replace("│", " ").replace("┃", " ").split()) for line in output.splitlines()
    ]


@pytest.mark.parametrize("label", ["[red]x[/red].py", "x[/red].py", "[x]雪.py", ":warning:.py"])
def test_rich_composition_labels_are_literal(report: CompositionReport, label: str) -> None:
    report = replace(
        report,
        largest_modules=(CompositionFileRow(label, "source", "src", report.total.counts),),
        largest_classes=(CompositionClassRow(label, "C", 2, 8, 1, "source"),),
        largest_functions=(CompositionFunctionRow(label, "f", 3, 7, "source"),),
        duplicates=(
            CompositionDuplicateBlock(
                20,
                (
                    CompositionDuplicateOccurrence(label, 1, 4, "source"),
                    CompositionDuplicateOccurrence("copy.py", 5, 8, "tests"),
                ),
            ),
        ),
        failures=(CompositionFailure(label, f"bad {label}"),),
    )
    lines = human(report, "rich")
    assert f"{label} source 0 0 0" in lines
    assert f"{label}:C 2 source 8 1" in lines
    assert f"{label}:f 3 source 7" in lines
    assert f"4 20 2 {label}:1-4" in lines
    assert f"{label} bad {label}" in lines


@pytest.mark.parametrize("output_format", ["plain", "rich"])
@pytest.mark.parametrize("detector_mismatch", [False, True])
def test_human_baseline_unknown_and_signed_deltas(
    report: CompositionReport, output_format: OutputFormat, detector_mismatch: bool
) -> None:
    # Keep metadata deterministic across Python versions; compare real snapshot data.
    report = replace(report, python_version="3.13.0")
    baseline = json.loads(render.render_composition_json(report))
    baseline["analyzer"]["python_version"] = "3.11.0"
    baseline["total"]["files"] = 2
    baseline["kinds"][0]["files"] = 3  # source: negative, tests: unknown, other: known zero
    del baseline["kinds"][1]["files"]
    baseline["total"]["code"] = 7
    baseline["kinds"][0]["code"] = 9
    baseline["kinds"][1]["code"] = 4
    current_counts = composition.analyze_source("x = 1\ny = 2\n").counts
    report = replace(report, total=replace(report.total, files=5, counts=current_counts))
    if detector_mismatch:
        baseline["detectors"][0]["version"] = 0
    comparison = composition_baseline.compare(report, baseline, path=Path("before.json"))
    lines = human(replace(report, baseline=comparison), output_format)
    assert "Changes Since Baseline" in lines
    assert "Baseline: before.json" in lines
    assert "Metric Current Δ Total Δ Source Δ Tests Δ Other" in lines
    assert "files 5 +3 -3 - +0" in lines
    assert "code 2 -5 -9 -4 +0" in lines
    assert "statements 2 +2 +0 +0 +0" in lines
    assert "Warning: Python version differs: baseline '3.11.0', current '3.13.0'" in lines
    warning = "Warning: not directly comparable with this baseline; see the warnings below."
    assert (warning in lines) is detector_mismatch
    if detector_mismatch:
        assert "Warning: detector qt version differs: baseline 0, current 1" in lines
    else:
        assert not any("detector" in line for line in lines)


@pytest.mark.parametrize("output_format", ["plain", "rich"])
@pytest.mark.parametrize("copies", [5, 6, 8])
def test_duplicate_location_boundary(
    report: CompositionReport, output_format: OutputFormat, copies: int
) -> None:
    occurrences = tuple(
        CompositionDuplicateOccurrence(f"copy{i}.py", i + 1, i + 4, "source") for i in range(copies)
    )
    report = replace(report, duplicates=(CompositionDuplicateBlock(37, occurrences),))
    lines = human(report, output_format)
    assert f"4 37 {copies} copy0.py:1-4" in lines
    for i in range(1, 5):
        assert f"copy{i}.py:{i + 1}-{i + 4}" in lines
    text = "\n".join(lines)
    assert "copy5.py" not in text
    if copies == 5:
        assert "... and" not in text
    else:
        assert f"... and {copies - 5} more" in lines
    data = json.loads(render.render_composition_json(report))
    assert data["duplicates"] == [
        {
            "lines": 4,
            "tokens": 37,
            "occurrences": [
                {"path": f"copy{i}.py", "start_line": i + 1, "end_line": i + 4, "kind": "source"}
                for i in range(copies)
            ],
        }
    ]


@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_mixed_and_empty_month_churn(
    report: CompositionReport, output_format: OutputFormat
) -> None:
    churn = CompositionChurn(
        ref="topic",
        months=2,
        since="2026-09",
        status="ok",
        reason=None,
        rows=(
            CompositionChurnMonth("2026-09", (11, 23, 5), (31, 3, 17)),
            CompositionChurnMonth("2026-10", (0, 0, 0), (0, 0, 0)),
        ),
    )
    report = replace(report, churn=churn)
    lines = human(report, output_format)
    assert "Monthly Churn (Python lines on topic, first-parent, since 2026-09)" in lines
    assert "Month Added Removed Net Source Net Tests Net Other Net" in lines
    assert "2026-09 39 51 -12 -20 +20 -12" in lines
    assert "2026-10 0 0 +0 +0 +0 +0" in lines
    data = json.loads(render.render_composition_json(report))
    assert data["churn"] == {
        "ref": "topic",
        "months": 2,
        "since": "2026-09",
        "status": "ok",
        "reason": None,
        "rows": [
            {
                "month": "2026-09",
                "added": {"total": 39, "source": 11, "tests": 23, "other": 5},
                "removed": {"total": 51, "source": 31, "tests": 3, "other": 17},
                "net": {"total": -12, "source": -20, "tests": 20, "other": -12},
            },
            {
                "month": "2026-10",
                "added": {"total": 0, "source": 0, "tests": 0, "other": 0},
                "removed": {"total": 0, "source": 0, "tests": 0, "other": 0},
                "net": {"total": 0, "source": 0, "tests": 0, "other": 0},
            },
        ],
    }


@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_skipped_churn(report: CompositionReport, output_format: OutputFormat) -> None:
    report = replace(
        report, churn=CompositionChurn("topic", 2, "2026-09", "skipped", "no history", ())
    )
    lines = human(report, output_format)
    if output_format == "plain":
        assert "Monthly Churn" in lines
        assert "(skipped: no history)" in lines
    else:
        assert "Monthly Churn skipped: no history" in lines
    assert "Month Added Removed Net Source Net Tests Net Other Net" not in lines
    data = json.loads(render.render_composition_json(report))
    assert data["churn"] == {
        "ref": "topic",
        "months": 2,
        "since": "2026-09",
        "status": "skipped",
        "reason": "no history",
        "rows": [],
    }
