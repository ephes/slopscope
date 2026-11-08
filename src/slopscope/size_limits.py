"""Size-limits check: hold oversized functions, classes, and test files at their current size.

Units are measured with the composition analyzer: functions and classes by their span (first
decorator to the end of the body), test files by their code lines. Units over a limit must be
recorded in an allowlist, where they may shrink but not grow.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import stat
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from slopscope import classify, composition
from slopscope._json import DuplicateKeyError, unique_object
from slopscope.report import (
    SIZE_LIMIT_KINDS,
    CompositionFailure,
    SizeLimitEntry,
    SizeLimitSettings,
    SizeLimitsReport,
)

DEFAULT_ALLOWLIST = "size-limits.json"
SCHEMA_VERSION = 1
MAX_ALLOWLIST_BYTES = 16 * 1024 * 1024
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


@dataclasses.dataclass(frozen=True)
class AllowlistDestination:
    path: Path
    target: Path
    identity: tuple[int, int] | None
    parent_identity: tuple[int, int] | None
    mode: int | None


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except RuntimeError as exc:
        raise SizeLimitsError(f"allowlist path contains a symlink loop: {path}") from exc


def _destination(path: Path, target: Path) -> AllowlistDestination:
    try:
        info = target.stat()
    except FileNotFoundError:
        info = None
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise SizeLimitsError(f"allowlist {path} must be a regular file")
    return AllowlistDestination(
        path,
        target,
        (info.st_dev, info.st_ino) if info else None,
        _identity(target.parent),
        stat.S_IMODE(info.st_mode) if info else None,
    )


def _verify_destination(destination: AllowlistDestination) -> None:
    if (
        _resolve(destination.path) != destination.target
        or _resolve(destination.target) != destination.target
        or _identity(destination.target) != destination.identity
        or _identity(destination.target.parent) != destination.parent_identity
    ):
        raise SizeLimitsError(f"allowlist target changed after locking: {destination.path}")


def _read_allowlist_bytes(
    path: Path, destination: AllowlistDestination | None = None
) -> bytes | None:
    try:
        if destination is not None:
            _verify_destination(destination)
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(destination.target if destination else path, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SizeLimitsError(f"allowlist {path} must be a regular file")
            if destination is not None and (info.st_dev, info.st_ino) != destination.identity:
                raise SizeLimitsError(f"allowlist target changed after locking: {path}")
            if info.st_size > MAX_ALLOWLIST_BYTES:
                raise SizeLimitsError(f"allowlist {path} exceeds {MAX_ALLOWLIST_BYTES} bytes")
            content = handle.read(MAX_ALLOWLIST_BYTES + 1)
            if len(content) > MAX_ALLOWLIST_BYTES:
                raise SizeLimitsError(f"allowlist {path} exceeds {MAX_ALLOWLIST_BYTES} bytes")
        if destination is not None:
            _verify_destination(destination)
        return content
    except FileNotFoundError:
        if destination is not None:
            _verify_destination(destination)
        return None
    except OSError as exc:
        raise SizeLimitsError(f"could not read allowlist {path}: {exc}") from exc


def load_allowlist(path: Path, *, destination: AllowlistDestination | None = None) -> Units | None:
    """Load an allowlist, or return ``None`` when the file does not exist."""

    content = _read_allowlist_bytes(path, destination)
    if content is None:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SizeLimitsError(f"allowlist {path} is not valid UTF-8: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=unique_object)
    except DuplicateKeyError as exc:
        raise SizeLimitsError(f"allowlist {path}: {exc}") from exc
    except RecursionError as exc:
        raise SizeLimitsError(f"allowlist {path}: JSON nesting is too deep") from exc
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


def write_allowlist(
    path: Path,
    allowlist: Mapping[str, Mapping[str, int]],
    *,
    destination: AllowlistDestination | None = None,
) -> None:
    """Write an allowlist with every section and sorted keys.

    Mutation callers pass the destination admitted by ``allowlist_locks``.
    """

    try:
        if destination is None:
            target = _resolve(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            destination = _destination(path, target)
        publish_allowlists([(path, allowlist)], destinations={path: destination})
    except OSError as exc:
        raise SizeLimitsError(f"could not write allowlist {path}: {exc}") from exc


@dataclasses.dataclass
class _PreparedAllowlist:
    destination: AllowlistDestination
    temporary: Path
    backup: Path | None
    installed_identity: tuple[int, int] | None = None
    keep_backup: bool = False


def _stage_bytes(target: Path, content: bytes, mode: int | None, *, suffix: str) -> Path:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", suffix=suffix, delete=False
        ) as handle:
            temporary = Path(handle.name)
            if mode is not None:
                os.chmod(temporary, mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise


def publish_allowlists(
    updates: Sequence[tuple[Path, Mapping[str, Mapping[str, int]]]],
    *,
    destinations: Mapping[Path, AllowlistDestination],
) -> None:
    """Stage the set, then replace; compensate handled errors under the caller's locks.

    This does not provide a transaction across process death. Failed compensation retains
    a named sibling backup for manual recovery.
    """

    prepared: list[_PreparedAllowlist] = []
    replaced: list[_PreparedAllowlist] = []
    try:
        for path, allowlist in updates:
            destination = destinations[path]
            _verify_destination(destination)
            data = {
                kind: dict(sorted(allowlist.get(kind, {}).items())) for kind in SIZE_LIMIT_KINDS
            }
            content = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
            temporary = _stage_bytes(
                destination.target, content, destination.mode, suffix=".pending"
            )
            item = _PreparedAllowlist(destination, temporary, None)
            prepared.append(item)
            prior = _read_allowlist_bytes(path, destination)
            if prior is not None:
                item.backup = _stage_bytes(
                    destination.target, prior, destination.mode, suffix=".backup"
                )
        # No replacements before every project has a durable output and recovery copy.
        for item in prepared:
            _verify_destination(item.destination)
        for item in prepared:
            _verify_destination(item.destination)
            item.installed_identity = _identity(item.temporary)
            os.replace(item.temporary, item.destination.target)
            replaced.append(item)
    except (OSError, SizeLimitsError) as exc:
        recovery_errors: list[str] = []
        for item in reversed(replaced):
            destination = item.destination
            try:
                # Do not restore through a changed ancestor or clobber an external writer.
                _verify_destination(
                    dataclasses.replace(destination, identity=item.installed_identity)
                )
                if item.backup is None:
                    destination.target.unlink()
                else:
                    os.replace(item.backup, destination.target)
            except (OSError, SizeLimitsError) as recovery:
                item.keep_backup = True
                evidence = (
                    f"restore from {item.backup}"
                    if item.backup is not None
                    else f"remove newly seeded {destination.target}"
                )
                recovery_errors.append(f"{destination.path}: {recovery}; {evidence}")
        details = "; compensation failed: " + "; ".join(recovery_errors) if recovery_errors else ""
        raise SizeLimitsError(f"could not publish allowlists: {exc}{details}") from exc
    finally:
        for item in prepared:
            for leftover in (item.temporary, None if item.keep_backup else item.backup):
                if leftover is not None:
                    with contextlib.suppress(OSError):
                        leftover.unlink(missing_ok=True)


@contextlib.contextmanager
def allowlist_locks(
    paths: Sequence[Path], *, action: str
) -> Iterator[dict[Path, AllowlistDestination]]:
    """Hold per-target OS locks across read/plan/publish; reports create no locks."""

    if action == REPORT:
        yield {}
        return
    try:
        targets = {path: _resolve(path) for path in paths}
        with contextlib.ExitStack() as stack:
            parents = {}
            for target in sorted(set(targets.values())):
                target.parent.mkdir(parents=True, exist_ok=True)
                parents[target] = _identity(target.parent)
                stack.enter_context(_allowlist_lock(target))
            destinations = {}
            for path, target in targets.items():
                if _resolve(path) != target or _identity(target.parent) != parents[target]:
                    raise SizeLimitsError(f"allowlist target changed while locking: {path}")
                destinations[path] = _destination(path, target)
            yield destinations
    except OSError as exc:
        raise SizeLimitsError(f"could not lock allowlist: {exc}") from exc


@contextlib.contextmanager
def _allowlist_lock(target: Path) -> Iterator[None]:
    # Keep the sibling file: unlinking it would let waiters lock different inodes.
    lock = target.with_name(f".{target.name}.lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        parent_identity = _identity(lock.parent)
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NONBLOCK", 0), 0o600)
    except OSError as exc:
        raise SizeLimitsError(f"could not lock allowlist {target}: {exc}") from exc
    try:
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise SizeLimitsError(f"allowlist lock {lock} must be a regular file")
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise SizeLimitsError(f"could not lock allowlist {target}: {exc}") from exc
        if _identity(lock.parent) != parent_identity or _identity(lock) != (
            os.fstat(descriptor).st_dev,
            os.fstat(descriptor).st_ino,
        ):
            raise SizeLimitsError(f"allowlist target changed while locking: {target}")
        yield
    finally:
        os.close(descriptor)


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

    with allowlist_locks([allowlist_path], action=action) as destinations:
        report, pending = plan(
            root,
            allowlist_path=allowlist_path,
            destination=destinations.get(allowlist_path),
            settings=settings,
            action=action,
            excluded_paths=excluded_paths,
            include_globs=include_globs,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
        )
        if pending is not None:
            write_allowlist(allowlist_path, pending, destination=destinations[allowlist_path])
    return report


def plan(
    root: Path,
    *,
    allowlist_path: Path,
    destination: AllowlistDestination | None = None,
    settings: SizeLimitSettings,
    action: str = REPORT,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
) -> tuple[SizeLimitsReport, Units | None]:
    """Measure and validate everything without writing.

    Returns the report and, for seed and update, the allowlist to write. Every refusal is raised
    here, so a caller can plan several projects before writing any of them. Mutation callers must
    hold ``allowlist_locks`` across all planning and publication and pass its destination
    to both operations, or use ``check``.
    """

    loaded = load_allowlist(allowlist_path, destination=destination)
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
