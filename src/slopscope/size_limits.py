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
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from functools import partial
from itertools import chain
from pathlib import Path

from slopscope import _source, classify, composition, composition_duplication, fallback
from slopscope._json import DuplicateKeyError, unique_object
from slopscope.project import AdmittedProject
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
MAX_ALLOWLIST_STAGING_BYTES = 128 * 1024 * 1024
ALLOWLIST_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_RETRY_SECONDS = 0.05
REPORT = "report"
UPDATE = "update"
SEED = "seed"

Units = dict[str, dict[str, int]]
UnitScope = Callable[[str, str], tuple[bool, str | None]]


class SizeLimitsError(Exception):
    """User-facing allowlist or action error."""


def measure(
    root: Path,
    *,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
    max_source_bytes: int = composition.DEFAULT_MAX_SOURCE_BYTES,
    max_total_source_bytes: int | None = None,
    skips: fallback.DiscoverySkips | None = None,
) -> tuple[Units, tuple[CompositionFailure, ...]]:
    """Measure every function, class, and test file below ``root``, keyed as in the allowlist.

    Use structural line counts and definition spans, with overload stubs excluded.
    Only the cumulative source-byte budget applies.
    """

    budget = composition.AnalysisBudget(
        composition.DEFAULT_MAX_TOTAL_SOURCE_BYTES
        if max_total_source_bytes is None
        else max_total_source_bytes,
        None,
    )
    units: Units = {kind: {} for kind in SIZE_LIMIT_KINDS}
    failures: list[CompositionFailure] = []
    admissions: dict[str, _source.Admission] = {}
    for relative_path in composition.discover_python_files(
        root,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
        admissions=admissions,
        skips=skips,
    ):
        try:
            analysis = composition.analyze_file(
                root,
                relative_path,
                measure_only=True,
                admission=admissions[relative_path],
                max_source_bytes=max_source_bytes,
                budget=budget,
            )
        except composition_duplication.BudgetExceeded as exc:
            raise fallback.CountingError(f"incomplete analysis: whole-size-limits {exc}") from exc
        except composition.CompositionAnalysisError as exc:
            failures.append(CompositionFailure(path=relative_path, error=str(exc)))
            continue
        _add_keyed(units["functions"], relative_path, analysis.functions)
        _add_keyed(units["classes"], relative_path, analysis.classes)
        kind = classify.classify_source_test(
            relative_path, source_dirs=source_dirs, test_dirs=test_dirs
        )
        if kind == "tests":
            units["test_files"][relative_path] = analysis.counts.code
    return units, tuple(failures)


def _add_keyed(
    units: dict[str, int], path: str, definitions: Sequence[composition.DefinitionSize]
) -> None:
    """Skip overloads and number repeated definitions in source order."""

    seen: dict[str, int] = {}
    for definition in definitions:
        if definition.overload:
            continue
        name = definition.name
        ordinal = seen.get(name, 0) + 1
        seen[name] = ordinal
        base = f"{path}::{name}"
        key = base if ordinal == 1 else f"{base}#{ordinal}"
        units[key] = definition.lines


@dataclasses.dataclass(frozen=True)
class AllowlistDestination:
    path: Path
    target: Path
    identity: tuple[int, int] | None
    parent_identity: tuple[int, int] | None
    mode: int | None


