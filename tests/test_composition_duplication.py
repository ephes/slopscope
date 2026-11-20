from __future__ import annotations

import io
import textwrap
import tokenize
from pathlib import Path
from types import GeneratorType
from typing import Any

import pytest

from slopscope import composition, composition_duplication, fallback

EXCLUDES = tuple(sorted(fallback.DEFAULT_EXCLUDED_PATH_SEGMENTS))

BLOCK = """
def build(values):
    total = 0
    for value in values:
        if value > 10:
            total += value * 2
        else:
            total -= value
    return {"total": total, "count": len(values)}
""".lstrip("\n")


def segments(source: str, interner: dict[str, int]) -> tuple[composition_duplication.Segment, ...]:
    text = textwrap.dedent(source).lstrip("\n")
    analysis = composition.analyze_source(text)
    tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    return composition_duplication.token_segments(tokens, analysis.line_categories, interner)


def write(root: Path, relative_path: str, text: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")


def test_segments_skip_comments_layout_and_break_at_docstrings() -> None:
    interner: dict[str, int] = {}
    result = segments(
        '''
        x = 1  # comment

        def f():
            """Docstring."""
            return x
        ''',
        interner,
    )

    words = {token_id: word for word, token_id in interner.items()}
    assert [[words[token_id] for token_id in segment.ids] for segment in result] == [
        ["x", "=", "1", "def", "f", "(", ")", ":"],
        ["return", "x"],
    ]


def test_identical_blocks_are_found_once_with_every_occurrence(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", BLOCK)
    write(tmp_path, "src/b.py", "import os\n\n" + BLOCK.replace("build", "build"))
    write(tmp_path, "tests/test_c.py", "# copy\n" + BLOCK)

    report = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=20
    )

    assert len(report.duplicates) == 1
    block = report.duplicates[0]
    assert [(o.path, o.start_line, o.end_line) for o in block.occurrences] == [
        ("src/a.py", 1, 8),
        ("src/b.py", 3, 10),
        ("tests/test_c.py", 2, 9),
    ]
    assert block.lines == 8
    assert block.tokens == 42
    rows = {row.path: row.counts.duplicated_lines for row in report.files}
    assert rows == {"src/a.py": 8, "src/b.py": 8, "tests/test_c.py": 8}
    assert report.total.counts.duplicated_lines == 24


def test_formatting_and_comments_do_not_hide_duplicates(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", BLOCK)
    reformatted = BLOCK.replace(
        'return {"total": total, "count": len(values)}',
        'return {\n        "total": total,  # note\n\n        "count": len(values),\n    }',
    ).replace('"count": len(values),\n', '"count": len(values)\n')
    write(tmp_path, "src/b.py", reformatted)

    report = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=20
    )

    assert len(report.duplicates) == 1
    assert [o.end_line for o in report.duplicates[0].occurrences] == [8, 12]
    rows = {row.path: row.counts.duplicated_lines for row in report.files}
    # The blank and comment lines inside the reformatted copy are not code lines.
    assert rows == {"src/a.py": 8, "src/b.py": 11}


def test_short_or_different_code_is_not_duplicated(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", BLOCK)
    write(tmp_path, "src/b.py", BLOCK.replace("value * 2", "value * 3"))

    report = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=40
    )

    assert report.duplicates == ()
    assert report.total.counts.duplicated_lines == 0


def test_matches_stop_at_docstrings(tmp_path: Path) -> None:
    head = "a = 1\nb = 2\nc = 3\n\n\ndef g():\n"
    body = "    d = 4\n    e = 5\n    f = 6\n"
    write(tmp_path, "src/a.py", head + '    """Doc."""\n' + body)
    write(tmp_path, "src/b.py", head + body)

    report = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=12
    )

    # Without the docstring break the match would run on through ``f = 6``.
    assert len(report.duplicates) == 1
    block = report.duplicates[0]
    assert block.tokens == 14
    assert [(o.path, o.start_line, o.end_line) for o in block.occurrences] == [
        ("src/a.py", 1, 6),
        ("src/b.py", 1, 6),
    ]


def test_duplicates_within_one_file_and_overlap_limits(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", BLOCK + "\n" + BLOCK.replace("def build", "def build_again"))

    report = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=20
    )

    assert len(report.duplicates) == 1
    occurrences = report.duplicates[0].occurrences
    assert [(o.start_line, o.end_line) for o in occurrences] == [(2, 8), (11, 17)]


