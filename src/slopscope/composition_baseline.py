"""Compare a composition report with an earlier JSON snapshot."""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from slopscope._json import DuplicateKeyError, unique_object
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

MAX_BASELINE_BYTES = 16 * 1024 * 1024

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
_METRIC_PATHS = tuple((metric, tuple(metric.split("."))) for metric in METRICS)


# Settings that only change what is listed, not what is counted.
# The admission ceiling also leaves counts unchanged for complete reports.
_PRESENTATION_SETTINGS = frozenset(
    {
        "language",
        "limit",
        "max_source_bytes",
        "max_total_source_bytes",
        "max_total_tokens",
        "max_total_anchors",
    }
)


class BaselineError(Exception):
    """User-facing baseline error."""


class _NonFiniteNumberError(ValueError):
    """A JSON number would decode to a nonfinite value."""


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise _NonFiniteNumberError("JSON numbers must be finite")
    return value


def load_baseline(
    path: Path,
    *,
    expected_report_type: str,
    schema_version: int,
) -> dict[str, Any]:
    """Load a snapshot and reject files that cannot be compared with this report shape."""

    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with ExitStack() as stack:
            descriptor = os.open(path, flags)
            stack.callback(os.close, descriptor)
            handle = stack.enter_context(os.fdopen(descriptor, "rb", closefd=False))
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise BaselineError(f"baseline file {path} must be a regular file")
            if info.st_size > MAX_BASELINE_BYTES:
                raise BaselineError(f"baseline file {path} exceeds {MAX_BASELINE_BYTES} bytes")
            # The file can grow after fstat; bound the actual read too.
            content = handle.read(MAX_BASELINE_BYTES + 1)
            if len(content) > MAX_BASELINE_BYTES:
                raise BaselineError(f"baseline file {path} exceeds {MAX_BASELINE_BYTES} bytes")
            after = os.fstat(handle.fileno())
            if (
                len(content) != after.st_size
                or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (
                    info.st_ctime_ns != after.st_ctime_ns
                    # Atomic pathname replacement unlinks the opened, unchanged inode.
                    and not (info.st_nlink > 0 and after.st_nlink == 0)
                )
            ):
                raise BaselineError(f"baseline file {path} changed while reading")
        data = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=_parse_finite_float,
            parse_float=_parse_finite_float,
        )
    except FileNotFoundError as exc:
        raise BaselineError(f"baseline file not found: {path}") from exc
    except OSError as exc:
        raise BaselineError(f"could not read baseline file {path}: {exc}") from exc
    except (DuplicateKeyError, _NonFiniteNumberError) as exc:
        raise BaselineError(f"baseline file {path}: {exc}") from exc
    except RecursionError as exc:
        raise BaselineError(f"baseline file {path}: JSON nesting is too deep") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BaselineError(f"baseline file {path} is not valid JSON: {exc}") from exc
    except ValueError as exc:
        raise BaselineError(f"baseline file {path}: JSON numeric decoding limit exceeded") from exc

    if not isinstance(data, dict):
        raise BaselineError(f"baseline file {path} is not a composition report")
    _validate_report_header(data, str(path), expected_report_type, schema_version)
    if expected_report_type == PROJECTS_REPORT_TYPE:
        projects = data.get("projects")
        if not isinstance(projects, list):
            raise BaselineError(f"baseline file {path} has no valid projects list")
        seen_projects: set[str] = set()
        for index, project in enumerate(projects):
            if not isinstance(project, dict) or not isinstance(project.get("name"), str):
                raise BaselineError(f"baseline file {path} has an invalid projects[{index}]")
            name = project["name"]
            if name in seen_projects:
                raise BaselineError(
                    f"baseline file {path} has a duplicate projects[{index}].name: {name!r}"
                )
            seen_projects.add(name)
            where = f"projects[{index}].report"
            nested = project.get("report")
            if not isinstance(nested, dict):
                raise BaselineError(f"baseline file {path} has an invalid {where} object")
            _validate_report_header(nested, f"{path} {where}", SINGLE_REPORT_TYPE, schema_version)
            _validate_report(nested, path=path, where=where)
    else:
        _validate_report(data, path=path, where="report")
    return data


def _validate_report_header(
    data: Mapping[str, Any], location: str, expected_report_type: str, schema_version: int
) -> None:
    report_type = data.get("report_type")
    if report_type != expected_report_type:
        raise BaselineError(
            f"baseline file {location} has report_type {report_type!r}, "
            f"expected {expected_report_type!r}"
        )
    baseline_schema = data.get("schema_version")
    if type(baseline_schema) is not int or baseline_schema != schema_version:
        raise BaselineError(
            f"baseline file {location} has schema_version {baseline_schema!r}, "
            f"this version of slopscope writes {schema_version}"
        )


