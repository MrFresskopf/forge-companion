"""Deterministic CSV export for validated fermentation readings."""

import csv
import io
from pathlib import Path

from forge_companion.fermentation import ParseResult
from forge_companion.file_io import atomic_write_text


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
    atomic_write_text(content, destination, newline="")
