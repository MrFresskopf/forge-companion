"""Shared safe local-file operations."""

import os
import tempfile
from pathlib import Path


class AtomicDestinationExistsError(FileExistsError):
    """Report that a create-only atomic destination already exists."""


def atomic_write_text(content: str, destination: Path, *, newline: str) -> None:
    """Write UTF-8 text atomically without a predictable shared temporary path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_create_text(content: str, destination: Path, *, newline: str) -> None:
    """Atomically publish new UTF-8 text while refusing every existing destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise AtomicDestinationExistsError("destination already exists") from None
    finally:
        temporary.unlink(missing_ok=True)
