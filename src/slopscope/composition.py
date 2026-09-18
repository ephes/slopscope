"""Python composition analysis: which structural kind of line each physical line is.

The composition report is a separate, Python-only mode. It reuses fallback file discovery and
path classification, parses each discovered Python file with the standard library ``ast`` and
``tokenize`` modules, and assigns every physical line exactly one structural category.
"""

from __future__ import annotations

import ast
import dataclasses
import io
import platform
import re
import tokenize
import warnings
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from slopscope import classify, composition_duplication, composition_semantics, fallback
from slopscope.composition_semantics import Unit
from slopscope.report import (
    COMPOSITION_CATEGORIES,
    COMPOSITION_CONSTRUCTS,
    CompositionAggregate,
    CompositionClassRow,
    CompositionCounts,
    CompositionDetector,
    CompositionDuplicateBlock,
    CompositionDuplicateOccurrence,
    CompositionFailure,
    CompositionFileRow,
    CompositionFunctionRow,
    CompositionReport,
    CompositionSettings,
)

ANALYZER_NAME = "slopscope.composition"
ANALYZER_VERSION = 1
SCHEMA_VERSION = 4
DEFAULT_LIMIT = 10
COMPOSITION_LANGUAGE = "Python"
SOURCE_TEST_KINDS = ("source", "tests", "other")

BLANK = "blank"
COMMENT = "comment"
DOCSTRING = "docstring"
IMPORT = "import"
DEFINITION = "definition"
ASSERTION = "assertion"
ERROR_HANDLING = "error_handling"
LITERAL_DATA = "literal_data"
OTHER_CODE = "other_code"

MIN_LITERAL_LINES = 2

_CATEGORY_INDEX = {name: index for index, name in enumerate(COMPOSITION_CATEGORIES)}
_DEFINITION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)
_LITERAL_TYPES = (ast.Dict, ast.List, ast.Tuple, ast.Set)
_LITERAL_STATEMENT_TYPES = (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Return, ast.Expr)
_ELIF = re.compile(r"elif\b")
_STRING_START_TYPES = frozenset(
    token_type
    for name in ("FSTRING_START", "TSTRING_START")
    if (token_type := getattr(tokenize, name, None)) is not None
)
_STRING_END_TYPES = frozenset(
    token_type
    for name in ("FSTRING_END", "TSTRING_END")
    if (token_type := getattr(tokenize, name, None)) is not None
)


DETECTORS: tuple[CompositionDetector, ...] = (
    *composition_semantics.DETECTORS,
    CompositionDetector(
        name="duplication", kind="duplication", version=composition_duplication.DUPLICATION_VERSION
    ),
)


class CompositionAnalysisError(Exception):
    """A file could not be read, decoded, tokenized, or parsed."""


@dataclass(frozen=True)
class DefinitionSize:
    """Span of one class or function definition inside a file."""

    name: str
    line: int
    lines: int
    methods: int | None = None


@dataclass(frozen=True)
class FileAnalysis:
    """Composition result for one parsed file, before path classification."""

    counts: CompositionCounts
    line_categories: tuple[str, ...]
    classes: tuple[DefinitionSize, ...]
    functions: tuple[DefinitionSize, ...]
    segments: tuple[composition_duplication.Segment, ...] = ()


def python_version() -> str:
    """Return the interpreter version whose ``ast`` grammar parses files."""

    return platform.python_version()


