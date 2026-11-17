"""Regression tests for Python evaluation and binding boundaries in real reports."""

from __future__ import annotations

import sys
import textwrap

import pytest

from slopscope import composition


def analyze(source: str, *, test_file: bool = False) -> composition.FileAnalysis:
    return composition.analyze_source(textwrap.dedent(source).lstrip("\n"), test_file=test_file)


@pytest.mark.parametrize(
    "default",
    [
        "lambda logging: None",
        "lambda x=(lambda logging: None): x",
        "[lambda logging: None for item in ()]",
        "{item: (lambda logging: None) for item in ()}",
    ],
)
def test_signature_inventory_excludes_default_child_parameters(default: str) -> None:
    result = analyze(f'import logging\ndef f(x={default}):\n    logging.info("yes")\n')
    assert result.counts.tag("logging") == 2
    result = analyze(
        "from typing import overload\n"
        f"def f(x={default.replace('logging', 'overload')}):\n"
        "    @overload\n    def stub(): ...\n"
    )
    assert next(f for f in result.functions if f.name == "f.stub").overload


@pytest.mark.parametrize(
    "parameter", ["logging, /", "logging", "*logging", "*, logging", "**logging"]
)
def test_direct_signature_parameters_still_shadow(parameter: str) -> None:
    result = analyze(f'import logging\ndef f({parameter}):\n    logging.info("no")\n')
    assert result.counts.tag("logging") == 1


def test_child_default_parameters_do_not_invalidate_logger_assignment() -> None:
    result = analyze("""
        import logging
        def f(x=lambda logger: None):
            logger = logging.getLogger()
            logger.info("yes")
    """)
    assert result.counts.tag("logging") == 3


