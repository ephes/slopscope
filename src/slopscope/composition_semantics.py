"""Semantic detectors for the composition report.

Detectors add overlapping *tags* to code lines, count *marker* lines, and place test-file code
lines. They never change the structural category of a line. Every detector resolves names
through the file's own imports; naming conventions alone never trigger a detector.
"""

from __future__ import annotations

import ast
import re
import tokenize
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from slopscope.report import (
    COMPOSITION_MARKERS,
    COMPOSITION_TAGS,
    COMPOSITION_TEST_PLACEMENTS,
    CompositionDetector,
)

QT = "qt"
LOGGING = "logging"
DATA_SHAPE = "data_shape"
COMPAT = "compat"

TEST = "test"
FIXTURE = "fixture"
SETUP = "setup"
MODULE_LEVEL = "module_level"
HELPER_OR_UNKNOWN = "helper_or_unknown"

DETECTORS = (
    CompositionDetector(name=QT, kind="tag", version=1),
    CompositionDetector(name=LOGGING, kind="tag", version=1),
    CompositionDetector(name=DATA_SHAPE, kind="tag", version=1),
    CompositionDetector(name=COMPAT, kind="marker", version=1),
    CompositionDetector(name="test_placement", kind="placement", version=1),
)

QT_MODULES = frozenset({"PySide2", "PySide6", "PyQt5", "PyQt6", "qtpy"})
LOGGER_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
)
DATA_SHAPE_DECORATORS = frozenset(
    {
        "dataclasses.dataclass",
        "attr.s",
        "attr.attrs",
        "attr.define",
        "attr.frozen",
        "attr.mutable",
        "attrs.define",
        "attrs.frozen",
        "attrs.mutable",
    }
)
DATA_SHAPE_BASES = frozenset(
    {
        "enum.Enum",
        "enum.IntEnum",
        "enum.StrEnum",
        "enum.Flag",
        "enum.IntFlag",
        "typing.NamedTuple",
        "typing.TypedDict",
        "typing_extensions.NamedTuple",
        "typing_extensions.TypedDict",
        "pydantic.BaseModel",
    }
)
COMPAT_MARKER = re.compile(r"legacy|fallback|compat", re.IGNORECASE)

PYTEST_FIXTURES = frozenset({"pytest.fixture", "pytest_asyncio.fixture"})
UNITTEST_BASES = frozenset(
    {
        "unittest.TestCase",
        "unittest.IsolatedAsyncioTestCase",
        "django.test.TestCase",
        "django.test.SimpleTestCase",
        "django.test.TransactionTestCase",
        "django.test.LiveServerTestCase",
    }
)
MODULE_SETUP_FUNCTIONS = frozenset(
    {
        "setup_module",
        "teardown_module",
        "setup_function",
        "teardown_function",
        "setUpModule",
        "tearDownModule",
    }
)
PYTEST_CLASS_SETUP_METHODS = frozenset(
    {"setup_method", "teardown_method", "setup_class", "teardown_class", "setup", "teardown"}
)
UNITTEST_SETUP_METHODS = frozenset(
    {"setUp", "tearDown", "setUpClass", "tearDownClass", "asyncSetUp", "asyncTearDown"}
)

_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Unit:
    """Lines claimed by one simple statement or compound-statement header, with its nodes."""

    start: int
    end: int
    nodes: tuple[ast.AST, ...]


@dataclass(frozen=True)
class SemanticCounts:
    """Semantic counts for one file."""

    tags: tuple[int, ...]
    markers: tuple[int, ...]
    placements: tuple[int, ...]
    data_shape_classes: int
    qt_classes: int
    tests: int


class ImportMap:
    """Map local names to every qualified name that the file's imports bind them to.

    Imports anywhere in the file count, regardless of scope, and a name keeps every binding it
    gets: a later import under the same name does not erase an earlier one. Relative imports keep
    their leading dots, so they never resolve to a third-party or standard library module.
    """

    def __init__(self, nodes: Iterable[ast.Import | ast.ImportFrom]) -> None:
        self.names: dict[str, set[str]] = {}
        self.roots: set[str] = set()
        for node in nodes:
            self.roots.update(_import_roots(node))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname is not None:
                        self._bind(alias.asname, alias.name)
                    else:
                        root = alias.name.split(".", 1)[0]
                        self._bind(root, root)
            else:
                module = "." * node.level + (node.module or "")
                for alias in node.names:
                    if alias.name != "*":
                        self._bind(alias.asname or alias.name, f"{module}.{alias.name}")

    def _bind(self, name: str, qualified: str) -> None:
        self.names.setdefault(name, set()).add(qualified)

    def resolve(self, node: ast.AST) -> frozenset[str]:
        """Resolve a name or attribute chain to every qualified name it may refer to."""

        if isinstance(node, ast.Name):
            return frozenset(self.names.get(node.id, ()))
        if isinstance(node, ast.Attribute):
            return frozenset(f"{base}.{node.attr}" for base in self.resolve(node.value))
        return frozenset()

    def names_from(self, modules: Iterable[str]) -> frozenset[str]:
        """Return local names bound from any of the given top-level modules."""

        roots = set(modules)
        return frozenset(
            name
            for name, qualified_names in self.names.items()
            if any(qualified.split(".", 1)[0] in roots for qualified in qualified_names)
        )


def dotted_name(node: ast.AST) -> str | None:
    """Return ``a.b.c`` for a chain of names and attributes, or ``None``."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def detect(
    tree: ast.Module,
    *,
    units: Sequence[Unit],
    code_lines: frozenset[int],
    tokens: Sequence[tokenize.TokenInfo],
    first_line: dict[int, int],
    test_file: bool,
) -> SemanticCounts:
    """Run every detector over one parsed file.

    ``first_line`` maps a definition node's ``id()`` to its first line including decorators.
    """

    scan = _Scan(tree)
    imports = ImportMap(scan.imports)
    tag_lines: dict[str, set[int]] = {tag: set() for tag in COMPOSITION_TAGS}

    qt_names = imports.names_from(QT_MODULES)
    qt_active = bool(imports.roots & QT_MODULES)
    logger_targets = _logger_targets(scan.assignments, imports)
    logging_active = "logging" in imports.roots
    if qt_active or logging_active:
        for unit in units:
            is_qt, is_logging = _unit_tags(
                unit.nodes,
                imports,
                qt_names=qt_names,
                qt_active=qt_active,
                logger_targets=logger_targets,
                logging_active=logging_active,
            )
            if not is_qt and not is_logging:
                continue
            unit_lines = [line for line in range(unit.start, unit.end + 1) if line in code_lines]
            if is_qt:
                tag_lines[QT].update(unit_lines)
            if is_logging:
                tag_lines[LOGGING].update(unit_lines)

    data_shape_classes = 0
    qt_classes = 0
    for node in scan.classes:
        if qt_names and any(_resolves_to_qt(base, imports) for base in node.bases):
            qt_classes += 1
        if _is_data_shape(node, imports):
            data_shape_classes += 1
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign | ast.Assign):
                    tag_lines[DATA_SHAPE].update(
                        line for line in range(stmt.lineno, _end(stmt) + 1) if line in code_lines
                    )

    marker_lines = _compat_marker_lines(tokens, code_lines)

    placements = (0,) * len(COMPOSITION_TEST_PLACEMENTS)
    tests = 0
    if test_file:
        placement_lines, tests = _test_placements(tree, imports, first_line)
        counts = dict.fromkeys(COMPOSITION_TEST_PLACEMENTS, 0)
        for line in code_lines:
            counts[placement_lines.get(line, MODULE_LEVEL)] += 1
        placements = tuple(counts[name] for name in COMPOSITION_TEST_PLACEMENTS)

    return SemanticCounts(
        tags=tuple(len(tag_lines[tag]) for tag in COMPOSITION_TAGS),
        markers=tuple(len(marker_lines) if name == COMPAT else 0 for name in COMPOSITION_MARKERS),
        placements=placements,
        data_shape_classes=data_shape_classes,
        qt_classes=qt_classes,
        tests=tests,
    )


def _end(node: ast.stmt) -> int:
    return node.end_lineno if node.end_lineno is not None else node.lineno


def _walk(nodes: Iterable[ast.AST]) -> Iterator[ast.AST]:
    for node in nodes:
        yield from ast.walk(node)


def _resolves_to_qt(node: ast.AST, imports: ImportMap) -> bool:
    return any(qualified.split(".", 1)[0] in QT_MODULES for qualified in imports.resolve(node))


class _Scan:
    """Collect imports, assignments, and classes from one walk over the tree."""

    def __init__(self, tree: ast.Module) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.assignments: list[ast.Assign | ast.AnnAssign] = []
        self.classes: list[ast.ClassDef] = []
        for node in _statements(tree.body):
            if isinstance(node, ast.Import | ast.ImportFrom):
                self.imports.append(node)
            elif isinstance(node, ast.Assign | ast.AnnAssign):
                self.assignments.append(node)
            elif isinstance(node, ast.ClassDef):
                self.classes.append(node)


def _statements(body: Iterable[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield every statement in a block and its nested blocks, without visiting expressions."""

    stack = list(reversed(list(body)))
    while stack:
        stmt = stack.pop()
        yield stmt
        blocks: list[ast.stmt] = []
        for field in ("body", "orelse", "finalbody"):
            block = getattr(stmt, field, None)
            if isinstance(block, list):
                blocks.extend(block)
        for handler in getattr(stmt, "handlers", ()):
            blocks.extend(handler.body)
        for case in getattr(stmt, "cases", ()):
            blocks.extend(case.body)
        stack.extend(reversed(blocks))


def _import_roots(node: ast.Import | ast.ImportFrom) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.split(".", 1)[0] for alias in node.names}
    if node.level:
        return set()
    return {(node.module or "").split(".", 1)[0]}


def _is_logging_getlogger(node: ast.AST, imports: ImportMap) -> bool:
    return isinstance(node, ast.Call) and "logging.getLogger" in imports.resolve(node.func)


