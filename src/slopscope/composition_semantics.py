"""Semantic detectors for the composition report.

Detectors add overlapping *tags* to code lines, count *marker* lines, and place test-file code
lines. They never change the structural category of a line. Every detector resolves names
through the file's own imports; naming conventions alone never trigger a detector.
"""

from __future__ import annotations

import ast
import re
import tokenize
from collections import Counter
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
    CompositionDetector(name=QT, kind="tag", version=2),
    CompositionDetector(name=LOGGING, kind="tag", version=2),
    CompositionDetector(name=DATA_SHAPE, kind="tag", version=2),
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
DEFAULT_COMPAT_MARKERS = ("legacy", "fallback", "compat")

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
class SemanticSettings:
    """Detector settings: built-in defaults plus ``[tool.slopscope.composition]`` values."""

    qt_modules: tuple[str, ...] = tuple(sorted(QT_MODULES))
    logging_patterns: tuple[str, ...] = ()
    compat_markers: tuple[str, ...] = DEFAULT_COMPAT_MARKERS

    @classmethod
    def configured(
        cls,
        *,
        qt_modules: Iterable[str] = (),
        logging_patterns: Iterable[str] = (),
        compat_markers: Iterable[str] | None = None,
    ) -> SemanticSettings:
        """Add configured Qt modules to the defaults and apply the other settings."""

        return cls(
            qt_modules=tuple(dict.fromkeys((*sorted(QT_MODULES), *qt_modules))),
            logging_patterns=tuple(logging_patterns),
            compat_markers=DEFAULT_COMPAT_MARKERS
            if compat_markers is None
            else tuple(compat_markers),
        )


DEFAULT_SETTINGS = SemanticSettings()


def in_modules(qualified: str, modules: Iterable[str]) -> bool:
    """Return whether a qualified name is one of the modules or lies inside one."""

    return any(qualified == module or qualified.startswith(f"{module}.") for module in modules)


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
        self.modules: set[str] = set()
        for node in nodes:
            self.modules.update(_imported_modules(node))
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

    def names_from(self, modules: Sequence[str]) -> frozenset[str]:
        """Return local names bound to one of the modules or to something inside one."""

        return frozenset(
            name
            for name, qualified_names in self.names.items()
            if any(in_modules(qualified, modules) for qualified in qualified_names)
        )

    def names_above(self, modules: Sequence[str]) -> frozenset[str]:
        """Return local names bound to a package that contains one of the modules.

        ``import app.qt`` binds ``app``; attribute chains such as ``app.qt.Label`` on it must be
        resolved before they can count.
        """

        return frozenset(
            name
            for name, qualified_names in self.names.items()
            if any(
                module.startswith(f"{qualified}.")
                for qualified in qualified_names
                for module in modules
            )
        )

    def imports_any(self, modules: Sequence[str]) -> bool:
        """Return whether the file imports one of the modules or something inside one."""

        return any(in_modules(module, modules) for module in self.modules)


def _scope_nodes(body: Sequence[ast.stmt]) -> list[ast.AST]:
    """Collect one lexical scope without importing bindings from child scopes."""
    nodes: list[ast.AST] = []
    pending: list[ast.AST] = list(body)
    while pending:
        node = pending.pop()
        nodes.append(node)
        if not isinstance(
            node,
            (
                *_FUNCTION_TYPES,
                ast.ClassDef,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            pending.extend(ast.iter_child_nodes(node))
    return nodes


def _scope_bindings(
    nodes: Sequence[ast.AST], inherited: ImportMap, arguments: ast.arguments | None = None
) -> ImportMap:
    imports = ImportMap(n for n in nodes if isinstance(n, ast.Import | ast.ImportFrom))
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in nodes):
        inherited = ImportMap(())
        imports = ImportMap(())
    bindings = ImportMap(())
    bindings.names = {**inherited.names, **imports.names}
    shadowed: set[str] = set()
    for node in nodes:
        if isinstance(node, (*_FUNCTION_TYPES, ast.ClassDef)):
            shadowed.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            shadowed.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del):
            name = dotted_name(node)
            if name is not None:
                shadowed.add(name.split(".", 1)[0])
        elif (
            isinstance(node, ast.ExceptHandler)
            and node.name
            or isinstance(node, ast.MatchAs | ast.MatchStar)
            and node.name
        ):
            shadowed.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            shadowed.add(node.rest)
    if arguments is not None:
        shadowed.update(n.arg for n in ast.walk(arguments) if isinstance(n, ast.arg))
    for name in tuple(bindings.names):
        if name.split(".", 1)[0] in shadowed:
            bindings.names.pop(name, None)
    return bindings