@pytest.mark.parametrize(
    "definition",
    [
        "def child(x=(ALIAS := object())): pass",
        "async def child(*, x=(ALIAS := object())): pass",
        "@(ALIAS := ordinary)\ndef child(): pass",
        "class Child((ALIAS := object())): pass",
        "class Child(metaclass=(ALIAS := object())): pass",
        "def child(x=lambda y=(ALIAS := object()): y): pass",
        "value = lambda x=(ALIAS := object()): x",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
def test_definition_headers_invalidate_enclosing_aliases(definition: str, nested: bool) -> None:
    body = definition.replace("ALIAS", "logging") + '\nlogging.info("no")\n'
    if nested:
        body = "def enclosing():\n" + textwrap.indent(body, "    ")
    assert analyze("import logging\n" + body).counts.tag("logging") == 1
    body = definition.replace("ALIAS", "overload") + "\n@overload\ndef stub(): ...\n"
    if nested:
        body = "def enclosing():\n" + textwrap.indent(body, "    ")
    result = analyze("from typing import overload\n" + body)
    assert not next(f for f in result.functions if f.name.endswith("stub")).overload


def test_definition_body_writes_do_not_invalidate_enclosing_aliases() -> None:
    result = analyze("""
        import logging
        from typing import overload
        def child():
            logging = overload = object()
        class Child:
            logging = overload = object()
        logging.info("yes")
        @overload
        def stub(): ...
    """)
    assert result.counts.tag("logging") == 2
    assert next(f for f in result.functions if f.name == "stub").overload


@pytest.mark.parametrize(
    "expression",
    [
        '(logging.info("no"), (logging := object()))',
        '[logging.info("no") for item in () if (logging := object())]',
        '(lambda x=(logging := object()): x, logging.info("no"))',
    ],
)
def test_lambda_body_writes_are_local_throughout_body(expression: str) -> None:
    result = analyze(f'import logging\nf = lambda: {expression}\nlogging.info("yes")\n')
    assert result.counts.tag("logging") == 2


def test_lambda_nested_body_writes_do_not_shadow_outer_lambda() -> None:
    result = analyze("""
        import logging
        f = lambda: (
            logging.info("yes"),
            lambda: (logging := object()),
            [logging for logging in ()],
        )
        logging.info("yes")
    """)
    # The lambda assignment's full physical span is tagged by its one real call.
    assert result.counts.tag("logging") == 7


def test_lambda_defaults_use_the_enclosing_environment() -> None:
    result = analyze("""
        import logging
        f = lambda x=logging.info("default"): (logging := object())
        logging.info("outer")
    """)
    assert result.counts.tag("logging") == 3


@pytest.mark.parametrize(
    "expression",
    [
        '[a for x in () for a in logging.info("no") for logging in ()]',
        '{a for x in () if logging.info("no") for logging in () for a in ()}',
        '{x: a for x in () if logging.info("no") for logging in () for a in ()}',
        '(a for x in () for a in logging.info("no") for logging in ())',
        '[x for x in () if logging.info("no") for (logging, other) in ()]',
    ],
)
def test_all_comprehension_targets_are_lexically_local(expression: str) -> None:
    result = analyze(f'import logging\nvalues = {expression}\nlogging.info("yes")\n')
    assert result.counts.tag("logging") == 2


@pytest.mark.parametrize(
    "expression",
    [
        '[logging for logging in logging.info("yes")]',
        '{logging for logging in logging.info("yes")}',
        '{logging: logging for logging in logging.info("yes")}',
        '(logging for logging in logging.info("yes"))',
    ],
)
def test_comprehension_first_iterable_keeps_enclosing_binding(expression: str) -> None:
    result = analyze(f'import logging\nvalues = {expression}\nlogging.info("outer")\n')
    assert result.counts.tag("logging") == 3


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax needs Python 3.12")
@pytest.mark.parametrize(
    "definition,expected",
    [
        ('def f[logging]():\n    logging.info("no")', 2),
        ('async def f[logging]():\n    logging.info("no")', 2),
        ('def f[*logging]():\n    logging.info("no")', 2),
        ('def f[**logging]():\n    logging.info("no")', 2),
        ('class C[logging]:\n    logging.info("no")', 2),
        ('class C[logging]:\n    def run(self):\n        logging.info("no")', 2),
        ('class C[logging](logging.info("no")): pass', 2),
        ('class C[logging](metaclass=logging.info("no")): pass', 2),
        ('type Alias[logging] = logging.info("no")', 2),
        ('def f[logging, T: logging.info("no")](): pass', 2),
        ('type Alias[logging, T: logging.info("no")] = T', 2),
        ('def f[logging](x: logging.info("no")) -> logging.info("no"): pass', 2),
        ('def f[logging](x=logging.info("yes")):\n    logging.info("no")', 3),
        ('@logging.getLogger()\ndef f[logging]():\n    logging.info("no")', 4),
        ('@logging.getLogger()\nclass C[logging]:\n    logging.info("no")', 4),
        ("def f[T: logging.getLogger()](): pass", 3),
        ("type Alias[T] = logging.getLogger()", 3),
    ],
)
def test_type_parameters_shadow_only_their_lexical_environments(
    definition: str, expected: int
) -> None:
    result = analyze(f'import logging\n{definition}\nlogging.info("outer")\n')
    assert result.counts.tag("logging") == expected


@pytest.mark.skipif(sys.version_info < (3, 13), reason="type parameter defaults need Python 3.13")
@pytest.mark.parametrize("kind", ["def f", "class C", "type Alias"])
def test_type_parameter_defaults_use_type_environment(kind: str) -> None:
    tail = (
        "(): pass" if kind.startswith("def") else ": pass" if kind.startswith("class") else " = T"
    )
    result = analyze(
        f'import logging\n{kind}[logging, T = logging.info("no")]{tail}\nlogging.info("yes")\n'
    )
    assert result.counts.tag("logging") == 2


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax needs Python 3.12")
def test_generic_bases_and_fixture_annotations_do_not_resolve_imports() -> None:
    result = analyze(
        """
        from pydantic import BaseModel
        from pytest import fixture
        @fixture
        def resource[fixture](x: fixture): pass
        class Model[BaseModel](BaseModel): pass
    """,
        test_file=True,
    )
    assert result.counts.construct("data_shape_classes") == 0
    assert result.counts.placement("fixture") == 2


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax needs Python 3.12")
def test_generic_parameters_shadow_nested_overloads_but_not_own_decorators() -> None:
    result = analyze("""
        from typing import overload
        @overload
        def genuine[overload]():
            @overload
            def fake(): ...
        class C[overload]:
            @overload
            def fake(self): ...
            def run(self):
                @overload
                def fake(): ...
        @overload
        def outside(): ...
    """)
    assert {f.name for f in result.functions if f.overload} == {"genuine", "outside"}


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type parameter syntax needs Python 3.12")
def test_generic_annotation_scope_can_use_class_imports_without_leaking_to_methods() -> None:
    result = analyze("""
        class C:
            import logging
            def run[T: logging.getLogger()](self):
                logging.info("no")
            type Alias[T] = logging.getLogger()
    """)
    assert result.counts.tag("logging") == 3
