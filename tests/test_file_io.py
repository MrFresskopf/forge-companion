from pathlib import Path

from forge_companion.file_io import atomic_write_text


def test_atomic_write_text_creates_parent_and_replaces_content(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "report.txt"

    atomic_write_text("first\n", destination, newline="\n")
    atomic_write_text("second\n", destination, newline="\n")

    assert destination.read_text(encoding="utf-8") == "second\n"
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []
