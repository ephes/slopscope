"""Size-limits check: hold oversized functions, classes, and test files at their current size.

Units are measured with the composition analyzer: functions and classes by their span (first
decorator to the end of the body), test files by their code lines. Units over a limit must be
recorded in an allowlist, where they may shrink but not grow.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from slopscope import classify, composition
from slopscope.report import (
    SIZE_LIMIT_KINDS,
    CompositionFailure,
    SizeLimitEntry,
    SizeLimitSettings,
    SizeLimitsReport,
)

DEFAULT_ALLOWLIST = "size-limits.json"
SCHEMA_VERSION = 1
REPORT = "report"
UPDATE = "update"
SEED = "seed"

Units = dict[str, dict[str, int]]


class SizeLimitsError(Exception):
    """User-facing allowlist or action error."""


def measure(
    root: Path,
    *,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
) -> tuple[Units, tuple[CompositionFailure, ...]]:
    """Measure every function, class, and test file below ``root``, keyed as in the allowlist."""

    units: Units = {kind: {} for kind in SIZE_LIMIT_KINDS}
    failures: list[CompositionFailure] = []
    for relative_path in composition.discover_python_files(
        root, excluded_paths=excluded_paths, include_globs=include_globs
    ):
        try:
            analysis = composition.analyze_file(root, relative_path)
        except composition.CompositionAnalysisError as exc:
            failures.append(CompositionFailure(path=relative_path, error=str(exc)))
            continue
        functions = [
            (definition.line, definition.name, definition.lines)
            for definition in analysis.functions
            if not definition.overload
        ]
        classes = [
            (definition.line, definition.name, definition.lines) for definition in analysis.classes
        ]
        units["functions"].update(_keyed(relative_path, functions))
        units["classes"].update(_keyed(relative_path, classes))
        kind = classify.classify_source_test(
            relative_path, source_dirs=source_dirs, test_dirs=test_dirs
        )
        if kind == "tests":
            units["test_files"][relative_path] = analysis.counts.code
    return units, tuple(failures)


def _keyed(path: str, definitions: list[tuple[int, str, int]]) -> dict[str, int]:
    """Key definitions as ``path::Qualified.name``, numbering repeats ``#2``, ``#3`` in order."""

    keyed: dict[str, int] = {}
    seen: dict[str, int] = {}
    for _line, name, size in sorted(definitions):
        base = f"{path}::{name}"
        seen[base] = seen.get(base, 0) + 1
        key = base if seen[base] == 1 else f"{base}#{seen[base]}"
        keyed[key] = size
    return keyed


def load_allowlist(path: Path) -> Units | None:
    """Load an allowlist, or return ``None`` when the file does not exist."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise SizeLimitsError(f"allowlist {path} is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise SizeLimitsError(f"could not read allowlist {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SizeLimitsError(f"allowlist {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SizeLimitsError(f"allowlist {path} must be a JSON object")
    unknown = sorted(set(data) - set(SIZE_LIMIT_KINDS))
    if unknown:
        raise SizeLimitsError(f"allowlist {path} has an unknown section: {unknown[0]}")
    allowlist: Units = {}
    for kind in SIZE_LIMIT_KINDS:
        entries = data.get(kind, {})
        if not isinstance(entries, dict):
            raise SizeLimitsError(f"allowlist {path} section {kind} must be an object")
        for key, size in entries.items():
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise SizeLimitsError(
                    f"allowlist {path} entry {kind}[{key!r}] must be a positive integer"
                )
        allowlist[kind] = dict(entries)
    return allowlist


def write_allowlist(path: Path, allowlist: Mapping[str, Mapping[str, int]]) -> None:
    """Write an allowlist with every section and sorted keys."""

    data = {kind: dict(sorted(allowlist.get(kind, {}).items())) for kind in SIZE_LIMIT_KINDS}
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise SizeLimitsError(f"could not write allowlist {path}: {exc}") from exc


def check(
    root: Path,
    *,
    allowlist_path: Path,
    settings: SizeLimitSettings,
    action: str = REPORT,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
) -> SizeLimitsReport:
    """Measure a project, apply ``action`` to its allowlist, and report the four groups."""

    report, pending = plan(
        root,
        allowlist_path=allowlist_path,
        settings=settings,
        action=action,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
        source_dirs=source_dirs,
        test_dirs=test_dirs,
    )
    if pending is not None:
        write_allowlist(allowlist_path, pending)
    return report


def plan(
    root: Path,
    *,
    allowlist_path: Path,
    settings: SizeLimitSettings,
    action: str = REPORT,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
) -> tuple[SizeLimitsReport, Units | None]:
    """Measure and validate everything without writing.

    Returns the report and, for seed and update, the allowlist to write. Every refusal is raised
    here, so a caller can plan several projects before writing any of them.
    """

    loaded = load_allowlist(allowlist_path)
    existed = loaded is not None
    if action == SEED and existed:
        raise SizeLimitsError(
            f"allowlist {allowlist_path} already exists; update it or delete it before seeding"
        )
    if action == UPDATE and not existed:
        raise SizeLimitsError(f"no allowlist at {allowlist_path}; seed one first")

    units, failures = measure(
        root,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
        source_dirs=source_dirs,
        test_dirs=test_dirs,
    )
    if failures and action != REPORT:
        raise SizeLimitsError(
            f"cannot {action} the allowlist while {len(failures)} file(s) fail to parse"
        )

    allowlist: Units = loaded or {kind: {} for kind in SIZE_LIMIT_KINDS}
    seeded: list[SizeLimitEntry] = []
    lowered: list[SizeLimitEntry] = []
    dropped: list[SizeLimitEntry] = []
    if action == SEED:
        allowlist = {
            kind: {key: size for key, size in units[kind].items() if size > settings.limit(kind)}
            for kind in SIZE_LIMIT_KINDS
        }
        seeded = [
            SizeLimitEntry(kind=kind, key=key, size=size, limit=settings.limit(kind))
            for kind in SIZE_LIMIT_KINDS
            for key, size in sorted(allowlist[kind].items())
        ]
    elif action == UPDATE:
        allowlist, lowered, dropped = _updated(units, allowlist, settings)

    over_limit, grown, shrunk, stale = _groups(units, allowlist, settings, failures)
    report = SizeLimitsReport(
        path=root,
        action=action,
        settings=settings,
        allowlist_path=allowlist_path,
        allowlist_existed=existed,
        allowlist_entries=tuple(len(allowlist.get(kind, {})) for kind in SIZE_LIMIT_KINDS),
        units=tuple(len(units[kind]) for kind in SIZE_LIMIT_KINDS),
        over_limit=tuple(over_limit),
        grown=tuple(grown),
        shrunk=tuple(shrunk),
        stale=tuple(stale),
        lowered=tuple(lowered),
        dropped=tuple(dropped),
        seeded=tuple(seeded),
        failures=failures,
    )
    return report, allowlist if action in (SEED, UPDATE) else None


def _unit_path(kind: str, key: str) -> str:
    return key if kind == "test_files" else key.split("::", 1)[0]


def _groups(
    units: Units,
    allowlist: Units,
    settings: SizeLimitSettings,
    failures: Sequence[CompositionFailure],
) -> tuple[list[SizeLimitEntry], ...]:
    failed_paths = {failure.path for failure in failures}
    over_limit: list[SizeLimitEntry] = []
    grown: list[SizeLimitEntry] = []
    shrunk: list[SizeLimitEntry] = []
    stale: list[SizeLimitEntry] = []
    for kind in SIZE_LIMIT_KINDS:
        limit = settings.limit(kind)
        recorded_sizes = allowlist.get(kind, {})
        for key, size in sorted(units[kind].items()):
            if key not in recorded_sizes and size > limit:
                over_limit.append(SizeLimitEntry(kind=kind, key=key, size=size, limit=limit))
        for key, recorded in sorted(recorded_sizes.items()):
            if _unit_path(kind, key) in failed_paths:
                # The file was not measured, so nothing can be said about this entry.
                continue
            current = units[kind].get(key)
            entry = SizeLimitEntry(kind=kind, key=key, size=current, limit=limit, recorded=recorded)
            if current is None:
                stale.append(dataclasses.replace(entry, reason="missing"))
            elif current <= limit:
                stale.append(dataclasses.replace(entry, reason="within limit"))
            elif current > recorded:
                grown.append(entry)
            elif current < recorded:
                shrunk.append(entry)
    return over_limit, grown, shrunk, stale


def _updated(
    units: Units,
    allowlist: Units,
    settings: SizeLimitSettings,
) -> tuple[Units, list[SizeLimitEntry], list[SizeLimitEntry]]:
    """Lower shrunk entries and drop missing or within-limit ones; never add or raise."""

    updated: Units = {}
    lowered: list[SizeLimitEntry] = []
    dropped: list[SizeLimitEntry] = []
    for kind in SIZE_LIMIT_KINDS:
        limit = settings.limit(kind)
        updated[kind] = {}
        for key, recorded in sorted(allowlist.get(kind, {}).items()):
            size = units[kind].get(key)
            if size is None or size <= limit:
                dropped.append(
                    SizeLimitEntry(
                        kind=kind,
                        key=key,
                        size=size,
                        limit=limit,
                        recorded=recorded,
                        reason="missing" if size is None else "within limit",
                    )
                )
                continue
            if size < recorded:
                lowered.append(
                    SizeLimitEntry(kind=kind, key=key, size=size, limit=limit, recorded=recorded)
                )
                updated[kind][key] = size
            else:
                updated[kind][key] = recorded
    return updated, lowered, dropped
