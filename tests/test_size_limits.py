from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from slopscope import fallback, size_limits
from slopscope.report import SizeLimitSettings, SizeLimitsReport

EXCLUDES = tuple(sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS))
SMALL = SizeLimitSettings(max_function_lines=3, max_class_lines=8, max_test_file_code_lines=4)


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def measure(root: Path) -> size_limits.Units:
    units, failures = size_limits.measure(root, excluded_paths=EXCLUDES)
    assert failures == ()
    return units


def run(root: Path, action: str = size_limits.REPORT) -> SizeLimitsReport:
    return size_limits.check(
        root,
        allowlist_path=root / "size-limits.json",
        settings=SMALL,
        action=action,
        excluded_paths=EXCLUDES,
    )


def test_spans_include_decorators_and_nested_definitions_count_twice(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.py",
        """
        @decorator(
            option=True,
        )
        class Outer:
            @property
            def value(self):
                def helper():
                    return 1
                return helper()
        """,
    )

    units = measure(tmp_path)

    assert units["classes"] == {"src/app.py::Outer": 9}
    assert units["functions"] == {
        "src/app.py::Outer.value": 5,
        "src/app.py::Outer.value.helper": 2,
    }


def test_conditional_definitions_keep_the_enclosing_prefix(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.py",
        """
        class Service:
            if FAST:
                def run(self):
                    pass
            else:
                def run(self):
                    pass
            try:
                def load(self):
                    pass
            except ImportError:
                pass
            with ctx:
                def save(self):
                    pass
        """,
    )

    assert sorted(measure(tmp_path)["functions"]) == [
        "src/app.py::Service.load",
        "src/app.py::Service.run",
        "src/app.py::Service.run#2",
        "src/app.py::Service.save",
    ]


def test_overload_stubs_are_skipped(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.py",
        """
        import typing
        from typing import overload

        @overload
        def parse(value: int) -> int: ...
        @typing.overload
        def parse(value: str) -> str: ...
        def parse(value):
            return value
        """,
    )

    assert measure(tmp_path)["functions"] == {"src/app.py::parse": 2}


def test_repeated_names_get_numbered_suffixes_in_source_order(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.py",
        """
        class Box:
            @property
            def size(self):
                return 1

            @size.setter
            def size(self, value):
                self._size = value
                self._dirty = True

            @size.deleter
            def size(self):
                pass
        """,
    )

    assert measure(tmp_path)["functions"] == {
        "src/app.py::Box.size": 3,
        "src/app.py::Box.size#2": 4,
        "src/app.py::Box.size#3": 3,
    }


def test_test_files_are_measured_in_code_lines(tmp_path: Path) -> None:
    write(
        tmp_path,
        "tests/test_app.py",
        '''
        """Docstring."""
        # comment

        def test_one():
            assert True
        ''',
    )
    write(tmp_path, "tests/conftest.py", "import pytest\n")
    write(tmp_path, "tests/helpers/factory.py", "def make():\n    return 1\n")
    write(tmp_path, "src/app.py", "x = 1\n")

    assert measure(tmp_path)["test_files"] == {
        "tests/conftest.py": 1,
        "tests/helpers/factory.py": 2,
        "tests/test_app.py": 2,
    }


def make_project(root: Path) -> None:
    write(root, "src/big.py", "def big():\n" + "    x = 1\n" * 5)
    write(root, "src/fine.py", "def fine():\n    return 1\n")
    write(
        root,
        "src/shape.py",
        "class Shape:\n"
        + "".join(f"    def m{index}(self):\n        pass\n" for index in range(5)),
    )
    write(root, "tests/test_big.py", "".join(f"x{index} = {index}\n" for index in range(6)))


def test_report_groups(tmp_path: Path) -> None:
    make_project(tmp_path)
    allowlist = tmp_path / "size-limits.json"
    size_limits.write_allowlist(
        allowlist,
        {
            "functions": {
                "src/big.py::big": 4,  # grew to 6
                "src/fine.py::fine": 9,  # now within the limit
                "src/gone.py::old": 7,  # missing
            },
            "classes": {"src/shape.py::Shape": 12},  # shrank to 11
            "test_files": {},
        },
    )

    report = run(tmp_path)

    assert [(e.kind, e.key, e.size) for e in report.over_limit] == [
        ("test_files", "tests/test_big.py", 6)
    ]
    assert [(e.key, e.recorded, e.size, e.delta) for e in report.grown] == [
        ("src/big.py::big", 4, 6, 2)
    ]
    assert [(e.key, e.recorded, e.size, e.delta) for e in report.shrunk] == [
        ("src/shape.py::Shape", 12, 11, -1)
    ]
    assert [(e.key, e.reason) for e in report.stale] == [
        ("src/fine.py::fine", "within limit"),
        ("src/gone.py::old", "missing"),
    ]
    assert report.check_failed
    assert report.units == (7, 1, 1)