def _identity(path: Path, *, follow_symlinks: bool = True) -> tuple[int, int] | None:
    try:
        info = path.stat(follow_symlinks=follow_symlinks)
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
        with contextlib.ExitStack() as stack:
            descriptor = os.open(destination.target if destination else path, flags)
            stack.callback(os.close, descriptor)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise SizeLimitsError(f"allowlist {path} must be a regular file")
            if destination is not None and (info.st_dev, info.st_ino) != destination.identity:
                raise SizeLimitsError(f"allowlist target changed after locking: {path}")
            if info.st_size > MAX_ALLOWLIST_BYTES:
                raise SizeLimitsError(f"allowlist {path} exceeds {MAX_ALLOWLIST_BYTES} bytes")
            handle = stack.enter_context(os.fdopen(descriptor, "rb", closefd=False))
            content = handle.read(MAX_ALLOWLIST_BYTES + 1)
            if len(content) > MAX_ALLOWLIST_BYTES:
                raise SizeLimitsError(f"allowlist {path} exceeds {MAX_ALLOWLIST_BYTES} bytes")
            after = os.fstat(descriptor)
            if len(content) != info.st_size or (
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise SizeLimitsError(f"allowlist {path} changed during read")
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
    except ValueError as exc:
        raise SizeLimitsError(f"allowlist {path}: JSON number exceeds decoder limits") from exc
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


def _sync_directory(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        raise SizeLimitsError(
            "allowlist publication requires directory synchronization (O_DIRECTORY)"
        )
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_parent(parent: Path) -> None:
    missing: list[Path] = []
    current = parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    parent.mkdir(parents=True, exist_ok=True)
    # Persist new ancestor names before publishing anything below them.
    for directory in reversed(missing):
        _sync_directory(directory.parent)


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
            _serialize_allowlist(path, allowlist)  # Admit before creating a missing parent.
            target = _resolve(path)
            _ensure_parent(target.parent)
            destination = _destination(path, target)
        publish_allowlists([(path, allowlist)], destinations={path: destination})
    except OSError as exc:
        raise SizeLimitsError(f"could not write allowlist {path}: {exc}") from exc


@dataclasses.dataclass(frozen=True)
class _StagedAllowlist:
    path: Path
    identity: tuple[int, int]

    def matches(self) -> bool:
        return _identity(self.path, follow_symlinks=False) == self.identity

    def verify(self) -> None:
        if not self.matches():
            raise SizeLimitsError(f"allowlist staging entry changed: {self.path}")

    def unlink(self) -> None:
        self.verify()
        self.path.unlink()

    def discard(self) -> None:
        with contextlib.suppress(OSError, SizeLimitsError):
            self.unlink()


@dataclasses.dataclass
class _PreparedAllowlist:
    destination: AllowlistDestination
    temporary: _StagedAllowlist
    backup: _StagedAllowlist | None
    keep_backup: bool = False
    prior: bytes | None = None


def _stage_bytes(
    target: Path, content: bytes, mode: int | None, *, suffix: str
) -> _StagedAllowlist:
    temporary: _StagedAllowlist | None = None
    try:
        # Close explicitly so a close error cannot hide the staging failure.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="wb", dir=target.parent, prefix=f".{target.name}.", suffix=suffix, delete=False
        )
        try:
            descriptor = handle.fileno()
            info = os.fstat(descriptor)
            temporary = _StagedAllowlist(Path(handle.name), (info.st_dev, info.st_ino))
            if mode is not None:
                if os.chmod in os.supports_fd:
                    os.chmod(descriptor, mode)
                else:
                    temporary.verify()
                    os.chmod(temporary.path, mode)
            written = handle.write(content)
            if written != len(content):
                raise SizeLimitsError(
                    f"short write staging allowlist {temporary.path}: "
                    f"wrote {written} of {len(content)} bytes"
                )
            handle.flush()
            os.fsync(descriptor)
        except BaseException:
            with contextlib.suppress(OSError):
                handle.close()
            raise
        else:
            handle.close()
        return temporary
    except BaseException:
        if temporary is not None:
            temporary.discard()
        raise


def _retain_backup(item: _PreparedAllowlist) -> None:
    """Keep owned recovery evidence or recreate it under a fresh name."""

    if item.prior is None:
        return
    backup, item.backup = item.backup, None
    if backup is not None and backup.matches():
        item.backup = backup
        return
    backup = _stage_bytes(
        item.destination.target, item.prior, item.destination.mode, suffix=".backup"
    )
    backup.verify()
    item.backup = backup
    _sync_directory(item.destination.target.parent)


def _staging_overflow(path: Path) -> SizeLimitsError:
    return SizeLimitsError(
        f"allowlist publication staging exceeds {MAX_ALLOWLIST_STAGING_BYTES} bytes at {path}"
    )


def _serialize_allowlist(
    path: Path, allowlist: Mapping[str, Mapping[str, int]], *, staging_bytes: int = 0
) -> bytes:
    data = {kind: dict(allowlist.get(kind, {})) for kind in SIZE_LIMIT_KINDS}
    encoder = json.JSONEncoder(indent=2, sort_keys=True, ensure_ascii=True)
    content = bytearray()
    for fragment in chain(encoder.iterencode(data), ("\n",)):
        # The ASCII-only JSON representation uses one UTF-8 byte per character.
        size = len(content) + len(fragment)
        if size > MAX_ALLOWLIST_BYTES:
            raise SizeLimitsError(f"allowlist {path} exceeds {MAX_ALLOWLIST_BYTES} bytes")
        if staging_bytes + size > MAX_ALLOWLIST_STAGING_BYTES:
            raise _staging_overflow(path)
        content.extend(fragment.encode("utf-8"))
    return bytes(content)


def publish_allowlists(
    updates: Sequence[tuple[Path, Mapping[str, Mapping[str, int]]]],
    *,
    destinations: Mapping[Path, AllowlistDestination],
    project_roots: Sequence[AdmittedProject] = (),
) -> None:
    """Stage the set, then replace; compensate handled errors under the caller's locks.

    Recheck the admitted project roots after preparing the batch and before staging.
    This does not provide a transaction across process death. Directory-sync uncertainty
    leaves visible publication in place and retains recovery evidence. Failed compensation
    also retains a named sibling backup for manual recovery.
    """

    prepared: list[_PreparedAllowlist] = []
    replaced: list[_PreparedAllowlist] = []
    durability_uncertain = False
    try:
        # Admit the whole batch before staging any output or recovery copy.
        contents = []
        staging_bytes = 0
        for path, data in updates:
            content = _serialize_allowlist(path, data, staging_bytes=staging_bytes)
            prior = _read_allowlist_bytes(path, destinations[path])
            staging_bytes += len(content) + len(prior or b"")
            if staging_bytes > MAX_ALLOWLIST_STAGING_BYTES:
                raise _staging_overflow(path)
            contents.append((path, content, prior))
        for project in project_roots:
            project.verify_directory()
        for path, content, prior in contents:
            destination = destinations[path]
            _verify_destination(destination)
            temporary = _stage_bytes(
                destination.target, content, destination.mode, suffix=".pending"
            )
            item = _PreparedAllowlist(destination, temporary, None, prior=prior)
            prepared.append(item)
            if prior is not None:
                item.backup = _stage_bytes(
                    destination.target, prior, destination.mode, suffix=".backup"
                )
        # Persist recovery names before replacing any destination.
        for item in prepared:
            _verify_destination(item.destination)
            _sync_directory(item.destination.target.parent)
        for item in prepared:
            _verify_destination(item.destination)
            if item.backup is not None:
                item.backup.verify()
            item.temporary.verify()
            # Keep the prior bytes until the outcome is settled, so an interruption at any
            # point after this replacement (including during compensation) retains them.
            item.keep_backup = True
            replaced.append(item)  # Include an interrupted rename whose outcome is uncertain.
            os.replace(item.temporary.path, item.destination.target)
            durability_uncertain = True
            _sync_directory(item.destination.target.parent)
            durability_uncertain = False
        # All target names are durable before any recovery evidence is retired.
        durability_uncertain = True
        for item in prepared:
            if item.backup is not None:
                item.backup.unlink()
                _sync_directory(item.destination.target.parent)
        durability_uncertain = False
        for item in prepared:
            item.keep_backup = False
    except BaseException as exc:
        for item in prepared:
            if item not in replaced:
                item.keep_backup = False  # Not replaced: the target still holds its prior bytes.
        recovery_errors: list[str] = []
        for item in reversed(replaced):
            item.keep_backup = True
            destination = item.destination
            try:
                if durability_uncertain:
                    raise SizeLimitsError("publication visible; durability unconfirmed")
                # A rename may have been interrupted before it changed the target.
                if _identity(destination.target) == destination.identity:
                    _verify_destination(destination)
                    item.keep_backup = False
                    continue
                # Do not restore through a changed ancestor or clobber an external writer.
                _verify_destination(
                    dataclasses.replace(destination, identity=item.temporary.identity)
                )
                if item.backup is None:
                    destination.target.unlink()
                else:
                    item.backup.verify()
                    os.replace(item.backup.path, destination.target)
                _sync_directory(destination.target.parent)
                item.keep_backup = False
            except BaseException as recovery:
                try:
                    _retain_backup(item)
                except BaseException as retention:
                    recovery_errors.append(f"could not settle recovery evidence: {retention}")
                if item.backup is not None:
                    evidence = f"restore from {item.backup.path}"
                elif item.prior is None:
                    evidence = f"remove newly seeded {destination.target}"
                else:
                    evidence = "no recovery backup available"
                recovery_errors.append(
                    f"{destination.path}: {type(recovery).__name__}: {recovery}; {evidence}"
                )
        label = (
            "durability unconfirmed; recovery retained"
            if durability_uncertain
            else "compensation failed"
        )
        details = f"; {label}: " + "; ".join(recovery_errors) if recovery_errors else ""
        if isinstance(exc, OSError | SizeLimitsError):
            raise SizeLimitsError(f"could not publish allowlists: {exc}{details}") from exc
        if details:
            exc.add_note(details.lstrip("; "))
        raise
    finally:
        for item in prepared:
            for leftover in (item.temporary, None if item.keep_backup else item.backup):
                if leftover is not None:
                    leftover.discard()


@contextlib.contextmanager
def allowlist_locks(
    paths: Sequence[Path], *, action: str
) -> Iterator[dict[Path, AllowlistDestination]]:
    """Hold per-target OS locks across read/plan/publish; reports create no locks."""

    if action == REPORT:
        yield {}
        return
    with contextlib.ExitStack() as stack:
        try:
            deadline = time.monotonic() + ALLOWLIST_LOCK_TIMEOUT_SECONDS
            targets = {path: _resolve(path) for path in paths}
            parents = {}
            for target in sorted(set(targets.values())):
                _ensure_parent(target.parent)
                parents[target] = _identity(target.parent)
                stack.enter_context(_allowlist_lock(target, deadline=deadline))
            destinations = {}
            for path, target in targets.items():
                if _resolve(path) != target or _identity(target.parent) != parents[target]:
                    raise SizeLimitsError(f"allowlist target changed while locking: {path}")
                destinations[path] = _destination(path, target)
        except OSError as exc:
            raise SizeLimitsError(f"could not lock allowlist: {exc}") from exc
        # Errors from the caller's body propagate unchanged rather than as lock failures.
        yield destinations


@contextlib.contextmanager
def _allowlist_lock(target: Path, *, deadline: float) -> Iterator[None]:
    # Keep the sibling file: unlinking it would let waiters lock different inodes.
    lock = target.with_name(f".{target.name}.lock")
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        parent_identity = _identity(lock.parent)
        if not hasattr(os, "O_NOFOLLOW"):
            raise SizeLimitsError("allowlist locks require O_NOFOLLOW")
        descriptor = os.open(
            lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0), 0o600
        )
    except OSError as exc:
        raise SizeLimitsError(f"could not lock allowlist {target}: {exc}") from exc
    try:
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise SizeLimitsError(f"allowlist lock {lock} must be a single-link regular file")
            # POSIX only: platforms without O_NOFOLLOW (Windows) were refused above,
            # matching size-limits source analysis, which is unsupported there.
            import fcntl

            # Always try once, so an uncontended lock is taken even after the shared
            # deadline was spent waiting on an earlier target.
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise SizeLimitsError(
                            f"timed out acquiring allowlist lock for {target}; another"
                            " seed/update is still running, retry when it finishes"
                        ) from None
                    time.sleep(min(_LOCK_RETRY_SECONDS, remaining))
                else:
                    break
        except OSError as exc:
            raise SizeLimitsError(f"could not lock allowlist {target}: {exc}") from exc
        info = os.fstat(descriptor)
        named = lock.lstat()
        if (
            _identity(lock.parent) != parent_identity
            or not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or info.st_nlink != 1
            or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)
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
    max_source_bytes: int = composition.DEFAULT_MAX_SOURCE_BYTES,
    max_total_source_bytes: int | None = None,
) -> SizeLimitsReport:
    """Measure a project, apply ``action`` to its allowlist, and report the results."""

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
            max_source_bytes=max_source_bytes,
            max_total_source_bytes=max_total_source_bytes,
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
    max_source_bytes: int = composition.DEFAULT_MAX_SOURCE_BYTES,
    max_total_source_bytes: int | None = None,
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

    skips = fallback.DiscoverySkips()
    units, failures = measure(
        root,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
        source_dirs=source_dirs,
        test_dirs=test_dirs,
        max_source_bytes=max_source_bytes,
        max_total_source_bytes=max_total_source_bytes,
        skips=skips if loaded and any(loaded.values()) else None,
    )
    if failures and action != REPORT:
        raise SizeLimitsError(
            f"cannot {action} the allowlist while {len(failures)} file(s) fail to parse"
        )

    matches_filters = partial(
        fallback.matches_path_filters,
        excluded_patterns=fallback.compile_excluded_paths(excluded_paths),
        include_globs=tuple(include_globs),
    )

    def unit_scope(kind: str, path: str) -> tuple[bool, str | None]:
        if not matches_filters(path):
            return False, None
        if (reason := skips.reason(Path(path))) is not None:
            return False, reason
        if (
            kind == "test_files"
            and classify.classify_source_test(path, source_dirs=source_dirs, test_dirs=test_dirs)
            != "tests"
        ):
            return False, "not classified as tests"
        return True, None

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
        allowlist, lowered, dropped = _updated(units, allowlist, settings, unit_scope)

    over_limit, grown, shrunk, stale, out_of_scope = _groups(
        units, allowlist, settings, failures, unit_scope
    )
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
        out_of_scope=tuple(out_of_scope),
        lowered=tuple(lowered),
        dropped=tuple(dropped),
        seeded=tuple(seeded),
        failures=failures,
    )
    return report, allowlist if action in (SEED, UPDATE) else None


