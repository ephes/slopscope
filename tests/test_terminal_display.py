"""Human display escapes repository-controlled text; JSON keeps identities."""

from __future__ import annotations

import io
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from slopscope import cli, composition, composition_render, render, size_limits_render
from slopscope.render import OutputFormat
from slopscope.report import (
    AreaRow,
    CompositionClassRow,
    CompositionDuplicateBlock,
    CompositionDuplicateOccurrence,
    CompositionFailure,
    CompositionFileRow,
    CompositionFunctionRow,
    CompositionProjectReport,
    DirectoryRow,
    GroupedProfileReport,
    GroupedRow,
    LanguageRow,
    MultiProjectCompositionReport,
    MultiProjectReport,
    MultiProjectSizeLimitsReport,
    ProjectReport,
    ProjectSnapshotRow,
    RepositoryReport,
    SizeLimitEntry,
    SizeLimitSettings,
    SizeLimitsProjectReport,
    SizeLimitsReport,
    SkippedProject,
    SourceTestSummary,
)

PAYLOADS = [
    ("a\x1b[2Jb", r"a\x1b[2Jb"),
    ("a\x1b]8;;url\x07b", r"a\x1b]8;;url\x07b"),
    ("a\rb", r"a\rb"),
    ("a\nFORGEDb", r"a\nFORGEDb"),
    ("a\tb", r"a\tb"),
    ("a\x9bb", r"a\x9bb"),
    ("a\u202eb", r"a\u202eb"),
    ("a\u2028b", r"a\u2028b"),
]


def safe_output(output: str, escaped: str, output_format: OutputFormat) -> str:
    if output_format == "rich":
        assert "\x1b[" in output  # Real Rich still emits its own color sequences.
    text = re.sub(r"\x1b\[[0-9;]*m", "", output)
    assert all(char.isprintable() or char == "\n" for char in text)
    assert escaped in text
    assert "\nFORGED" not in text
    return text


@pytest.mark.parametrize(("label", "escaped"), PAYLOADS)
@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_report_and_profile_display(label: str, escaped: str, output_format: OutputFormat) -> None:
    report = RepositoryReport(
        "python",
        Path(label),
        (LanguageRow(label, 1, 0, 0, 1),),
        SourceTestSummary(1, 1, 0, 0),
        (AreaRow(label, 1, 1),),
        (DirectoryRow(label, 1, 1),),
    )
    projects = MultiProjectReport(
        "python",
        (ProjectReport(label, report),),
        (ProjectSnapshotRow(label, Path(label), "python", 1, 1, 1, 0),),
        (SkippedProject(label, Path(label), label),),
    )
    profile = GroupedProfileReport(
        label, "python", Path(label), label, (GroupedRow(label, 1, 1),), 1, None, True
    )
    for item in (report, projects, profile):
        safe_output(render.render_report(item, output_format=output_format), escaped, output_format)
    assert json.loads(render.render_json(projects))["projects"][0]["name"] == label
    assert json.loads(render.render_json(report))["directory_rows"][0]["name"] == label
    assert json.loads(render.render_json(profile))["rows"][0]["name"] == label
    assert str(report.path) == label


@pytest.mark.parametrize(("label", "escaped"), PAYLOADS)
@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_size_limits_display(label: str, escaped: str, output_format: OutputFormat) -> None:
    report = SizeLimitsReport(
        Path(label),
        "report",
        SizeLimitSettings(),
        Path(label),
        True,
        (1, 0, 0),
        (1, 0, 0),
        (SizeLimitEntry("functions", label, 200, 150),),
        (),
        (),
        (),
        (),
        (),
        (),
        (CompositionFailure(label, label),),
    )
    projects = MultiProjectSizeLimitsReport(
        (SizeLimitsProjectReport(label, report),), (SkippedProject(label, Path(label), label),)
    )
    for item in (report, projects):
        safe_output(
            size_limits_render.render_size_limits_report(item, output_format=output_format),
            escaped,
            output_format,
        )
    data = json.loads(size_limits_render.render_size_limits_json(report))
    assert data["over_limit"][0]["key"] == label
    assert data["failures"][0] == {"path": label, "error": label}


@pytest.mark.parametrize(("label", "escaped"), PAYLOADS)
@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_composition_display(
    tmp_path: Path, label: str, escaped: str, output_format: OutputFormat
) -> None:
    report = composition.build_composition_report(tmp_path, excluded_paths=())
    report = replace(
        report,
        path=Path(label),
        largest_modules=(CompositionFileRow(label, "source", label, report.total.counts),),
        largest_classes=(CompositionClassRow(label, label, 1, 2, 1, "source"),),
        largest_functions=(CompositionFunctionRow(label, label, 1, 2, "source"),),
        duplicates=(
            CompositionDuplicateBlock(20, (CompositionDuplicateOccurrence(label, 1, 2, "source"),)),
        ),
        failures=(CompositionFailure(label, label),),
    )
    projects = MultiProjectCompositionReport(
        report.analyzer,
        report.analyzer_version,
        report.schema_version,
        report.python_version,
        report.detectors,
        (CompositionProjectReport(label, report),),
        (SkippedProject(label, Path(label), label),),
    )
    for item in (report, projects):
        safe_output(
            composition_render.render_composition_report(item, output_format=output_format),
            escaped,
            output_format,
        )
    data = json.loads(composition_render.render_composition_json(report))
    assert data["largest_modules"][0]["path"] == label
    assert data["failures"][0]["error"] == label


@pytest.mark.parametrize("mode", ["--composition", "--size-limits"])
def test_cli_diagnostics_escape_actual_filename(tmp_path: Path, mode: str) -> None:
    name = "evil\x1b[2J\r\nFORGED.py"
    (tmp_path / name).write_text("def broken(:\n")
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path), mode, "--format", "json"], stdout=out, stderr=err) == 1
    safe_output(err.getvalue(), r"evil\x1b[2J\r\nFORGED.py", "plain")
    assert json.loads(out.getvalue())["failures"][0]["path"] == name


def test_cli_missing_root_diagnostic(tmp_path: Path) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert cli.run([str(tmp_path / "a\nFORGED")], stdout=out, stderr=err) == 2
    safe_output(err.getvalue(), r"a\nFORGED", "plain")
    assert out.getvalue() == ""


def test_argument_error_escapes_control_characters(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.run(["--unknown\x1b[2J\nFORGED"])
    assert raised.value.code == 2
    safe_output(capsys.readouterr().err, r"--unknown\x1b[2J\nFORGED", "plain")


@pytest.mark.parametrize("output_format", ["plain", "rich"])
def test_printable_unicode_and_brackets_remain_literal(output_format: OutputFormat) -> None:
    label = "[red]雪[/red]:warning:"
    report = GroupedProfileReport(
        label, "python", Path(label), label, (GroupedRow(label, 1, 1),), 1, None, True
    )
    output = render.render_report(report, output_format=output_format)
    assert label in re.sub(r"\x1b\[[0-9;]*m", "", output)


def test_rich_fallback_and_colorless_display(monkeypatch: pytest.MonkeyPatch) -> None:
    report = GroupedProfileReport("a\nFORGED", "python", Path("a\x1b[2J"), "x", (), 0, None, True)
    safe_output(render.render_rich(report, color=False), r"a\nFORGED", "plain")
    monkeypatch.setattr(render, "_load_rich", lambda: None)
    safe_output(render.render_rich(report), r"a\nFORGED", "plain")
