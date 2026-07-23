from pathlib import Path

import pytest

from forge_companion.file_io import (
    AtomicDestinationExistsError,
    atomic_create_text,
    atomic_write_text,
)


def test_atomic_write_text_creates_parent_and_replaces_content(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "report.txt"

    atomic_write_text("first\n", destination, newline="\n")
    atomic_write_text("second\n", destination, newline="\n")

    assert destination.read_text(encoding="utf-8") == "second\n"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_create_text_publishes_complete_new_file(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "plan.json"

    atomic_create_text("complete\n", destination, newline="\n")

    assert destination.read_text(encoding="utf-8") == "complete\n"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_create_text_preserves_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "plan.json"
    destination.write_text("original\n", encoding="utf-8")

    with pytest.raises(AtomicDestinationExistsError):
        atomic_create_text("replacement\n", destination, newline="\n")

    assert destination.read_text(encoding="utf-8") == "original\n"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_atomic_create_text_refuses_dangling_symlink(tmp_path: Path) -> None:
    destination = tmp_path / "plan.json"
    try:
        destination.symlink_to(tmp_path / "missing-target.json")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(AtomicDestinationExistsError):
        atomic_create_text("replacement\n", destination, newline="\n")

    assert destination.is_symlink()
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []
