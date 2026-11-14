from __future__ import annotations

import textwrap

import pytest

from slopscope import composition
from slopscope.report import CompositionCounts


def counts(source: str, *, test_file: bool = False) -> CompositionCounts:
    text = textwrap.dedent(source).lstrip("\n")
    return composition.analyze_source(text, test_file=test_file).counts


def test_qt_tag_requires_qt_imports() -> None:
    result = counts(
        """
        class Panel(QWidget):
            changed = Signal(str)

            def build(self):
                self.setText("x")
                self.changed.connect(self.refresh)
                self.changed.emit("y")
        """
    )

    assert result.tag("qt") == 0
    assert result.construct("qt_classes") == 0


def test_qt_tag_marks_statements_using_names_bound_from_qt_imports() -> None:
    result = counts(
        """
        from PySide6.QtCore import Signal
        from PySide6 import QtWidgets as widgets
        import other


        class Panel(widgets.QWidget):
            changed = Signal(str)

            def build(self):
                label = widgets.QLabel(
                    "text",
                )
                self.setText("x")
                self.changed.connect(self.refresh)
                other.run()
        """
    )

    # Two import lines, the class header, the Signal line, and the three-line QLabel call.
    assert result.tag("qt") == 7
    assert result.construct("qt_classes") == 1


def test_qt_names_from_relative_or_similar_modules_do_not_count() -> None:
    result = counts(
        """
        from .PySide6 import QLabel
        from qtpy_extras import QWidget

        label = QLabel()
        widget = QWidget()
        """
    )

    assert result.tag("qt") == 0


@pytest.mark.parametrize(
    "module",
    [
        "PySide2.QtWidgets",
        "PySide6.QtWidgets",
        "PyQt5.QtWidgets",
        "PyQt6.QtWidgets",
        "qtpy.QtWidgets",
    ],
)
def test_qt_tag_supports_known_bindings(module: str) -> None:
    result = counts(
        f"""
        from {module} import QLabel

        label = QLabel()
        """
    )

    assert result.tag("qt") == 2


def test_logging_tag_uses_logging_module_and_getlogger_names() -> None:
    result = counts(
        """
        import logging
        from logging import getLogger as get_logger

        logger = logging.getLogger(__name__)
        other = get_logger("other")


        class Service:
            def __init__(self):
                self._log = logging.getLogger("service")

            def run(self):
                logger.info("start")
                other.debug(
                    "detail",
                )
                self._log.warning("careful")
                logging.error("module-level")
                logging.getLogger("inline").info("inline")
                self.log.info("not a known logger")
                audit_logger.info("not a known logger")
                print("done")
        """
    )

    # 2 imports, 3 getLogger assignments, 1 + 3 + 1 + 1 + 1 logger call lines.
    assert result.tag("logging") == 12


def test_logging_tag_ignores_logger_like_names_without_logging() -> None:
    result = counts(
        """
        logger = make_logger()
        logger.info("x")
        log_metric("y")
        """
    )

    assert result.tag("logging") == 0


def test_logging_tag_marks_compound_headers_only_for_their_own_expressions() -> None:
    result = counts(
        """
        import logging

        if logging.getLogger().isEnabledFor(10):
            value = compute()
        """
    )

    assert result.tag("logging") == 2


def test_data_shape_tag_resolves_imports_and_aliases() -> None:
    result = counts(
        """
        import dataclasses
        import enum as e
        from dataclasses import dataclass as dc
        from typing import NamedTuple, TypedDict


        @dataclasses.dataclass(frozen=True)
        class Point:
            x: int
            y: int = 0

            def norm(self):
                return abs(self.x)


        @dc
        class Size:
            width: int


        class Color(e.Enum):
            RED = 1
            GREEN = 2


        class Pair(NamedTuple):
            left: int


        class Options(TypedDict):
            name: str
        """
    )

    assert result.construct("data_shape_classes") == 5
    assert result.tag("data_shape") == 7