def overload_functions(tree: ast.Module) -> frozenset[int]:
    """Find typing overloads using scoped imports, refusing ambiguous/shadowed bindings.

    This is conservative static resolution: any rebinding in a scope invalidates that
    name throughout the scope. Imports in child scopes never leak into their parent.
    """

    overloads: set[int] = set()
    targets = {"typing.overload", "typing_extensions.overload"}

    def visit_scope(
        body: Sequence[ast.stmt],
        inherited: ImportMap,
        arguments: ast.arguments | None = None,
        *,
        class_scope: bool = False,
    ) -> None:
        nodes: list[ast.AST] = []
        pending: list[ast.AST] = list(body)
        while pending:
            node = pending.pop()
            nodes.append(node)
            if not isinstance(node, (*_FUNCTION_TYPES, ast.ClassDef, ast.Lambda)):
                pending.extend(ast.iter_child_nodes(node))
        bindings = _scope_bindings(nodes, inherited, arguments)
        for node in nodes:
            if isinstance(node, _FUNCTION_TYPES):
                if any(
                    (resolved := bindings.resolve(decorator)) and resolved <= targets
                    for decorator in node.decorator_list
                ):
                    overloads.add(id(node))
                visit_scope(node.body, inherited if class_scope else bindings, node.args)
            elif isinstance(node, ast.ClassDef):
                visit_scope(node.body, inherited if class_scope else bindings, class_scope=True)

    visit_scope(tree.body, ImportMap(()))
    return frozenset(overloads)