def _validate_report(data: Mapping[str, Any], *, path: Path, where: str) -> None:
    """Check the parts of a single report object that the comparison reads."""

    def fail(field: str) -> BaselineError:
        return BaselineError(f"baseline file {path} has an invalid {where} {field}")

    analyzer = data.get("analyzer")
    if not isinstance(analyzer, dict):
        raise fail("analyzer")
    _analyzer_versions(analyzer, path=path, where=where)
    detectors = data.get("detectors")
    if not isinstance(detectors, list) or not all(isinstance(item, dict) for item in detectors):
        raise fail("detectors")
    seen_detectors: set[str] = set()
    for index, detector in enumerate(detectors):
        for field in ("name", "kind"):
            if not isinstance(detector.get(field), str):
                raise fail(f"detectors[{index}].{field}")
        name = detector["name"]
        if name in seen_detectors:
            raise fail(f"detectors[{index}].name (duplicate {name!r})")
        seen_detectors.add(name)
        version = detector.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise fail(f"detectors[{index}].version")
    if not isinstance(data.get("total"), dict):
        raise fail("total")
    kinds = data.get("kinds")
    if not isinstance(kinds, list) or not all(isinstance(item, dict) for item in kinds):
        raise fail("kinds")
    total_counts = _read_metrics(data["total"], path=path, where=f"{where} total")
    seen_kinds: set[str] = set()
    for index, kind in enumerate(kinds):
        name = kind.get("name")
        if not isinstance(name, str):
            raise fail(f"kinds[{index}].name")
        if name in seen_kinds:
            raise fail(f"kinds[{index}].name (duplicate {name!r})")
        seen_kinds.add(name)
        _read_kind_metrics(kind, total_counts, path=path, where=f"{where} kinds[{index}]")


def _analyzer_versions(
    analyzer: Mapping[str, Any], *, path: Path, where: str
) -> tuple[int | None, str | None]:
    version = analyzer.get("version")
    if "version" in analyzer and type(version) is not int:
        raise BaselineError(f"baseline file {path} has an invalid {where} analyzer.version")
    python_version = analyzer.get("python_version")
    if "python_version" in analyzer and type(python_version) is not str:
        raise BaselineError(f"baseline file {path} has an invalid {where} analyzer.python_version")
    return version, python_version


def _read_metrics(data: Mapping[str, Any], *, path: Path, where: str) -> dict[str, int]:
    """Read present comparison counts, which must be nonnegative integers."""

    counts: dict[str, int] = {}
    for metric, parts in _METRIC_PATHS:
        value = data
        for index, part in enumerate(parts):
            if part not in value:
                break
            item = value[part]
            if index < len(parts) - 1:
                if isinstance(item, Mapping):
                    value = item
                    continue
            elif isinstance(item, int) and not isinstance(item, bool) and item >= 0:
                counts[metric] = item
                break
            field = f"{where}.{'.'.join(parts[: index + 1])}"
            raise BaselineError(f"baseline file {path} has an invalid {field}")
    return counts


def _read_kind_metrics(
    kind: Mapping[str, Any], total_counts: Mapping[str, int], *, path: Path, where: str
) -> dict[str, int]:
    counts = _read_metrics(kind, path=path, where=where)
    for metric in ("files", "physical", "code"):
        count = counts.get(metric)
        total = total_counts.get(metric)
        if count is not None and total is not None and count > total:
            raise BaselineError(
                f"baseline file {path} has an invalid {where}.{metric} (exceeds total.{metric})"
            )
    return counts


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
    baseline_analyzer_version, baseline_python_version = _analyzer_versions(
        analyzer if isinstance(analyzer, Mapping) else {}, path=path, where="report"
    )
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
    current_detector_names = {detector.name for detector in report.detectors}
    for name in baseline_detectors:
        if name not in current_detector_names:
            warnings.append(f"detector {name} is present only in the baseline")
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

    total = baseline.get("total")
    total_counts = (
        _read_metrics(total, path=path, where="report total") if isinstance(total, Mapping) else {}
    )
    baseline_kinds = {
        str(kind.get("name")): _read_kind_metrics(
            kind, total_counts, path=path, where=f"report kinds[{index}]"
        )
        for index, kind in enumerate(baseline.get("kinds", []))
        if isinstance(kind, Mapping)
    }
    scopes = [
        _scope_delta(report.total, total_counts),
        *(_scope_delta(kind, baseline_kinds.get(kind.name, {})) for kind in report.kinds),
    ]
    return CompositionComparison(
        path=path,
        analyzer_version=baseline_analyzer_version,
        python_version=baseline_python_version,
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
        scopes=tuple(_scope_delta(aggregate, {}) for aggregate in (report.total, *report.kinds)),
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


def _scope_delta(
    aggregate: CompositionAggregate,
    baseline: Mapping[str, int],
) -> CompositionScopeDelta:
    current = _current_values(aggregate)
    return CompositionScopeDelta(
        name=aggregate.name,
        metrics=tuple(
            CompositionMetricDelta(
                metric=metric,
                baseline=baseline.get(metric),
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
