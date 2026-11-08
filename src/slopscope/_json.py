"""Strict JSON object admission for persisted analysis inputs."""

from __future__ import annotations

from typing import Any


class DuplicateKeyError(ValueError):
    """A JSON object repeats a key, including keys with identical values."""


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguity before JSON object pairs are collapsed into a dictionary."""
    result: dict[str, Any] = {}
    for index, (key, value) in enumerate(pairs, start=1):
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key at member {index}: {key[:60]!r}")
        result[key] = value
    return result