def analyze_source(
    source: str,
    *,
    path: str = "<source>",
    test_file: bool = False,
    interner: dict[str, int] | None = None,
    settings: composition_semantics.SemanticSettings = composition_semantics.DEFAULT_SETTINGS,
) -> FileAnalysis:
    """Classify every physical line of Python source text and run the semantic detectors.

    ``source`` must already use ``\\n`` line endings, as produced by ``tokenize.open()``.
    Test placement is computed only when ``test_file`` is true. Token segments for duplicate
    detection are built only when an ``interner`` is passed.
    Raises :class:`CompositionAnalysisError` when the source cannot be parsed or tokenized.
    """

    lines = _physical_lines(source)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            warnings.simplefilter("ignore", DeprecationWarning)
            tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        raise CompositionAnalysisError(_syntax_error_message(exc)) from exc
    except (ValueError, RecursionError, MemoryError) as exc:
        raise CompositionAnalysisError(f"parse error: {exc}") from exc

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError) as exc:
        raise CompositionAnalysisError(f"tokenize error: {exc}") from exc

    classifier = _LineClassifier(lines)
    classifier.mark_blank_and_comment_lines(tokens)
    classifier.visit_body(tree.body, allow_docstring=True)
    analysis = classifier.result(tree, tokens, test_file=test_file, settings=settings)
    if interner is None:
        return analysis
    segments = composition_duplication.token_segments(tokens, analysis.line_categories, interner)
    return dataclasses.replace(analysis, segments=segments)


def analyze_file(
    root: Path,
    relative_path: str,
    *,
    test_file: bool = False,
    interner: dict[str, int] | None = None,
    settings: composition_semantics.SemanticSettings = composition_semantics.DEFAULT_SETTINGS,
) -> FileAnalysis:
    """Read one file with PEP 263 encoding detection and classify its lines."""

    file_path = root / relative_path
    try:
        with tokenize.open(file_path) as handle:
            source = handle.read()
    except UnicodeDecodeError as exc:
        raise CompositionAnalysisError(f"decode error: {exc}") from exc
    except SyntaxError as exc:
        # tokenize.open() reports unknown or conflicting encoding declarations as SyntaxError.
        raise CompositionAnalysisError(f"decode error: {exc.msg}") from exc
    except OSError as exc:
        raise CompositionAnalysisError(f"I/O error: {exc.strerror or exc}") from exc
    return analyze_source(
        source, path=relative_path, test_file=test_file, interner=interner, settings=settings
    )


def discover_python_files(
    path: Path | str,
    *,
    excluded_paths: Iterable[str],
    include_globs: Iterable[str] = (),
) -> list[str]:
    """Discover Python files with the fallback discovery rules, sorted by relative path."""

    root = Path(path)
    discovered = fallback.discover_files(
        root,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
    )
    relative_paths = {
        relative_path.as_posix()
        for relative_path in discovered
        if fallback.map_language(relative_path) == COMPOSITION_LANGUAGE
        # Tracked paths missing from the working tree are skipped, as in the default report.
        and (root / relative_path).is_file()
    }
    return sorted(relative_paths)


def build_composition_report(
    path: Path | str,
    *,
    excluded_paths: Sequence[str],
    include_globs: Sequence[str] = (),
    source_dirs: Sequence[str] = classify.DEFAULT_SOURCE_DIRS,
    test_dirs: Sequence[str] = classify.DEFAULT_TEST_DIRS,
    named_areas: Sequence[str] = classify.DEFAULT_NAMED_AREAS,
    limit: int = DEFAULT_LIMIT,
    min_duplicate_tokens: int = composition_duplication.DEFAULT_MIN_TOKENS,
    semantic_settings: composition_semantics.SemanticSettings = (
        composition_semantics.DEFAULT_SETTINGS
    ),
) -> CompositionReport:
    """Discover, analyze, classify, and aggregate Python files below a path."""

    root = Path(path)
    interner: dict[str, int] = {}
    analyses: list[FileAnalysis] = []
    file_rows: list[CompositionFileRow] = []
    classes: list[CompositionClassRow] = []
    functions: list[CompositionFunctionRow] = []
    failures: list[CompositionFailure] = []

    for relative_path in discover_python_files(
        root,
        excluded_paths=excluded_paths,
        include_globs=include_globs,
    ):
        kind = classify.classify_source_test(
            relative_path, source_dirs=source_dirs, test_dirs=test_dirs
        )
        try:
            analysis = analyze_file(
                root,
                relative_path,
                test_file=kind == "tests",
                interner=interner,
                settings=semantic_settings,
            )
        except CompositionAnalysisError as exc:
            failures.append(CompositionFailure(path=relative_path, error=str(exc)))
            continue

        area = classify.classify_area(
            relative_path,
            source_dirs=source_dirs,
            test_dirs=test_dirs,
            named_areas=named_areas,
        )
        analyses.append(analysis)
        file_rows.append(
            CompositionFileRow(path=relative_path, kind=kind, area=area, counts=analysis.counts)
        )
        classes.extend(
            CompositionClassRow(
                path=relative_path,
                name=row.name,
                line=row.line,
                lines=row.lines,
                methods=row.methods or 0,
                kind=kind,
            )
            for row in analysis.classes
        )
        functions.extend(
            CompositionFunctionRow(
                path=relative_path,
                name=row.name,
                line=row.line,
                lines=row.lines,
                kind=kind,
            )
            for row in analysis.functions
        )

    duplication = composition_duplication.find_duplicates(
        [analysis.segments for analysis in analyses], min_tokens=min_duplicate_tokens
    )
    for index, spans in duplication.intervals.items():
        categories = analyses[index].line_categories
        duplicated = sum(
            1
            for start, end in spans
            for line in range(start, end + 1)
            if categories[line - 1] not in (BLANK, COMMENT, DOCSTRING)
        )
        row = file_rows[index]
        file_rows[index] = dataclasses.replace(
            row, counts=dataclasses.replace(row.counts, duplicated_lines=duplicated)
        )
    duplicates = sorted(
        (
            CompositionDuplicateBlock(
                tokens=block.tokens,
                occurrences=tuple(
                    CompositionDuplicateOccurrence(
                        path=file_rows[occurrence.file].path,
                        start_line=occurrence.start_line,
                        end_line=occurrence.end_line,
                        kind=file_rows[occurrence.file].kind,
                    )
                    for occurrence in block.occurrences
                ),
            )
            for block in duplication.blocks
        ),
        key=lambda block: (
            -block.lines,
            -len(block.occurrences),
            -block.tokens,
            block.occurrences[0].path,
            block.occurrences[0].start_line,
        ),
    )

    rows = tuple(file_rows)
    return CompositionReport(
        path=root,
        analyzer=ANALYZER_NAME,
        analyzer_version=ANALYZER_VERSION,
        schema_version=SCHEMA_VERSION,
        python_version=python_version(),
        detectors=DETECTORS,
        settings=CompositionSettings(
            limit=limit,
            min_duplicate_tokens=min_duplicate_tokens,
            qt_modules=semantic_settings.qt_modules,
            logging_patterns=semantic_settings.logging_patterns,
            compat_markers=semantic_settings.compat_markers,
            excluded_paths=tuple(excluded_paths),
            include_globs=tuple(include_globs),
            source_dirs=tuple(source_dirs),
            test_dirs=tuple(test_dirs),
            areas=tuple(named_areas),
        ),
        total=_aggregate("total", rows),
        kinds=tuple(
            _aggregate(kind, [row for row in rows if row.kind == kind])
            for kind in SOURCE_TEST_KINDS
        ),
        areas=_aggregate_areas(rows),
        files=rows,
        largest_modules=tuple(sorted(rows, key=lambda row: (-row.counts.code, row.path))[:limit]),
        largest_classes=tuple(
            sorted(classes, key=lambda row: (-row.lines, row.path, row.line, row.name))[:limit]
        ),
        largest_functions=tuple(
            sorted(functions, key=lambda row: (-row.lines, row.path, row.line, row.name))[:limit]
        ),
        duplicates=tuple(duplicates[:limit]),
        failures=tuple(failures),
    )


def _aggregate(name: str, rows: Sequence[CompositionFileRow]) -> CompositionAggregate:
    return CompositionAggregate(
        name=name,
        files=len(rows),
        counts=CompositionCounts.sum(row.counts for row in rows),
    )


def _aggregate_areas(rows: Sequence[CompositionFileRow]) -> tuple[CompositionAggregate, ...]:
    by_area: dict[str, list[CompositionFileRow]] = {}
    for row in rows:
        by_area.setdefault(row.area, []).append(row)
    aggregates = [_aggregate(area, area_rows) for area, area_rows in by_area.items()]
    return tuple(
        sorted(
            aggregates,
            key=lambda aggregate: (-aggregate.counts.code, -aggregate.files, aggregate.name),
        )
    )


def _physical_lines(source: str) -> list[str]:
    """Split newline-normalized source into physical lines.

    A missing final newline still counts the last line; an empty file has no lines.
    """

    if not source:
        return []
    lines = source.split("\n")
    if source.endswith("\n"):
        lines.pop()
    return lines


def _syntax_error_message(exc: SyntaxError) -> str:
    location = f" (line {exc.lineno})" if exc.lineno is not None else ""
    return f"syntax error: {exc.msg}{location}"


def _end_line(node: ast.stmt | ast.expr | ast.pattern) -> int:
    return node.end_lineno if node.end_lineno is not None else node.lineno


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _is_multiline_literal(node: ast.expr | None) -> bool:
    if node is None or _end_line(node) - node.lineno + 1 < MIN_LITERAL_LINES:
        return False
    if isinstance(node, _LITERAL_TYPES):
        return True
    return isinstance(node, ast.Constant) and isinstance(node.value, str | bytes)


def _header_nodes(stmt: ast.stmt) -> tuple[ast.AST, ...]:
    """Return the expressions in a compound statement's header, excluding its blocks."""

    nodes: list[ast.AST] = []
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        nodes.extend(stmt.decorator_list)
        nodes.append(stmt.args)
        if stmt.returns is not None:
            nodes.append(stmt.returns)
    elif isinstance(stmt, ast.ClassDef):
        nodes.extend(stmt.decorator_list)
        nodes.extend(stmt.bases)
        nodes.extend(stmt.keywords)
    elif isinstance(stmt, ast.If | ast.While):
        nodes.append(stmt.test)
    elif isinstance(stmt, ast.For | ast.AsyncFor):
        nodes.extend((stmt.target, stmt.iter))
    elif isinstance(stmt, ast.With | ast.AsyncWith):
        nodes.extend(stmt.items)
    nodes.extend(getattr(stmt, "type_params", ()))
    return tuple(nodes)


def _simple_statement_category(stmt: ast.stmt) -> str:
    if isinstance(stmt, ast.Import | ast.ImportFrom):
        return IMPORT
    if isinstance(stmt, ast.Assert):
        return ASSERTION
    if isinstance(stmt, ast.Raise):
        return ERROR_HANDLING
    if isinstance(stmt, _LITERAL_STATEMENT_TYPES) and _is_multiline_literal(stmt.value):
        return LITERAL_DATA
    return OTHER_CODE


class _LineClassifier:
    """Assign one category per physical line.

    Blank and comment-only lines are marked first. Statements are then visited outside-in and
    in source order; a line keeps the first category it is given, so a line shared by several
    statements or clauses belongs to the leftmost one.
    """

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.categories: list[str | None] = [None] * (len(lines) + 1)
        self.begin_lines: set[int] = set()
        self.statements = 0
        self.scope: list[str] = []
        self.classes: list[DefinitionSize] = []
        self.functions: list[DefinitionSize] = []
        self.units: list[Unit] = []
        self.first_lines: dict[int, int] = {}

    def mark_blank_and_comment_lines(self, tokens: Sequence[tokenize.TokenInfo]) -> None:
        string_interior: set[int] = set()
        comment_lines: set[int] = set()
        open_strings: list[int] = []
        for token in tokens:
            if token.type == tokenize.COMMENT:
                row, column = token.start
                if row <= len(self.lines) and not self.lines[row - 1][:column].strip():
                    comment_lines.add(row)
            elif token.type == tokenize.STRING:
                string_interior.update(range(token.start[0] + 1, token.end[0] + 1))
            elif token.type in _STRING_START_TYPES:
                open_strings.append(token.start[0])
            elif token.type in _STRING_END_TYPES and open_strings:
                string_interior.update(range(open_strings.pop() + 1, token.end[0] + 1))

        for lineno, text in enumerate(self.lines, start=1):
            if lineno in string_interior:
                continue
            if not text.strip():
                self.categories[lineno] = BLANK
            elif lineno in comment_lines:
                self.categories[lineno] = COMMENT

    def result(
        self,
        tree: ast.Module,
        tokens: Sequence[tokenize.TokenInfo],
        *,
        test_file: bool,
        settings: composition_semantics.SemanticSettings,
    ) -> FileAnalysis:
        counts = [0] * len(COMPOSITION_CATEGORIES)
        line_categories: list[str] = []
        continuation_lines = 0
        for lineno in range(1, len(self.lines) + 1):
            category = self.categories[lineno] or OTHER_CODE
            line_categories.append(category)
            counts[_CATEGORY_INDEX[category]] += 1
            if category not in (BLANK, COMMENT, DOCSTRING) and lineno not in self.begin_lines:
                continuation_lines += 1
        code_lines = frozenset(
            lineno
            for lineno, category in enumerate(line_categories, start=1)
            if category not in (BLANK, COMMENT, DOCSTRING)
        )
        semantics = composition_semantics.detect(
            tree,
            units=self.units,
            code_lines=code_lines,
            tokens=tokens,
            first_line=self.first_lines,
            test_file=test_file,
            settings=settings,
        )
        constructs = {
            "classes": len(self.classes),
            "functions": len(self.functions),
            "data_shape_classes": semantics.data_shape_classes,
            "qt_classes": semantics.qt_classes,
            "tests": semantics.tests,
        }
        return FileAnalysis(
            counts=CompositionCounts(
                categories=tuple(counts),
                statements=self.statements,
                continuation_lines=continuation_lines,
                tags=semantics.tags,
                markers=semantics.markers,
                placements=semantics.placements,
                constructs=tuple(constructs[name] for name in COMPOSITION_CONSTRUCTS),
            ),
            line_categories=tuple(line_categories),
            classes=tuple(self.classes),
            functions=tuple(self.functions),
        )

    def claim(self, start: int, end: int, category: str) -> None:
        for lineno in range(max(start, 1), min(end, len(self.lines)) + 1):
            if self.categories[lineno] is None:
                self.categories[lineno] = category

    def visit_body(self, body: Sequence[ast.stmt], *, allow_docstring: bool = False) -> None:
        for index, stmt in enumerate(body):
            if allow_docstring and index == 0 and _is_docstring(stmt):
                self.statements += 1
                self.begin_lines.add(stmt.lineno)
                self.claim(stmt.lineno, _end_line(stmt), DOCSTRING)
                continue
            self.visit(stmt)

    def visit(self, stmt: ast.stmt) -> None:
        self.statements += 1
        self.begin_lines.add(stmt.lineno)
        end = _end_line(stmt)

        if isinstance(stmt, _DEFINITION_TYPES):
            self.visit_definition(stmt)
            return

        if isinstance(stmt, ast.Match):
            first_case_line = stmt.cases[0].pattern.lineno if stmt.cases else end + 1
            self.claim(stmt.lineno, first_case_line - 1, OTHER_CODE)
            self.units.append(Unit(stmt.lineno, first_case_line - 1, (stmt.subject,)))
            for case in stmt.cases:
                self.begin_lines.add(case.pattern.lineno)
                case_nodes: tuple[ast.AST, ...] = (case.pattern,)
                if case.guard is not None:
                    case_nodes += (case.guard,)
                self.claim_header(case.pattern.lineno, case.body, OTHER_CODE, case_nodes)
                self.visit_body(case.body)
            self.claim(stmt.lineno, end, OTHER_CODE)
            return

        body = getattr(stmt, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.stmt):
            self.claim_header(stmt.lineno, body, OTHER_CODE, _header_nodes(stmt))
            self.visit_body(body)
            for handler in getattr(stmt, "handlers", ()):
                self.begin_lines.add(handler.lineno)
                handler_nodes = () if handler.type is None else (handler.type,)
                self.claim_header(handler.lineno, handler.body, ERROR_HANDLING, handler_nodes)
                self.visit_body(handler.body)
            self.visit_clause(stmt, getattr(stmt, "orelse", []))
            self.visit_clause(stmt, getattr(stmt, "finalbody", []))
            # Any remaining lines of the statement belong to its own clauses.
            self.claim(stmt.lineno, end, OTHER_CODE)
            return

        self.claim(stmt.lineno, end, _simple_statement_category(stmt))
        self.units.append(Unit(stmt.lineno, end, (stmt,)))

    def visit_definition(
        self,
        stmt: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        first = self._first_line(stmt)
        end = _end_line(stmt)
        self.begin_lines.update(self._decorator_lines(stmt))
        self.first_lines[id(stmt)] = first
        self.claim_header(first, stmt.body, DEFINITION, _header_nodes(stmt))

        qualified_name = ".".join((*self.scope, stmt.name))
        if isinstance(stmt, ast.ClassDef):
            self.classes.append(
                DefinitionSize(
                    name=qualified_name,
                    line=first,
                    lines=end - first + 1,
                    methods=sum(isinstance(child, _FUNCTION_TYPES) for child in stmt.body),
                )
            )
        else:
            self.functions.append(
                DefinitionSize(name=qualified_name, line=first, lines=end - first + 1)
            )

        self.scope.append(stmt.name)
        self.visit_body(stmt.body, allow_docstring=True)
        self.scope.pop()

    def visit_clause(self, stmt: ast.stmt, clause: Sequence[ast.stmt]) -> None:
        if not clause:
            return
        first_line = self._first_line(clause[0])
        # ``elif`` is an ``If`` node in ``orelse`` whose own line starts with ``elif``.
        is_elif = (
            isinstance(stmt, ast.If)
            and isinstance(clause[0], ast.If)
            and _ELIF.match(self.lines[clause[0].lineno - 1].lstrip()) is not None
        )
        if not is_elif:
            keyword_line = self._clause_keyword_line(clause[0], first_line)
            self.begin_lines.add(keyword_line)
            self.claim_header(keyword_line, clause, OTHER_CODE)
        self.visit_body(clause)

    def claim_header(
        self,
        start: int,
        body: Sequence[ast.stmt],
        category: str,
        nodes: tuple[ast.AST, ...] = (),
    ) -> None:
        """Claim header lines from ``start`` up to the line before the block.

        When the first block statement shares its line with the header (``if x: y``), that line
        belongs to the header. ``nodes`` are the header's own expressions, for the detectors.
        """

        first_body_line = self._first_line(body[0])
        header_end = first_body_line if self._shares_line(body[0]) else first_body_line - 1
        self.claim(start, max(start, header_end), category)
        if nodes:
            self.units.append(Unit(start, max(start, header_end), nodes))

    def _first_line(self, stmt: ast.stmt) -> int:
        """Return the first physical line of a statement, including decorators."""

        return min([stmt.lineno, *self._decorator_lines(stmt)])

    def _decorator_lines(self, stmt: ast.stmt) -> list[int]:
        """Return the ``@`` line of each decorator.

        A decorator expression's location excludes surrounding parentheses, so ``@(`` may sit
        above the expression's first line. Only brackets, blank lines, and comments can come
        between the ``@`` and the expression, so the nearest line starting with ``@`` is it.
        """

        if not isinstance(stmt, _DEFINITION_TYPES):
            return []
        lines: list[int] = []
        for decorator in stmt.decorator_list:
            lineno = decorator.lineno
            while lineno > 1 and not self.lines[lineno - 1].lstrip().startswith("@"):
                lineno -= 1
            lines.append(lineno)
        return lines

    def _shares_line(self, stmt: ast.stmt) -> bool:
        if isinstance(stmt, _DEFINITION_TYPES) and stmt.decorator_list:
            return False
        line = self.lines[stmt.lineno - 1].encode("utf-8")
        return bool(line[: stmt.col_offset].strip())

    def _clause_keyword_line(self, first_stmt: ast.stmt, first_line: int) -> int:
        """Find the ``else:``/``finally:`` line that opens a clause block."""

        if self._shares_line(first_stmt):
            return first_line
        lineno = first_line - 1
        while lineno > 1 and self.categories[lineno] in (BLANK, COMMENT):
            lineno -= 1
        return lineno