def test_repeated_block_groups_are_not_enumerated_pairwise(tmp_path: Path) -> None:
    for index in range(30):
        write(tmp_path, f"src/m{index:02}.py", BLOCK)

    report = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=20
    )

    assert len(report.duplicates) == 1
    assert len(report.duplicates[0].occurrences) == 30
    assert report.total.counts.duplicated_lines == 30 * 8


def test_find_duplicates_is_deterministic(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", BLOCK)
    write(tmp_path, "src/b.py", BLOCK)

    first = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=20
    )
    second = composition.build_composition_report(
        tmp_path, excluded_paths=EXCLUDES, min_duplicate_tokens=20
    )

    assert first == second


@pytest.mark.parametrize("threshold", [0, -1, 1.0, True])
def test_find_duplicates_rejects_invalid_threshold_before_reading_files(threshold: Any) -> None:
    with pytest.raises(ValueError, match="^min_tokens must be a positive integer$"):
        composition_duplication.find_duplicates([], min_tokens=threshold)


def test_copies_that_share_more_are_reported_as_their_own_longer_block() -> None:
    interner: dict[str, int] = {}
    shared = "".join(f"v{index} = {index}\n" for index in range(10))
    longer = "".join(f"w{index} = {index}\n" for index in range(10))
    first = segments(shared + longer, interner)
    second = segments(shared + longer, interner)
    third = segments(shared + "other = call()\n", interner)

    result = composition_duplication.find_duplicates([first, second, third], min_tokens=15)

    spans = sorted((block.tokens, len(block.occurrences)) for block in result.blocks)
    assert spans == [(30, 3), (60, 2)]


def test_overlapping_windows_in_one_file_are_not_separate_copies() -> None:
    interner: dict[str, int] = {}
    repeated = segments("x = 1\n" * 12, interner)

    result = composition_duplication.find_duplicates([repeated], min_tokens=9)

    for block in result.blocks:
        ends = [(o.start_line, o.end_line) for o in block.occurrences]
        for (_start, end), (next_start, _end) in zip(ends, ends[1:], strict=False):
            assert next_start > end
    assert result.intervals == {0: [(1, 12)]}


def test_subgroups_limited_by_overlap_are_not_reported_again() -> None:
    interner: dict[str, int] = {}
    unit = "a = b + c\n"  # five tokens per line
    # Three adjacent copies in one file, exactly one window apart, and a fourth copy elsewhere
    # that diverges. The first two copies still share their next token, but the overlap limit
    # keeps them at the same length, so they are not reported as a second block.
    first = segments(unit * 3, interner)
    second = segments(unit + "z = 0\n", interner)

    result = composition_duplication.find_duplicates([first, second], min_tokens=5)

    assert [(block.tokens, len(block.occurrences)) for block in result.blocks] == [(5, 4)]


def test_extension_compares_only_within_segments() -> None:
    from array import array
    from typing import cast

    comparisons = 0

    class SegmentId(int):
        def __eq__(self, other: object) -> bool:
            nonlocal comparisons
            comparisons += 1
            return super().__eq__(other)

        __hash__ = int.__hash__

    count = 4000
    segment_ids = cast("array[int]", [SegmentId(index) for index in range(count)])
    token_segments = [
        (index, composition_duplication.Segment(array("i", [1, 2]), array("i", [1, 2])))
        for index in range(count)
    ]
    result = composition_duplication._extend_blocks(
        list(range(count)), 1, segment_ids, array("i", [0] * count), token_segments
    )

    assert result == [(list(range(count)), 2)]
    assert comparisons < count * 4


def test_staircase_subgroups_preserve_exact_output_order() -> None:
    from array import array

    files = [
        [composition_duplication.Segment(array("i", ids), array("i", range(1, len(ids) + 1)))]
        for ids in ([1, 2, 3, 4], [1, 2, 3, 5], [1, 2, 6], [1, 7])
    ]
    result = composition_duplication.find_duplicates(files, min_tokens=1)
    occurrence = composition_duplication.Occurrence
    block = composition_duplication.Block
    assert result.blocks == (
        block(1, tuple(occurrence(index, 1, 1) for index in range(4))),
        block(2, tuple(occurrence(index, 1, 2) for index in range(3))),
        block(3, tuple(occurrence(index, 1, 3) for index in range(2))),
    )
    assert result.intervals == {0: [(1, 3)], 1: [(1, 3)], 2: [(1, 2)], 3: [(1, 1)]}


