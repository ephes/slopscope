"""Compare a composition report with an earlier JSON snapshot."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from slopscope.report import (
    COMPOSITION_CATEGORIES,
    COMPOSITION_CONSTRUCTS,
    COMPOSITION_MARKERS,
    COMPOSITION_TAGS,
    COMPOSITION_TEST_PLACEMENTS,
    CompositionAggregate,
    CompositionComparison,
    CompositionMetricDelta,
    CompositionReport,
    CompositionScopeDelta,
)

SINGLE_REPORT_TYPE = "composition"
PROJECTS_REPORT_TYPE = "composition_projects"
HEADLINE_METRICS = (
    "files",
    "physical",
    "code",
    "statements",
    "continuation_lines",
    "duplicated_lines",
)
METRICS = (
    *HEADLINE_METRICS,
    *(f"categories.{name}" for name in COMPOSITION_CATEGORIES),
    *(f"tags.{name}" for name in COMPOSITION_TAGS),
    *(f"markers.{name}" for name in COMPOSITION_MARKERS),
    *(f"constructs.{name}" for name in COMPOSITION_CONSTRUCTS),
    *(f"test_placement.{name}" for name in COMPOSITION_TEST_PLACEMENTS),
)


# Settings that only change what is listed, not what is counted.
_PRESENTATION_SETTINGS = frozenset({"language", "limit"})


class BaselineError(Exception):
    """User-facing baseline loading error."""


def load_baseline(
    path: Path,
    *,
    expected_report_type: str,
    schema_version: int,
) -> dict[str, Any]:
    """Load a snapshot and reject files that cannot be compared with this report shape."""

    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise BaselineError(f"baseline file not found: {path}") from exc
    except OSError as exc:
        raise BaselineError(f"could not read baseline file {path}: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BaselineError(f"baseline file {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise BaselineError(f"baseline file {path} is not a composition report")
    report_type = data.get("report_type")
    if report_type != expected_report_type:
        raise BaselineError(
            f"baseline file {path} has report_type {report_type!r}, "
            f"expected {expected_report_type!r}"
        )
    baseline_schema = data.get("schema_version")
    if baseline_schema != schema_version:
        raise BaselineError(
            f"baseline file {path} has schema_version {baseline_schema!r}, "
            f"this version of slopscope writes {schema_version}"
        )
    if expected_report_type == PROJECTS_REPORT_TYPE:
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise BaselineError(f"baseline file {path} has no valid projects list")
        for index, project in enumerate(projects):
            if not isinstance(project, dict) or not isinstance(project.get("name"), str):
                raise BaselineError(f"baseline file {path} has an invalid projects[{index}]")
            _validate_report(project.get("report"), path=path, where=f"projects[{index}].report")
    else:
        _validate_report(data, path=path, where="report")
    return data


def _validate_report(data: object, *, path: Path, where: str) -> None:
    """Check the parts of a single report object that the comparison reads."""

    def fail(field: str) -> BaselineError:
        return BaselineError(f"baseline file {path} has an invalid {where} {field}")

    if not isinstance(data, dict):
        raise fail("object")
    if not isinstance(data.get("analyzer"), dict):
        raise fail("analyzer")
    detectors = data.get("detectors")
    if not isinstance(detectors, list) or not all(isinstance(item, dict) for item in detectors):
        raise fail("detectors")
    if not isinstance(data.get("total"), dict):
        raise fail("total")
    kinds = data.get("kinds")
    if not isinstance(kinds, list) or not all(isinstance(item, dict) for item in kinds):
        raise fail("kinds")


def project_baselines(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the per-project report objects of a multi-project snapshot by name."""

    return {
        str(project.get("name")): project["report"]
        for project in data.get("projects", [])
        if isinstance(project, dict) and isinstance(project.get("report"), dict)
    }


