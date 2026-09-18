"""Token-based duplicate code detection for the composition report.

Each file becomes one or more *segments*: runs of code tokens with comments, layout tokens, and
docstrings removed. Docstrings end a segment, so no match spans one. Every token that starts a
physical line is an *anchor*. The ``min_tokens`` tokens from each anchor form a window; windows
are grouped by hash and then verified token by token. A verified group with at least two windows
is a duplicate; windows that overlap an earlier window of the same group in the same segment are
dropped. Groups are extended to the right while every occurrence still agrees, copies that keep
agreeing after others stop are extended as their own subgroup, and groups that only continue an
already extended block are skipped, so each duplicated block is reported once with all of its
occurrences and no pairwise scores.
"""

from __future__ import annotations

import tokenize
from array import array
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_MIN_TOKENS = 50
DUPLICATION_VERSION = 1

_SKIPPED_TOKEN_TYPES = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)


@dataclass(frozen=True)
class Segment:
    """A run of code tokens from one file: interned token ids and their line numbers."""

    ids: array[int]
    lines: array[int]


@dataclass(frozen=True)
class Occurrence:
    """Where one copy of a duplicated block sits."""

    file: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Block:
    """A duplicated token sequence and every place it occurs."""

    tokens: int
    occurrences: tuple[Occurrence, ...]


@dataclass(frozen=True)
class DuplicationResult:
    """Duplicated line intervals per file index and the duplicated blocks."""

    intervals: dict[int, list[tuple[int, int]]]
    blocks: tuple[Block, ...]


def token_segments(
    tokens: Sequence[tokenize.TokenInfo],
    line_categories: Sequence[str],
    interner: dict[str, int],
) -> tuple[Segment, ...]:
    """Split a file's tokens into code segments, interning token strings."""

    segments: list[Segment] = []
    ids: array[int] = array("i")
    lines: array[int] = array("i")
    for token in tokens:
        if token.type in _SKIPPED_TOKEN_TYPES:
            continue
        row = token.start[0]
        if row <= len(line_categories) and line_categories[row - 1] == "docstring":
            if ids:
                segments.append(Segment(ids=ids, lines=lines))
                ids, lines = array("i"), array("i")
            continue
        token_id = interner.get(token.string)
        if token_id is None:
            token_id = len(interner)
            interner[token.string] = token_id
        ids.append(token_id)
        lines.append(row)
    if ids:
        segments.append(Segment(ids=ids, lines=lines))
    return tuple(segments)


def find_duplicates(
    files: Sequence[Sequence[Segment]],
    *,
    min_tokens: int = DEFAULT_MIN_TOKENS,
) -> DuplicationResult:
    """Find duplicated blocks of at least ``min_tokens`` tokens across and within files."""

    segments: list[tuple[int, Segment]] = [
        (file_index, segment)
        for file_index, file_segments in enumerate(files)
        for segment in file_segments
    ]

    # Anchors: (segment index, token position) for every line start with a full window.
    anchor_segment: array[int] = array("i")
    anchor_position: array[int] = array("i")
    first_by_hash: dict[int, int] = {}
    buckets: dict[int, list[int]] = {}
    for segment_index, (_file_index, segment) in enumerate(segments):
        ids, lines = segment.ids, segment.lines
        for position in range(len(ids) - min_tokens + 1):
            if position and lines[position] == lines[position - 1]:
                continue
            anchor = len(anchor_position)
            anchor_segment.append(segment_index)
            anchor_position.append(position)
            key = hash(ids[position : position + min_tokens].tobytes())
            first = first_by_hash.setdefault(key, anchor)
            if first != anchor:
                buckets.setdefault(key, [first]).append(anchor)

    def window(anchor: int) -> array[int]:
        position = anchor_position[anchor]
        return segments[anchor_segment[anchor]][1].ids[position : position + min_tokens]

    groups: list[list[int]] = []
    for bucket in buckets.values():
        verified: list[tuple[array[int], list[int]]] = []
        for anchor in bucket:
            tokens = window(anchor)
            for reference, members in verified:
                if reference == tokens:
                    members.append(anchor)
                    break
            else:
                verified.append((tokens, [anchor]))
        groups.extend(members for _reference, members in verified if len(members) > 1)
    groups.sort(key=lambda members: members[0])

    intervals: dict[int, list[tuple[int, int]]] = {}
    # Anchor -> every (block index, occurrence index) whose copy contains the anchor's window.
    covered_by: dict[int, list[tuple[int, int]]] = {}
    blocks: list[Block] = []
    for group in groups:
        members = _without_overlaps(group, anchor_segment, anchor_position, min_tokens)
        if len(members) < 2:
            continue
        for anchor in members:
            file_index, segment = segments[anchor_segment[anchor]]
            position = anchor_position[anchor]
            intervals.setdefault(file_index, []).append(
                (segment.lines[position], segment.lines[position + min_tokens - 1])
            )

        if _continues_block(members, covered_by, blocks):
            continue

        for block_members, length in _extend_blocks(
            members, min_tokens, anchor_segment, anchor_position, segments
        ):
            block_index = len(blocks)
            occurrences: list[Occurrence] = []
            for occurrence_index, anchor in enumerate(block_members):
                file_index, segment = segments[anchor_segment[anchor]]
                position = anchor_position[anchor]
                end = position + length
                occurrences.append(
                    Occurrence(
                        file=file_index,
                        start_line=segment.lines[position],
                        end_line=segment.lines[end - 1],
                    )
                )
                intervals[file_index].append((segment.lines[position], segment.lines[end - 1]))
                # Record later anchors of this segment whose windows lie inside this copy.
                next_anchor = anchor + 1
                while (
                    next_anchor < len(anchor_position)
                    and anchor_segment[next_anchor] == anchor_segment[anchor]
                    and anchor_position[next_anchor] + min_tokens <= end
                ):
                    covered_by.setdefault(next_anchor, []).append((block_index, occurrence_index))
                    next_anchor += 1
            blocks.append(Block(tokens=length, occurrences=tuple(occurrences)))

    return DuplicationResult(
        intervals={file_index: _merge(spans) for file_index, spans in intervals.items()},
        blocks=tuple(blocks),
    )


def _without_overlaps(
    group: Sequence[int],
    anchor_segment: array[int],
    anchor_position: array[int],
    min_tokens: int,
) -> list[int]:
    """Drop windows that overlap an earlier kept window of the same segment."""

    kept: list[int] = []
    last_kept: dict[int, int] = {}
    for anchor in sorted(group):
        segment = anchor_segment[anchor]
        position = anchor_position[anchor]
        previous = last_kept.get(segment)
        if previous is not None and position < previous + min_tokens:
            continue
        last_kept[segment] = position
        kept.append(anchor)
    return kept


def _continues_block(
    members: Sequence[int],
    covered_by: dict[int, list[tuple[int, int]]],
    blocks: Sequence[Block],
) -> bool:
    """Return whether one found block has every member inside a different one of its copies."""

    covers = [covered_by.get(anchor, []) for anchor in members]
    if not all(covers):
        return False
    shared_blocks = set.intersection(*({block for block, _occurrence in cover} for cover in covers))
    for block in shared_blocks:
        occurrences = {
            occurrence for cover in covers for covered, occurrence in cover if covered == block
        }
        if len(occurrences) == len(members) == len(blocks[block].occurrences):
            return True
    return False


def _extend_blocks(
    members: Sequence[int],
    length: int,
    anchor_segment: array[int],
    anchor_position: array[int],
    segments: Sequence[tuple[int, Segment]],
    *,
    subgroup: bool = False,
) -> list[tuple[Sequence[int], int]]:
    """Extend a verified group while every copy agrees, then keep extending subgroups.

    When copies stop agreeing, the copies that still share their next token form subgroups, so
    two copies that share more than a third one are also reported as their own, longer block.
    Copies in the same segment never run into each other.
    """

    start_length = length
    starts = [
        (segments[anchor_segment[anchor]][1].ids, anchor_position[anchor], anchor_segment[anchor])
        for anchor in members
    ]
    limit = min(len(ids) - position for ids, position, _segment in starts)
    for index, (_ids, position, segment) in enumerate(starts):
        for _other_ids, other_position, other_segment in starts[index + 1 :]:
            if other_segment == segment:
                limit = min(limit, abs(other_position - position))
    reference_ids, reference_position, _segment = starts[0]
    while length < limit:
        token = reference_ids[reference_position + length]
        if any(ids[position + length] != token for ids, position, _segment in starts[1:]):
            break
        length += 1

    # A subgroup is only its own block when it is longer than the block it split from.
    blocks: list[tuple[Sequence[int], int]] = (
        [(members, length)] if not subgroup or length > start_length else []
    )
    next_tokens: dict[int, list[int]] = {}
    for anchor, (ids, position, _segment) in zip(members, starts, strict=True):
        if position + length < len(ids):
            next_tokens.setdefault(ids[position + length], []).append(anchor)
    for agreeing in next_tokens.values():
        if 2 <= len(agreeing) < len(members):
            blocks.extend(
                _extend_blocks(
                    agreeing, length, anchor_segment, anchor_position, segments, subgroup=True
                )
            )
    return blocks


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or touching line intervals."""

    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
