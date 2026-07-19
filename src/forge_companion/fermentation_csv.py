"""Deterministic CSV export for validated fermentation readings."""

import csv
import io
import os
import tempfile
from pathlib import Path

from forge_companion.fermentation import ParseResult


def _spreadsheet_text(value: str | None) -> str | None:
    if value is not None and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def render_csv(parsed: ParseResult) -> str:
    """Render accepted readings as stable RFC 4180-style CSV rows."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "timestamp_utc",
            "gravity_sg",
            "temperature_raw",
            "pressure",
            "ph",
            "comment",
        ]
    )
    for reading in parsed.readings:
        writer.writerow(
            [
                _spreadsheet_text(reading.id),
                reading.timestamp.isoformat().replace("+00:00", "Z"),
                reading.gravity,
                reading.temperature,
                reading.pressure,
                reading.ph,
                _spreadsheet_text(reading.comment),
            ]
        )
    return output.getvalue()


def write_csv(content: str, destination: Path) -> None:
    """Write a CSV export atomically without a predictable shared temp path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
