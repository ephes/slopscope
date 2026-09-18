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


def test_a_name_keeps_every_import_binding() -> None:
    result = counts(
        """
        from PySide6.QtWidgets import QLabel as Label
        from local.widgets import Label

        widget = Label()
        """
    )

    assert result.tag("qt") == 2


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