def compare(
    report: CompositionReport,
    baseline: Mapping[str, Any],
    *,
    path: Path,
) -> CompositionComparison:
    """Compare a report with one single-report snapshot object."""

    warnings: list[str] = []
    analyzer = baseline.get("analyzer")
    analyzer = analyzer if isinstance(analyzer, dict) else {}
    baseline_analyzer_version = analyzer.get("version")
    baseline_python_version = analyzer.get("python_version")
    if baseline_analyzer_version != report.analyzer_version:
        warnings.append(
            f"analyzer version differs: baseline {baseline_analyzer_version!r}, "
            f"current {report.analyzer_version}"
        )
    baseline_detectors = {
        detector.get("name"): detector.get("version")
        for detector in baseline.get("detectors", [])
        if isinstance(detector, dict)
    }
    for detector in report.detectors:
        if baseline_detectors.get(detector.name) != detector.version:
            warnings.append(
                f"detector {detector.name} version differs: baseline "
                f"{baseline_detectors.get(detector.name)!r}, current {detector.version}"
            )
    baseline_settings = baseline.get("settings")
    baseline_settings = baseline_settings if isinstance(baseline_settings, dict) else {}
    for name, value in report.settings.as_mapping().items():
        if name in _PRESENTATION_SETTINGS:
            continue
        if baseline_settings.get(name) != value:
            warnings.append(
                f"setting {name} differs: baseline {baseline_settings.get(name)!r}, "
                f"current {value!r}"
            )
    comparable = not warnings
    if baseline_python_version != report.python_version:
        warnings.append(
            f"Python version differs: baseline {baseline_python_version!r}, "
            f"current {report.python_version!r}"
        )

    baseline_kinds = {
        str(kind.get("name")): kind for kind in baseline.get("kinds", []) if isinstance(kind, dict)
    }
    total = baseline.get("total")
    scopes = [
        _scope_delta(report.total, total if isinstance(total, dict) else None),
        *(_scope_delta(kind, baseline_kinds.get(kind.name)) for kind in report.kinds),
    ]
    return CompositionComparison(
        path=path,
        analyzer_version=baseline_analyzer_version
        if isinstance(baseline_analyzer_version, int)
        else None,
        python_version=baseline_python_version
        if isinstance(baseline_python_version, str)
        else None,
        comparable=comparable,
        warnings=tuple(warnings),
        scopes=tuple(scopes),
    )


def missing_baseline(
    report: CompositionReport, *, path: Path, reason: str
) -> CompositionComparison:
    """Return a comparison without baseline values, for a project absent from the snapshot."""

    return CompositionComparison(
        path=path,
        analyzer_version=None,
        python_version=None,
        comparable=False,
        warnings=(reason,),
        scopes=tuple(_scope_delta(aggregate, None) for aggregate in (report.total, *report.kinds)),
    )


def _current_values(aggregate: CompositionAggregate) -> dict[str, int]:
    counts = aggregate.counts
    values = {
        "files": aggregate.files,
        "physical": counts.physical,
        "code": counts.code,
        "statements": counts.statements,
        "continuation_lines": counts.continuation_lines,
        "duplicated_lines": counts.duplicated_lines,
    }
    for group, mapping in (
        ("categories", counts.as_mapping()),
        ("tags", counts.tag_mapping()),
        ("markers", counts.marker_mapping()),
        ("constructs", counts.construct_mapping()),
        ("test_placement", counts.placement_mapping()),
    ):
        values.update({f"{group}.{name}": value for name, value in mapping.items()})
    return values


def _baseline_value(data: Mapping[str, Any] | None, metric: str) -> int | None:
    if data is None:
        return None
    value: Any = data
    for part in metric.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _scope_delta(
    aggregate: CompositionAggregate,
    baseline: Mapping[str, Any] | None,
) -> CompositionScopeDelta:
    current = _current_values(aggregate)
    return CompositionScopeDelta(
        name=aggregate.name,
        metrics=tuple(
            CompositionMetricDelta(
                metric=metric,
                baseline=_baseline_value(baseline, metric),
                current=current[metric],
            )
            for metric in METRICS
        ),
    )


def changed_metrics(comparison: CompositionComparison) -> Sequence[str]:
    """Return headline metrics plus every metric whose value changed in any scope."""

    changed = {
        metric.metric
        for scope in comparison.scopes
        for metric in scope.metrics
        if metric.delta not in (0, None)
    }
    return [metric for metric in METRICS if metric in HEADLINE_METRICS or metric in changed]