def test_clean_project_passes(tmp_path: Path) -> None:
    write(tmp_path, "src/fine.py", "def fine():\n    return 1\n")

    report = run(tmp_path)

    assert not report.check_failed
    assert report.allowlist_existed is False


def test_update_only_lowers_and_drops(tmp_path: Path) -> None:
    make_project(tmp_path)
    allowlist = tmp_path / "size-limits.json"
    size_limits.write_allowlist(
        allowlist,
        {
            "functions": {"src/big.py::big": 4, "src/fine.py::fine": 9, "src/gone.py::old": 7},
            "classes": {"src/shape.py::Shape": 12},
        },
    )

    report = run(tmp_path, size_limits.UPDATE)

    assert json.loads(allowlist.read_text(encoding="utf-8")) == {
        "classes": {"src/shape.py::Shape": 11},
        # Grown entries are never raised, and the new test-file offender is never added.
        "functions": {"src/big.py::big": 4},
        "test_files": {},
    }
    assert [(e.key, e.recorded, e.size) for e in report.lowered] == [
        ("src/shape.py::Shape", 12, 11)
    ]
    assert [(e.key, e.reason) for e in report.dropped] == [
        ("src/fine.py::fine", "within limit"),
        ("src/gone.py::old", "missing"),
    ]
    assert [e.key for e in report.grown] == ["src/big.py::big"]
    assert [e.key for e in report.over_limit] == ["tests/test_big.py"]


def test_update_requires_an_allowlist(tmp_path: Path) -> None:
    make_project(tmp_path)

    with pytest.raises(size_limits.SizeLimitsError, match="seed one first"):
        run(tmp_path, size_limits.UPDATE)


def test_seed_writes_current_offenders_with_sorted_keys(tmp_path: Path) -> None:
    make_project(tmp_path)

    report = run(tmp_path, size_limits.SEED)

    text = (tmp_path / "size-limits.json").read_text(encoding="utf-8")
    assert json.loads(text) == {
        "classes": {"src/shape.py::Shape": 11},
        "functions": {"src/big.py::big": 6},
        "test_files": {"tests/test_big.py": 6},
    }
    assert text.index('"classes"') < text.index('"functions"') < text.index('"test_files"')
    assert len(report.seeded) == 3
    assert not report.check_failed


def test_seed_refuses_to_overwrite(tmp_path: Path) -> None:
    make_project(tmp_path)
    allowlist = tmp_path / "size-limits.json"
    allowlist.write_text("{}\n", encoding="utf-8")

    with pytest.raises(size_limits.SizeLimitsError, match="already exists"):
        run(tmp_path, size_limits.SEED)

    assert allowlist.read_text(encoding="utf-8") == "{}\n"


def test_unparseable_files_block_updates_and_keep_their_entries(tmp_path: Path) -> None:
    make_project(tmp_path)
    write(tmp_path, "src/broken.py", "def broken(:\n")
    size_limits.write_allowlist(
        tmp_path / "size-limits.json", {"functions": {"src/broken.py::broken": 9}}
    )

    report = run(tmp_path)

    assert [failure.path for failure in report.failures] == ["src/broken.py"]
    assert report.stale == ()
    with pytest.raises(size_limits.SizeLimitsError, match="fail to parse"):
        run(tmp_path, size_limits.UPDATE)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"modules": {}}', "unknown section: modules"),
        ('{"functions": []}', "section functions must be an object"),
        ('{"functions": {"a::b": 0}}', "must be a positive integer"),
        ('{"functions": {"a::b": true}}', "must be a positive integer"),
    ],
)
def test_invalid_allowlists_are_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "size-limits.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(size_limits.SizeLimitsError, match=message):
        size_limits.load_allowlist(path)


def test_missing_sections_default_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "size-limits.json"
    path.write_text('{"functions": {"a::b": 3}}', encoding="utf-8")

    assert size_limits.load_allowlist(path) == {
        "functions": {"a::b": 3},
        "classes": {},
        "test_files": {},
    }
