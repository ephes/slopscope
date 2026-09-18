from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from slopscope import composition, fallback
from slopscope.report import COMPOSITION_CATEGORIES

EXCLUDES = tuple(sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS))


def analyze(source: str) -> composition.FileAnalysis:
    return composition.analyze_source(textwrap.dedent(source).lstrip("\n"))


def categories(source: str) -> list[str]:
    return list(analyze(source).line_categories)


def test_every_structural_category_is_assigned_once_per_line() -> None:
    source = '''
        """Module docstring."""
        # A comment.
        import os
        from pathlib import (
            Path,
        )


        @decorator
        def build(
            value: int,
        ) -> None:
            """Function docstring."""
            assert value
            rows = [
                1,
            ]
            try:
                total = len(rows)  # inline comment stays code
            except TypeError:
                total = 0
            if not total:
                raise ValueError("empty")
    '''

    assert categories(source) == [
        "docstring",
        "comment",
        "import",
        "import",
        "import",
        "import",
        "blank",
        "blank",
        "definition",
        "definition",
        "definition",
        "definition",
        "docstring",
        "assertion",
        "literal_data",
        "literal_data",
        "literal_data",
        "other_code",
        "other_code",
        "error_handling",
        "other_code",
        "other_code",
        "error_handling",
    ]


def test_category_totals_equal_physical_lines_and_every_category_is_present() -> None:
    analysis = analyze(
        """
        import os

        x = 1
        """
    )

    mapping = analysis.counts.as_mapping()
    assert list(mapping) == list(COMPOSITION_CATEGORIES)
    assert analysis.counts.physical == 3 == sum(mapping.values())
    assert mapping["docstring"] == 0
    assert mapping["literal_data"] == 0
    assert analysis.counts.code == 2


def test_empty_file_has_no_lines_or_statements() -> None:
    analysis = composition.analyze_source("")

    assert analysis.counts.physical == 0
    assert analysis.counts.statements == 0
    assert analysis.counts.code_per_statement is None


def test_single_newline_is_one_blank_line() -> None:
    assert composition.analyze_source("\n").line_categories == ("blank",)


def test_missing_final_newline_still_counts_last_line() -> None:
    analysis = composition.analyze_source("import os\nx = 1")

    assert analysis.line_categories == ("import", "other_code")
    assert analysis.counts.physical == 2


def test_comment_only_lines_inside_brackets_are_comments() -> None:
    assert categories(
        """
        values = call(
            # explain the argument
            1,

        )
        """
    ) == ["other_code", "comment", "other_code", "blank", "other_code"]


def test_non_docstring_string_interior_belongs_to_its_statement() -> None:
    assert (
        categories(
            '''
        TEXT = """
        # not a comment

        text
        """
        '''
        )
        == ["literal_data"] * 5
    )


def test_docstring_blank_lines_are_docstring_lines() -> None:
    assert categories(
        '''
        def f():
            """Summary.

            Details.
            """
        '''
    ) == ["definition", "docstring", "docstring", "docstring", "docstring"]


def test_only_first_string_statement_in_a_body_is_a_docstring() -> None:
    assert categories(
        '''
        class Config:
            """Class docstring."""

            name = "value"
            """Attribute note."""
            """Multi-line
            attribute note."""
        '''
    ) == [
        "definition",
        "docstring",
        "blank",
        "other_code",
        "other_code",
        "literal_data",
        "literal_data",
    ]


def test_string_after_other_module_statement_is_not_a_docstring() -> None:
    assert categories(
        '''
        import os
        """Not a docstring."""
        '''
    ) == ["import", "other_code"]


def test_bytes_and_fstrings_are_never_docstrings() -> None:
    assert categories(
        """
        def f():
            b"bytes"
        def g():
            f"{name}"
        """
    ) == ["definition", "other_code", "definition", "other_code"]


def test_two_line_literals_are_literal_data() -> None:
    assert categories(
        """
        PAIR = [1,
                2]
        MAPPING: dict[str, int] = {"a": 1,
                                   "b": 2}
        TEXT = ("first"
                "second")
        ONE_LINE = [1, 2]
        """
    ) == [
        "literal_data",
        "literal_data",
        "literal_data",
        "literal_data",
        "literal_data",
        "literal_data",
        "other_code",
    ]


def test_multiline_literal_in_return_and_expression_statements() -> None:
    assert categories(
        """
        def f():
            return (
                1,
                2,
            )
        """
    ) == ["definition", "literal_data", "literal_data", "literal_data", "literal_data"]


def test_multiline_call_is_not_literal_data() -> None:
    assert (
        categories(
            """
        value = call(
            [1, 2],
        )
        """
        )
        == ["other_code"] * 3
    )


def test_decorators_and_multiline_signatures_are_definition_lines() -> None:
    analysis = analyze(
        """
        @first
        @second(
            option=True,
        )
        # between decorators
        async def run(
            self,
            value: int,
        ) -> None: return None
        """
    )

    assert list(analysis.line_categories) == [
        "definition",
        "definition",
        "definition",
        "definition",
        "comment",
        "definition",
        "definition",
        "definition",
        "definition",
    ]
    # Lines that begin a decorator or statement are not continuations; the last line also
    # begins the one-line ``return`` body.
    assert analysis.counts.continuation_lines == 4


def test_parenthesized_decorators_start_at_the_at_sign() -> None:
    analysis = analyze(
        """
        x = 1
        @(
            # wrapped decorator
            decorator
        )
        def f():
            pass
        """
    )

    assert list(analysis.line_categories) == [
        "other_code",
        "definition",
        "comment",
        "definition",
        "definition",
        "definition",
        "other_code",
    ]
    assert [(row.name, row.line, row.lines) for row in analysis.functions] == [("f", 2, 6)]
    # ``@(`` begins the decorator; the expression and closing bracket are continuations.
    assert analysis.counts.continuation_lines == 2


def test_one_line_bodies_belong_to_the_leftmost_header() -> None:
    assert categories(
        """
        def f(): return 1
        class A: pass
        if flag: import os
        try: x = 1
        except ValueError: x = 2
        """
    ) == ["definition", "definition", "other_code", "other_code", "error_handling"]


def test_if_elif_else_clause_lines() -> None:
    assert categories(
        """
        if a:
            assert a
        elif b:
            import b
        else:
            raise c
        """
    ) == [
        "other_code",
        "assertion",
        "other_code",
        "import",
        "other_code",
        "error_handling",
    ]


def test_else_containing_nested_if_is_not_treated_as_elif() -> None:
    analysis = analyze(
        """
        if a:
            pass
        else:
            if b:
                pass
        """
    )

    assert list(analysis.line_categories) == ["other_code"] * 5
    assert analysis.counts.continuation_lines == 0


@pytest.mark.parametrize("loop", ["for item in items:", "while running:"])
def test_loop_else_clause_lines(loop: str) -> None:
    analysis = analyze(
        f"""
        {loop}
            assert item
        else:
            raise Missing
        """
    )

    assert list(analysis.line_categories) == [
        "other_code",
        "assertion",
        "other_code",
        "error_handling",
    ]
    assert analysis.counts.continuation_lines == 0


def test_try_except_else_finally_handler_bodies_keep_structural_category() -> None:
    analysis = analyze(
        """
        try:
            import fast
        except (
            ImportError,
            OSError,
        ) as exc:
            import slow
            log(exc)
        else:
            pass
        finally:
            assert done
        """
    )

    assert list(analysis.line_categories) == [
        "other_code",
        "import",
        "error_handling",
        "error_handling",
        "error_handling",
        "error_handling",
        "import",
        "other_code",
        "other_code",
        "other_code",
        "other_code",
        "assertion",
    ]
    assert analysis.counts.continuation_lines == 3


def test_except_star_headers_are_error_handling() -> None:
    assert categories(
        """
        try:
            run()
        except* ValueError:
            handle()
        """
    ) == ["other_code", "other_code", "error_handling", "other_code"]


def test_with_and_async_compound_statements() -> None:
    assert categories(
        """
        async def main():
            async with lock:
                async for item in stream:
                    await item
            with (
                open(a) as f,
            ):
                assert f
        """
    ) == [
        "definition",
        "other_code",
        "other_code",
        "other_code",
        "other_code",
        "other_code",
        "other_code",
        "assertion",
    ]


def test_match_case_clause_lines() -> None:
    analysis = analyze(
        """
        match command:
            # comment before first case
            case [
                "go",
                direction,
            ]:
                import mover
            case _ if fallback:
                raise Unknown
        """
    )

    assert list(analysis.line_categories) == [
        "other_code",
        "comment",
        "other_code",
        "other_code",
        "other_code",
        "other_code",
        "import",
        "other_code",
        "error_handling",
    ]
    # ``case`` lines begin a clause; pattern lines after the first are continuations.
    assert analysis.counts.continuation_lines == 3


def test_backslash_continuation_lines_are_statement_continuations() -> None:
    analysis = analyze(
        """
        total = 1 + \\
            2
        """
    )

    assert list(analysis.line_categories) == ["other_code", "other_code"]
    assert analysis.counts.continuation_lines == 1
    assert analysis.counts.statements == 1


def test_statements_count_every_ast_statement_including_docstrings() -> None:
    analysis = analyze(
        '''
        """Doc."""
        import os; import sys
        def f():
            if x:
                return 1
            elif y:
                return 2
        '''
    )

    # docstring, two imports, def, if, return, elif-if, return
    assert analysis.counts.statements == 8
    assert analysis.counts.code == 6
    assert analysis.counts.code_per_statement == pytest.approx(0.75)


def test_nested_definitions_use_qualified_names_and_overlapping_spans() -> None:
    analysis = analyze(
        """
        class Outer:
            class Inner:
                def method(self):
                    def helper():
                        pass

            @property
            def value(self):
                return 1

            async def run(self):
                pass

            if flag:
                def conditional(self):
                    pass
        """
    )

    assert [(row.name, row.line, row.lines, row.methods) for row in analysis.classes] == [
        ("Outer", 1, 16, 2),
        ("Outer.Inner", 2, 4, 1),
    ]
    assert [(row.name, row.line, row.lines) for row in analysis.functions] == [
        ("Outer.Inner.method", 3, 3),
        ("Outer.Inner.method.helper", 4, 2),
        ("Outer.value", 7, 3),
        ("Outer.run", 11, 2),
        ("Outer.conditional", 15, 2),
    ]


def test_invalid_syntax_raises_analysis_error() -> None:
    with pytest.raises(composition.CompositionAnalysisError, match="syntax error"):
        composition.analyze_source("def broken(:\n")


def test_invalid_escape_sequences_do_not_warn(recwarn: pytest.WarningsRecorder) -> None:
    composition.analyze_source('PATTERN = "\\d"\n')

    assert not [warning for warning in recwarn if warning.category is SyntaxWarning]


def test_encoding_cookie_is_honored(tmp_path: Path) -> None:
    source = '# -*- coding: latin-1 -*-\nNAME = "caf\xe9"\n'
    (tmp_path / "legacy.py").write_bytes(source.encode("latin-1"))

    analysis = composition.analyze_file(tmp_path, "legacy.py")

    assert analysis.line_categories == ("comment", "other_code")


