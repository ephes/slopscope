"""Path normalization helpers shared across counting modes."""

from __future__ import annotations

from pathlib import Path


def row_filter_path(path: str, *, root: Path) -> Path:
    """Return a row path relative to root when an engine emits an absolute path."""

    file_path = Path(path)
    if not file_path.is_absolute():
        return file_path
    try:
        return file_path.resolve().relative_to(root.resolve())
    except ValueError:
        return file_path
