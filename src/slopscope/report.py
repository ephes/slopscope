"""Pure report data structures."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True)
class LanguageRow:
    """A language summary row."""

    language: str
    files: int
    blank: int
    comment: int
    code: int


@dataclass(frozen=True)
class LanguageSummaryReport:
    """Language-summary report data independent of counting and rendering."""

    engine: str
    path: Path
    language_rows: tuple[LanguageRow, ...]

    @classmethod
    def from_rows(
        cls,
        *,
        engine: str,
        path: Path | str,
        language_rows: Iterable[LanguageRow],
    ) -> Self:
        """Build a report from any iterable of language rows."""

        return cls(
            engine=engine,
            path=Path(path),
            language_rows=tuple(language_rows),
        )