def _logger_targets(
    assignments: Iterable[ast.Assign | ast.AnnAssign],
    imports: ImportMap,
) -> frozenset[str]:
    """Return names and attribute chains assigned from ``logging.getLogger()``."""

    targets: set[str] = set()
    for node in assignments:
        if node.value is None or not _is_logging_getlogger(node.value, imports):
            continue
        candidates = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in candidates:
            name = dotted_name(target)
            if name is not None:
                targets.add(name)
    return frozenset(targets)


def _is_logging_call(node: ast.Call, imports: ImportMap, logger_targets: frozenset[str]) -> bool:
    if any(qualified.startswith("logging.") for qualified in imports.resolve(node.func)):
        return True
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in LOGGER_METHODS:
        receiver = func.value
        return dotted_name(receiver) in logger_targets or _is_logging_getlogger(receiver, imports)
    return False


def _unit_tags(
    nodes: Sequence[ast.AST],
    imports: ImportMap,
    *,
    qt_names: frozenset[str],
    qt_active: bool,
    logger_targets: frozenset[str],
    logging_active: bool,
) -> tuple[bool, bool]:
    """Return whether a unit references Qt names and whether it makes a logging call."""

    is_qt = False
    is_logging = False
    for node in _walk(nodes):
        if isinstance(node, ast.Name):
            is_qt = is_qt or node.id in qt_names
        elif isinstance(node, ast.Call):
            is_logging = is_logging or (
                logging_active and _is_logging_call(node, imports, logger_targets)
            )
        elif isinstance(node, ast.Import | ast.ImportFrom):
            roots = _import_roots(node)
            is_qt = is_qt or bool(roots & QT_MODULES)
            is_logging = is_logging or "logging" in roots
        if (is_qt or not qt_active) and (is_logging or not logging_active):
            break
    return is_qt, is_logging


def _strip_call(node: ast.expr) -> ast.expr:
    return node.func if isinstance(node, ast.Call) else node


def _is_data_shape(node: ast.ClassDef, imports: ImportMap) -> bool:
    if any(
        imports.resolve(_strip_call(decorator)) & DATA_SHAPE_DECORATORS
        for decorator in node.decorator_list
    ):
        return True
    return any(imports.resolve(base) & DATA_SHAPE_BASES for base in node.bases)


def _compat_marker_lines(
    tokens: Sequence[tokenize.TokenInfo],
    code_lines: frozenset[int],
) -> set[int]:
    """Return code lines with an identifier that contains a compatibility marker word."""

    return {
        token.start[0]
        for token in tokens
        if token.type == tokenize.NAME
        and token.start[0] in code_lines
        and COMPAT_MARKER.search(token.string)
    }


def _is_pytest_test_name(name: str) -> bool:
    return name == "test" or name.startswith("test_")


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef, imports: ImportMap) -> bool:
    return any(
        imports.resolve(_strip_call(decorator)) & PYTEST_FIXTURES
        for decorator in node.decorator_list
    )


def _test_placements(
    tree: ast.Module,
    imports: ImportMap,
    first_line: dict[int, int],
) -> tuple[dict[int, str], int]:
    """Place every line of a test file and count test functions and methods.

    Lines not covered by a definition are ``module_level``.
    """

    placements: dict[int, str] = {}
    tests = 0
    unittest_classes: set[str] = set()

    def place(node: ast.stmt, placement: str) -> None:
        start = first_line.get(id(node), node.lineno)
        for line in range(start, _end(node) + 1):
            placements[line] = placement

    for stmt in tree.body:
        if isinstance(stmt, _FUNCTION_TYPES):
            if _is_fixture(stmt, imports):
                place(stmt, FIXTURE)
            elif _is_pytest_test_name(stmt.name):
                place(stmt, TEST)
                tests += 1
            elif stmt.name in MODULE_SETUP_FUNCTIONS:
                place(stmt, SETUP)
            else:
                place(stmt, HELPER_OR_UNKNOWN)
        elif isinstance(stmt, ast.ClassDef):
            place(stmt, HELPER_OR_UNKNOWN)
            is_unittest = any(
                imports.resolve(base) & UNITTEST_BASES
                or (isinstance(base, ast.Name) and base.id in unittest_classes)
                for base in stmt.bases
            )
            if is_unittest:
                unittest_classes.add(stmt.name)
            methods = [child for child in stmt.body if isinstance(child, _FUNCTION_TYPES)]
            is_pytest_class = stmt.name.startswith("Test") and not any(
                method.name == "__init__" for method in methods
            )
            for method in methods:
                if _is_fixture(method, imports):
                    place(method, FIXTURE)
                elif not is_unittest and not is_pytest_class:
                    continue
                elif (
                    is_unittest
                    and method.name.startswith("test")
                    or not is_unittest
                    and _is_pytest_test_name(method.name)
                ):
                    place(method, TEST)
                    tests += 1
                elif method.name in (
                    UNITTEST_SETUP_METHODS if is_unittest else PYTEST_CLASS_SETUP_METHODS
                ):
                    place(method, SETUP)
                else:
                    place(method, HELPER_OR_UNKNOWN)
    return placements, tests