def test_data_shape_tag_requires_resolved_imports() -> None:
    result = counts(
        """
        @dataclass
        class Point:
            x: int


        class Color(Enum):
            RED = 1
        """
    )

    assert result.construct("data_shape_classes") == 0
    assert result.tag("data_shape") == 0


def test_tags_overlap_and_do_not_change_categories() -> None:
    source = """
        import logging
        from dataclasses import dataclass

        LOG = logging.getLogger(__name__)


        @dataclass
        class Config:
            level: int = logging.INFO
        """
    plain = counts(source.replace("import logging\n", "import os\n"))
    tagged = counts(source)

    assert tagged.categories == plain.categories
    assert tagged.tag("logging") == 2
    assert tagged.tag("data_shape") == 1


def test_compat_marker_counts_identifier_lines_only() -> None:
    result = counts(
        '''
        """Legacy module docstring."""
        # fallback comment
        def load_legacy_config():
            value = "compat string"
            return use_fallback(
                value,
            )
        '''
    )

    assert result.marker("compat") == 2


def test_placement_is_only_computed_for_test_files() -> None:
    result = counts("def test_x():\n    assert True\n")

    assert sum(result.placements) == 0
    assert result.construct("tests") == 0


def test_pytest_placement_rules() -> None:
    result = counts(
        """
        import pytest
        from pytest import fixture

        pytestmark = pytest.mark.slow


        @pytest.fixture
        def client():
            return make_client()


        @fixture(scope="module")
        def test_data():
            return {}


        def test_create(client):
            assert client


        @pytest.mark.parametrize("value", [1, 2])
        def test_values(value):
            assert value


        def testing_helper():
            return 1


        def setup_module():
            pass


        def build_payload():
            return {}


        class TestGroup:
            value = 1

            def setup_method(self):
                self.value = 2

            def test_member(self):
                assert self.value

            def helper(self):
                return 3


        class TestNotCollected:
            def __init__(self):
                pass

            def test_member(self):
                assert True
        """,
        test_file=True,
    )

    placement = result.placement_mapping()
    # test_create, test_values, and TestGroup.test_member; a class with __init__ is skipped.
    assert result.construct("tests") == 3
    assert placement == {
        "test": 2 + 3 + 2,
        "fixture": 3 + 3,
        "setup": 2 + 2,
        "module_level": 3,
        "helper_or_unknown": 2 + 2 + 2 + 2 + 5,
    }
    assert sum(placement.values()) == result.code


def test_unittest_placement_rules_resolve_testcase_bases() -> None:
    result = counts(
        """
        import unittest
        from unittest import TestCase as Base


        class ServiceTests(unittest.TestCase):
            def setUp(self):
                self.value = 1

            def testValue(self):
                self.assertEqual(self.value, 1)

            def helper(self):
                return 2


        class MoreTests(ServiceTests):
            def test_more(self):
                pass


        class AliasTests(Base):
            def test_alias(self):
                pass


        class TestCase:
            def test_fake(self):
                pass
        """,
        test_file=True,
    )

    placement = result.placement_mapping()
    # ``TestCase`` defined locally is not unittest's, but ``Test*`` makes it a pytest class.
    assert result.construct("tests") == 4
    assert placement["setup"] == 2
    assert placement["test"] == 2 + 2 + 2 + 2
    assert placement["module_level"] == 2
    assert placement["helper_or_unknown"] == 1 + 2 + 1 + 1 + 1
    assert sum(placement.values()) == result.code


def test_ambiguous_import_binding_tags_only_the_import() -> None:
    result = counts(
        """
        from PySide6.QtWidgets import QLabel as Label
        from local.widgets import Label

        widget = Label()
        """
    )

    assert result.tag("qt") == 1


def test_star_imports_tag_their_own_line() -> None:
    result = counts(
        """
        from PySide6.QtWidgets import *
        from logging import *

        label = QLabel()
        """
    )

    assert result.tag("qt") == 1
    assert result.tag("logging") == 1


