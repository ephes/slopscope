"""Open admitted Python sources without following links below the selected root."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

Admission = tuple[os.stat_result, ...]


def admit(root: Path, relative_path: str) -> Admission:
    """Record the root, directory chain, and regular file selected by discovery."""

    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise OSError("source path must stay below the selected root")
    chain = [root.stat()]
    current = root
    for part in relative.parts:
        current /= part
        chain.append(current.lstat())
    if not all(stat.S_ISDIR(info.st_mode) for info in chain[:-1]):
        raise OSError("source directory changed or is a symbolic link")
    if not stat.S_ISREG(chain[-1].st_mode):
        raise OSError("source must be a regular file without symbolic links")
    return tuple(chain)


def _check(actual: os.stat_result, expected: os.stat_result, *, directory: bool) -> None:
    same_identity = (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
    correct_type = stat.S_ISDIR(actual.st_mode) if directory else stat.S_ISREG(actual.st_mode)
    if not same_identity or not correct_type:
        raise OSError("source changed since discovery")
    if not directory and (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (
        expected.st_size,
        expected.st_mtime_ns,
        expected.st_ctime_ns,
    ):
        raise OSError("source changed since discovery")


_READ_CHUNK = 64 * 1024


def read_bounded(handle: BinaryIO, limit: int) -> bytes | None:
    """Read at most ``limit`` bytes in fixed-size chunks; return None if the source is larger.

    Chunked reads keep allocation proportional to the bytes actually present, so a large
    configured limit never reserves (or overflows) a buffer of that size up front.
    """

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(min(_READ_CHUNK, limit - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)


@contextmanager
def open_source(
    root: Path, relative_path: str, admission: Admission | None = None
) -> Iterator[BinaryIO]:
    """Validate each opened descriptor before reading; refuse unsupported OS APIs."""

    expected = admission if admission is not None else admit(root, relative_path)
    parts = Path(relative_path).parts
    if not parts or Path(relative_path).is_absolute() or ".." in parts:
        raise OSError("source path must stay below the selected root")
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK")):
        raise OSError("safe source opening is unavailable on this platform")
    with ExitStack() as stack:
        try:
            directory_flags = os.O_RDONLY | os.O_DIRECTORY
            parent = os.open(root, directory_flags)
            stack.callback(os.close, parent)
            _check(os.fstat(parent), expected[0], directory=True)
            for index, part in enumerate(parts[:-1], start=1):
                parent = os.open(part, directory_flags | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, parent)
                _check(os.fstat(parent), expected[index], directory=True)
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except (NotImplementedError, TypeError) as exc:
            raise OSError("safe source opening is unavailable on this platform") from exc
        try:
            handle = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        stack.enter_context(handle)
        _check(os.fstat(handle.fileno()), expected[-1], directory=False)
        yield handle
        _check(os.fstat(handle.fileno()), expected[-1], directory=False)