def _unit_path(kind: str, key: str) -> str:
    # Paths may contain "::"; qualified definition names and ordinal suffixes cannot.
    return key if kind == "test_files" else key.rsplit("::", 1)[0]


def _groups(
    units: Units,
    allowlist: Units,
    settings: SizeLimitSettings,
    failures: Sequence[CompositionFailure],
    unit_scope: UnitScope,
) -> tuple[list[SizeLimitEntry], ...]:
    failed_paths = {failure.path for failure in failures}
    over_limit: list[SizeLimitEntry] = []
    grown: list[SizeLimitEntry] = []
    shrunk: list[SizeLimitEntry] = []
    stale: list[SizeLimitEntry] = []
    out_of_scope: list[SizeLimitEntry] = []
    for kind in SIZE_LIMIT_KINDS:
        limit = settings.limit(kind)
        recorded_sizes = allowlist.get(kind, {})
        for key, size in sorted(units[kind].items()):
            if key not in recorded_sizes and size > limit:
                over_limit.append(SizeLimitEntry(kind=kind, key=key, size=size, limit=limit))
        for key, recorded in sorted(recorded_sizes.items()):
            path = _unit_path(kind, key)
            if path in failed_paths:
                # The file was not measured, so nothing can be said about this entry.
                continue
            current = units[kind].get(key)
            entry = SizeLimitEntry(kind=kind, key=key, size=current, limit=limit, recorded=recorded)
            if current is None:
                in_scope, reason = unit_scope(kind, path)
                if in_scope:
                    stale.append(dataclasses.replace(entry, reason="missing"))
                else:
                    out_of_scope.append(dataclasses.replace(entry, reason=reason))
            elif current <= limit:
                stale.append(dataclasses.replace(entry, reason="within limit"))
            elif current > recorded:
                grown.append(entry)
            elif current < recorded:
                shrunk.append(entry)
    return over_limit, grown, shrunk, stale, out_of_scope


def _updated(
    units: Units,
    allowlist: Units,
    settings: SizeLimitSettings,
    unit_scope: UnitScope,
) -> tuple[Units, list[SizeLimitEntry], list[SizeLimitEntry]]:
    """Lower shrunk entries and drop stale entries within the selected paths."""

    updated: Units = {}
    lowered: list[SizeLimitEntry] = []
    dropped: list[SizeLimitEntry] = []
    for kind in SIZE_LIMIT_KINDS:
        limit = settings.limit(kind)
        updated[kind] = {}
        for key, recorded in sorted(allowlist.get(kind, {}).items()):
            size = units[kind].get(key)
            if size is None and not unit_scope(kind, _unit_path(kind, key))[0]:
                updated[kind][key] = recorded
                continue
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