def test_fixture_methods_in_any_top_level_class_are_fixtures() -> None:
    result = counts(
        """
        import pytest


        class Helpers:
            @pytest.fixture
            def client(self):
                return 1

            def build(self):
                return 2
        """,
        test_file=True,
    )

    assert result.placement("fixture") == 3
    assert result.placement("helper_or_unknown") == 3


@pytest.mark.parametrize("binding", ["parameter", "assignment", "child-import"])
def test_semantic_tags_exclude_shadowed_or_child_bindings(binding: str) -> None:
    imports = (
        "import logging\nfrom dataclasses import dataclass as dc\n"
        "from qtpy import QWidget as Widget\n"
    )
    if binding == "parameter":
        source = imports + "def f(logging, dc, Widget):\n"
    elif binding == "assignment":
        source = imports + "def f():\n    logging = dc = Widget = object()\n"
    else:
        source = "def unrelated():\n" + textwrap.indent(imports, "    ") + "def f():\n"
    source += (
        '    logging.info("x")\n    @dc\n    class Model(Widget):\n'
        "        field: int\n    return Widget()\n"
    )
    result = counts(source)
    assert result.tag("logging") == 1  # Only the explicit import.
    assert result.tag("qt") == 1
    assert result.tag("data_shape") == 0
    assert result.construct("qt_classes") == 0
    assert result.construct("data_shape_classes") == 0


def test_logger_fields_do_not_leak_to_unrelated_classes_or_rebound_names() -> None:
    result = counts("""
        import logging as log
        logger = log.getLogger("module")
        def shadow(logger):
            logger.info("not logging")
        class Service:
            def __init__(self):
                self.logger = log.getLogger("service")
            def run(self):
                self.logger.info("logging")
        class Other:
            def run(self):
                self.logger.info("not logging")
        def rebound():
            local = log.getLogger("local")
            local = object()
            local.info("not logging")
    """)
    assert result.tag("logging") == 5


def test_nested_import_aliases_stay_local_and_remain_resolvable() -> None:
    result = counts("""
        def real():
            import logging as log
            from dataclasses import dataclass as dc
            from qtpy import QWidget as Widget
            log.info("yes")
            @dc
            class Model(Widget):
                value: int
            return Widget()
        def unrelated():
            log.info("no")
            @dc
            class Other(Widget):
                value: int
    """)
    assert result.tag("logging") == 2
    assert result.tag("qt") == 4
    assert result.tag("data_shape") == 1
    assert result.construct("qt_classes") == 1
    assert result.construct("data_shape_classes") == 1


def test_lambda_and_comprehension_bindings_do_not_leak() -> None:
    result = counts("""
        import logging
        shadow = lambda logging: logging.info("no")
        real = lambda: logging.info("yes")
        values = [logging.info("no") for logging in objects]
        others = [logging.info("yes") for item in objects]
        logging.info("yes")
    """)
    assert result.tag("logging") == 4


@pytest.mark.parametrize(
    "rebind",
    [
        "import other as logger",
        "try:\n    pass\nexcept Exception as logger:\n    pass",
        "match value:\n    case logger:\n        pass",
    ],
)
def test_logger_assignment_with_another_binding_is_ambiguous(rebind: str) -> None:
    source = "import logging\nlogger = logging.getLogger()\n" + rebind + '\nlogger.info("no")\n'
    assert counts(source).tag("logging") == 2


@pytest.mark.parametrize(
    "method",
    [
        "@property\ndef reset(self):\n    self.logger = None\n",
        "def setup(self):\n    self = other\n    self.logger = logging.getLogger()\n",
    ],
)
def test_ambiguous_instance_fields_are_not_shared(method: str) -> None:
    result = counts(
        "import logging\nclass Service:\n"
        + textwrap.indent(method, "    ")
        + '    def run(self):\n        self.logger.info("no")\n'
    )
    assert result.tag("logging") == (2 if "getLogger" in method else 1)
