"""Shared safe local-file operations."""

import os
import tempfile
from pathlib import Path
from typing import Any, cast


class AtomicDestinationExistsError(FileExistsError):
    """Report that a create-only atomic destination already exists."""


def _replace_file_durably(source: Path, destination: Path) -> None:
    """Replace a file and flush the directory update before returning."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        win_ctypes = cast(Any, ctypes)
        move_file = win_ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        move_file.restype = wintypes.BOOL
        movefile_replace_existing = 0x1
        movefile_write_through = 0x8
        if not move_file(
            str(source),
            str(destination),
            movefile_replace_existing | movefile_write_through,
        ):
            raise win_ctypes.WinError(win_ctypes.get_last_error())
        return

    os.replace(source, destination)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_fd = os.open(destination.parent, flags)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


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
        _replace_file_durably(temporary, destination)
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