def test_extension_reuses_member_tail_per_group(monkeypatch: pytest.MonkeyPatch) -> None:
    from array import array
    from gc import get_referents

    shared_length = 64
    files = [
        [
            composition_duplication.Segment(
                array("i", [*range(shared_length), shared_length + index % 2]),
                array("i", [index + 1] * shared_length + [index + 2]),
            )
        ]
        for index in range(8)
    ]
    compared_tails: list[object] = []

    def count_tail(comparisons: GeneratorType[bool, None, None]) -> bool:
        # Keep each iterator's backing list alive so separate copies have distinct identities.
        frame = comparisons.gi_frame
        assert frame is not None
        (tail,) = get_referents(frame.f_locals[".0"])
        compared_tails.append(tail)
        return any(comparisons)

    monkeypatch.setattr(composition_duplication, "any", count_tail, raising=False)
    result = composition_duplication.find_duplicates(files, min_tokens=2)
    occurrence = composition_duplication.Occurrence
    block = composition_duplication.Block
    assert result.blocks == (
        block(shared_length, tuple(occurrence(index, index + 1, index + 1) for index in range(8))),
        block(
            shared_length + 1,
            tuple(occurrence(index, index + 1, index + 2) for index in range(0, 8, 2)),
        ),
        block(
            shared_length + 1,
            tuple(occurrence(index, index + 1, index + 2) for index in range(1, 8, 2)),
        ),
    )
    assert result.intervals == {index: [(index + 1, index + 2)] for index in range(8)}
    assert len({id(tail) for tail in compared_tails}) == 3


def test_staircase_subgroups_do_not_use_python_recursion() -> None:
    import subprocess
    import sys

    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from array import array
from slopscope.composition_duplication import Segment, find_duplicates
files = [[Segment(array('i', [1] * size + [size + 2]), array('i', [1] * (size + 1)))]
         for size in range(1, 121)]
sys.setrecursionlimit(80)
result = find_duplicates(files, min_tokens=1)
assert [(b.tokens, len(b.occurrences)) for b in result.blocks] == [
    (size, 121 - size) for size in range(1, 120)]
assert result.intervals == {index: [(1, 1)] for index in range(120)}
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert child.returncode == 0, child.stderr


@pytest.mark.parametrize("prefix", ["repeated", "staircase"])
@pytest.mark.parametrize("limit", ["MAX_DUPLICATE_OUTPUT_ITEMS", "MAX_DUPLICATE_WORK"])
def test_duplicate_expansion_has_a_whole_analysis_budget(
    monkeypatch: pytest.MonkeyPatch, limit: str, prefix: str
) -> None:
    from array import array

    files = [
        [
            composition_duplication.Segment(
                array(
                    "i", ([1] * size if prefix == "repeated" else list(range(size))) + [size + 100]
                ),
                array("i", range(1, size + 2)),
            )
        ]
        for size in range(1, 41)
    ]
    expected = composition_duplication.find_duplicates(files, min_tokens=1)
    monkeypatch.setattr(composition_duplication, limit, 100 if prefix == "repeated" else 5000)
    with pytest.raises(composition_duplication.BudgetExceeded, match="duplicate"):
        composition_duplication.find_duplicates(files, min_tokens=1)
    monkeypatch.setattr(composition_duplication, limit, 10_000_000)
    assert composition_duplication.find_duplicates(files, min_tokens=1) == expected


def test_anchor_hashing_is_charged_before_copying(monkeypatch: pytest.MonkeyPatch) -> None:
    from array import array

    segment = composition_duplication.Segment(array("i", range(100)), array("i", range(100)))
    hashed_words = 0

    def counted_hash(value: bytes) -> int:
        nonlocal hashed_words
        hashed_words += len(value) // segment.ids.itemsize
        return hash(value)

    monkeypatch.setattr(composition_duplication, "hash", counted_hash, raising=False)
    monkeypatch.setattr(composition_duplication, "MAX_DUPLICATE_WORK", 100)
    with pytest.raises(composition_duplication.BudgetExceeded, match="duplicate work"):
        composition_duplication.find_duplicates([[segment]], min_tokens=50)
    assert hashed_words == 100
    monkeypatch.setattr(composition_duplication, "MAX_DUPLICATE_WORK", 51 * 50)
    assert composition_duplication.find_duplicates([[segment]], min_tokens=50).blocks == ()