def test_undecodable_file_raises_decode_error(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_bytes(b'NAME = "\xff\xfe"\n')

    with pytest.raises(composition.CompositionAnalysisError, match="decode error"):
        composition.analyze_file(tmp_path, "bad.py")


def test_unknown_encoding_cookie_raises_decode_error(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_bytes(b"# -*- coding: no-such-codec -*-\nx = 1\n")

    with pytest.raises(composition.CompositionAnalysisError, match="decode error"):
        composition.analyze_file(tmp_path, "bad.py")


def test_crlf_line_endings_count_like_lf(tmp_path: Path) -> None:
    (tmp_path / "crlf.py").write_bytes(b"import os\r\n\r\nx = 1\r\n")

    analysis = composition.analyze_file(tmp_path, "crlf.py")

    assert analysis.line_categories == ("import", "blank", "other_code")


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_report_respects_excludes_and_counts_each_file_once(tmp_path: Path) -> None:
    write(tmp_path, "src/pkg/app.py", "import os\n\nx = 1\n")
    write(tmp_path, "src/pkg/types.pyi", "def f() -> int: ...\n")
    write(tmp_path, "tests/test_app.py", "def test_x():\n    assert True\n")
    write(tmp_path, "scripts/tool.py", "print(1)\n")
    write(tmp_path, ".venv/lib/site.py", "x = 1\n")
    write(tmp_path, "build/lib/app.py", "x = 1\n")
    write(tmp_path, "generated/out.py", "x = 1\n")
    write(tmp_path, "README.md", "# not python\n")

    report = composition.build_composition_report(
        tmp_path,
        excluded_paths=(*EXCLUDES, "generated"),
    )

    assert [row.path for row in report.files] == [
        "scripts/tool.py",
        "src/pkg/app.py",
        "src/pkg/types.pyi",
        "tests/test_app.py",
    ]
    assert [(row.kind, row.area) for row in report.files] == [
        ("other", "scripts"),
        ("source", "src"),
        ("source", "src"),
        ("tests", "tests"),
    ]
    assert report.total.files == 4
    assert report.failures == ()


def test_report_totals_equal_sum_over_files_and_physical_lines(tmp_path: Path) -> None:
    sources = {
        "src/a.py": '"""Doc."""\nimport os\n\n\nclass A:\n    pass\n',
        "src/b.py": "x = [\n    1,\n]\n# end",
        "tests/test_a.py": "def test_a():\n    assert 1\n",
        "empty.py": "",
    }
    for relative_path, text in sources.items():
        write(tmp_path, relative_path, text)

    report = composition.build_composition_report(tmp_path, excluded_paths=EXCLUDES)

    expected_physical = sum(len(text.splitlines()) for text in sources.values())
    assert report.total.counts.physical == expected_physical
    assert sum(row.counts.physical for row in report.files) == expected_physical
    for index, _name in enumerate(COMPOSITION_CATEGORIES):
        assert report.total.counts.categories[index] == sum(
            row.counts.categories[index] for row in report.files
        )
        assert report.total.counts.categories[index] == sum(
            kind.counts.categories[index] for kind in report.kinds
        )
        assert report.total.counts.categories[index] == sum(
            area.counts.categories[index] for area in report.areas
        )
    assert [kind.name for kind in report.kinds] == ["source", "tests", "other"]


def test_report_collects_failures_without_dropping_other_files(tmp_path: Path) -> None:
    write(tmp_path, "src/good.py", "x = 1\n")
    write(tmp_path, "src/broken.py", "def broken(:\n")

    report = composition.build_composition_report(tmp_path, excluded_paths=EXCLUDES)

    assert [row.path for row in report.files] == ["src/good.py"]
    assert len(report.failures) == 1
    assert report.failures[0].path == "src/broken.py"
    assert report.failures[0].error.startswith("syntax error:")


def test_report_largest_lists_are_sorted_and_limited(tmp_path: Path) -> None:
    write(tmp_path, "src/big.py", "class Big:\n    def a(self):\n        x = 1\n        y = 2\n")
    write(tmp_path, "src/small.py", "def tiny():\n    pass\n")
    write(tmp_path, "src/mid.py", "def mid():\n    x = 1\n    y = 2\n")

    report = composition.build_composition_report(tmp_path, excluded_paths=EXCLUDES, limit=2)

    assert [row.path for row in report.largest_modules] == ["src/big.py", "src/mid.py"]
    assert [row.qualified_name for row in report.largest_functions] == [
        "src/big.py:Big.a",
        "src/mid.py:mid",
    ]
    assert [(row.qualified_name, row.methods) for row in report.largest_classes] == [
        ("src/big.py:Big", 1)
    ]
    assert report.settings.limit == 2


def test_multiline_fstring_interior_is_not_blank_or_comment() -> None:
    assert (
        categories(
            '''
        MESSAGE = f"""
        # {name}

        done
        """
        '''
        )
        == ["other_code"] * 5
    )