def dotted_name(node: ast.AST) -> str | None:
    """Return ``a.b.c`` for a chain of names and attributes, or ``None``."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


class _ScopedImports(ImportMap):
    """Resolve each expression in its lexical environment; class fields stay in their class."""

    def __init__(self, tree: ast.Module) -> None:
        super().__init__(())
        self.scopes: dict[int, ImportMap] = {}
        self._visit(tree.body, ImportMap(()))

    def resolve(self, node: ast.AST) -> frozenset[str]:
        bindings = self.scopes.get(id(node))
        if bindings is not None:
            name = dotted_name(node)
            if name in bindings.names:
                return frozenset(bindings.names[name])
        if isinstance(node, ast.Attribute):
            return frozenset(f"{base}.{node.attr}" for base in self.resolve(node.value))
        return frozenset()

    def _expression(self, node: ast.AST, bindings: ImportMap) -> None:
        self.scopes[id(node)] = bindings
        if isinstance(node, ast.Lambda):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    self._expression(default, bindings)
            local = _scope_bindings((), bindings, node.args)
            self._expression(node.body, local)
        elif isinstance(node, ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
            local = ImportMap(())
            local.names = dict(bindings.names)
            for generator in node.generators:
                self._expression(generator.iter, local)
                targets = {n.id for n in ast.walk(generator.target) if isinstance(n, ast.Name)}
                local = _scope_bindings(list(ast.walk(generator.target)), local)
                for name in tuple(local.names):
                    if name.split(".", 1)[0] in targets:
                        local.names.pop(name, None)
                for condition in generator.ifs:
                    self._expression(condition, local)
            if isinstance(node, ast.DictComp):
                self._expression(node.key, local)
                self._expression(node.value, local)
            else:
                self._expression(node.elt, local)
        else:
            for child in ast.iter_child_nodes(node):
                self._expression(child, bindings)

    def _visit(
        self,
        body: Sequence[ast.stmt],
        inherited: ImportMap,
        arguments: ast.arguments | None = None,
        *,
        class_scope: bool = False,
        fields: dict[str, set[str]] | None = None,
    ) -> None:
        nodes = _scope_nodes(body)
        bindings = _scope_bindings(nodes, inherited, arguments)
        # Tags refuse every ambiguous import and declared cross-scope mutation.
        ambiguous = {name for name, values in bindings.names.items() if len(values) != 1}
        ambiguous.update(
            name for n in nodes if isinstance(n, ast.Global | ast.Nonlocal) for name in n.names
        )
        for binding_name in tuple(bindings.names):
            if binding_name.split(".", 1)[0] in ambiguous:
                bindings.names.pop(binding_name, None)
        if fields:
            rebound = {
                n.id
                for n in nodes
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store | ast.Del)
            }
            bindings.names.update(
                {
                    name: value
                    for name, value in fields.items()
                    if name.split(".", 1)[0] not in rebound
                }
            )
        assignments = [n for n in nodes if isinstance(n, ast.Assign | ast.AnnAssign)]
        # Ordinary rebinding, deletion or a different assignment invalidates a logger alias.
        writes: dict[str, list[ast.AST | None]] = {}
        for node in nodes:
            if isinstance(node, ast.Name | ast.Attribute) and isinstance(
                node.ctx, ast.Store | ast.Del
            ):
                name = dotted_name(node)
                if name is not None:
                    writes.setdefault(name, [])
        for assignment in assignments:
            targets = (
                assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            )
            for target in targets:
                name = dotted_name(target)
                if name is not None:
                    writes.setdefault(name, []).append(assignment.value)
        stores = Counter(
            dotted_name(n)
            for n in nodes
            if isinstance(n, ast.Name | ast.Attribute) and isinstance(n.ctx, ast.Store | ast.Del)
        )
        other_bindings = {n.name for n in nodes if isinstance(n, (*_FUNCTION_TYPES, ast.ClassDef))}
        other_bindings.update(
            ImportMap(n for n in nodes if isinstance(n, ast.Import | ast.ImportFrom)).names
        )
        for node in nodes:
            if isinstance(node, ast.Global | ast.Nonlocal):
                other_bindings.update(node.names)
            elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
                other_bindings.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                other_bindings.add(node.rest)
        if arguments is not None:
            other_bindings.update(n.arg for n in ast.walk(arguments) if isinstance(n, ast.arg))
        for name, values in writes.items():
            bindings.names.pop(name, None)
            # Do not infer through augmented assignment, deletion, or other rebinding.
            if (
                len(values) == 1
                and values[0] is not None
                and _is_logging_getlogger(values[0], bindings)
                and stores[name] == 1
                and name not in other_bindings
            ):
                bindings.names[name] = {"<logging.Logger>"}
        for node in nodes:
            self.scopes[id(node)] = bindings
            if isinstance(node, (*_FUNCTION_TYPES, ast.ClassDef, ast.Lambda)):
                # Decorators/defaults/bases execute in the enclosing environment.
                for field, value in ast.iter_fields(node):
                    if field == "body":
                        continue
                    children = value if isinstance(value, list) else [value]
                    for child in children:
                        if isinstance(child, ast.AST):
                            self._expression(child, bindings)
        for node in nodes:
            if isinstance(
                node, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
            ):
                self._expression(node, bindings)
            elif isinstance(node, _FUNCTION_TYPES):
                self._visit(node.body, inherited if class_scope else bindings, node.args)
            elif isinstance(node, ast.ClassDef):
                outer = inherited if class_scope else bindings
                self._visit(node.body, outer, class_scope=True)
                # Gather instance fields from this class's direct methods only. A field with
                # any non-logger write is ambiguous. Do not share fields with other classes.
                methods = [n for n in _scope_nodes(node.body) if isinstance(n, _FUNCTION_TYPES)]
                field_values: dict[str, list[bool]] = {}
                for method in methods:
                    if not method.args.args:
                        continue
                    receiver = method.args.args[0].arg
                    method_nodes = _scope_nodes(method.body)
                    receiver_rebound = any(
                        isinstance(n, ast.Name)
                        and n.id == receiver
                        and isinstance(n.ctx, ast.Store | ast.Del)
                        for n in method_nodes
                    )
                    for child in method_nodes:
                        if isinstance(child, ast.Attribute) and isinstance(
                            child.ctx, ast.Store | ast.Del
                        ):
                            name = dotted_name(child)
                            if name and name.startswith(receiver + "."):
                                resolved = self.scopes[id(child)].names.get(name, set())
                                field_values.setdefault(name, []).append(
                                    resolved == {"<logging.Logger>"}
                                    and not method.decorator_list
                                    and not receiver_rebound
                                )
                known = {
                    name: {"<logging.Logger>"}
                    for name, values in field_values.items()
                    if all(values)
                }
                for method in methods:
                    if method.args.args and not method.decorator_list:
                        receiver = method.args.args[0].arg
                        own = {
                            name: value
                            for name, value in known.items()
                            if name.startswith(receiver + ".")
                        }
                        self._visit(method.body, outer, method.args, fields=own)


def detect(
    tree: ast.Module,
    *,
    units: Sequence[Unit],
    code_lines: frozenset[int],
    tokens: Sequence[tokenize.TokenInfo],
    first_line: dict[int, int],
    test_file: bool,
    settings: SemanticSettings = DEFAULT_SETTINGS,
) -> SemanticCounts:
    """Run every detector over one parsed file.

    ``first_line`` maps a definition node's ``id()`` to its first line including decorators.
    """

    scan = _Scan(tree)
    imports = ImportMap(scan.imports)
    scoped = _ScopedImports(tree)
    tag_lines: dict[str, set[int]] = {tag: set() for tag in COMPOSITION_TAGS}

    qt_modules = settings.qt_modules
    qt_active = imports.imports_any(qt_modules)
    logging_active = imports.imports_any(("logging",)) or bool(settings.logging_patterns)
    if qt_active or logging_active:
        context = _TagContext(
            imports=scoped,
            qt_modules=qt_modules,
            qt_active=qt_active,
            logging_patterns=settings.logging_patterns,
            logging_active=logging_active,
        )
        for unit in units:
            is_qt, is_logging = _unit_tags(unit.nodes, context)
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
        if qt_active and any(_resolves_into(base, scoped, qt_modules) for base in node.bases):
            qt_classes += 1
        if _is_data_shape(node, scoped):
            data_shape_classes += 1
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign | ast.Assign):
                    tag_lines[DATA_SHAPE].update(
                        line for line in range(stmt.lineno, _end(stmt) + 1) if line in code_lines
                    )

    marker_lines = _compat_marker_lines(tokens, code_lines, settings.compat_markers)

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


def _resolves_into(node: ast.AST, imports: ImportMap, modules: Sequence[str]) -> bool:
    return any(in_modules(qualified, modules) for qualified in imports.resolve(node))


class _Scan:
    """Collect imports, assignments, and classes from one walk over the tree."""

    def __init__(self, tree: ast.Module) -> None:
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.classes: list[ast.ClassDef] = []
        for node in _statements(tree.body):
            if isinstance(node, ast.Import | ast.ImportFrom):
                self.imports.append(node)
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


def _imported_modules(node: ast.Import | ast.ImportFrom) -> set[str]:
    """Return the absolute module names an import statement imports from or binds."""

    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if node.level or not node.module:
        return set()
    return {node.module} | {
        f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
    }


def _is_logging_getlogger(node: ast.AST, imports: ImportMap) -> bool:
    return isinstance(node, ast.Call) and "logging.getLogger" in imports.resolve(node.func)


def _is_logging_call(node: ast.Call, context: _TagContext) -> bool:
    imports = context.imports
    if any(qualified.startswith("logging.") for qualified in imports.resolve(node.func)):
        return True
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in LOGGER_METHODS:
        receiver = func.value
        if "<logging.Logger>" in imports.resolve(receiver) or _is_logging_getlogger(
            receiver, imports
        ):
            return True
    if context.logging_patterns:
        name = dotted_name(func)
        return name is not None and any(pattern in name for pattern in context.logging_patterns)
    return False


@dataclass(frozen=True)
class _TagContext:
    imports: ImportMap
    qt_modules: tuple[str, ...]
    qt_active: bool
    logging_patterns: tuple[str, ...]
    logging_active: bool


def _unit_tags(nodes: Sequence[ast.AST], context: _TagContext) -> tuple[bool, bool]:
    """Return whether a unit references Qt names and whether it makes a logging call."""

    imports = context.imports
    is_qt = False
    is_logging = False
    for node in _walk(nodes):
        if isinstance(node, ast.Name | ast.Attribute):
            is_qt = is_qt or _resolves_into(node, imports, context.qt_modules)
        elif isinstance(node, ast.Call):
            is_logging = is_logging or (context.logging_active and _is_logging_call(node, context))
        elif isinstance(node, ast.Import | ast.ImportFrom):
            modules = _imported_modules(node)
            is_qt = is_qt or any(in_modules(module, context.qt_modules) for module in modules)
            is_logging = is_logging or any(in_modules(module, ("logging",)) for module in modules)
        if (is_qt or not context.qt_active) and (is_logging or not context.logging_active):
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
    words: Sequence[str],
) -> set[int]:
    """Return code lines with an identifier that contains a compatibility marker word."""

    if not words:
        return set()
    pattern = re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE)
    return {
        token.start[0]
        for token in tokens
        if token.type == tokenize.NAME
        and token.start[0] in code_lines
        and pattern.search(token.string)
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
