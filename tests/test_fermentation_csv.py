import csv
import io
from pathlib import Path

from forge_companion.fermentation import parse_readings
from forge_companion.fermentation_csv import render_csv, write_csv


def test_render_csv_neutralizes_spreadsheet_formulas_in_text_cells() -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "@reading",
                    "timestamp": "2026-07-17T08:00:00Z",
                    "gravity": 1.012,
                    "comment": " =SUM(1,1)",
                }
            ]
        }
    )

    rows = list(csv.reader(io.StringIO(render_csv(parsed))))

    assert rows[1][0] == "'@reading"
    assert rows[1][6] == "' =SUM(1,1)"


def test_write_csv_atomically_leaves_only_destination(tmp_path: Path) -> None:
    destination = tmp_path / "reports" / "readings.csv"

    write_csv("id,timestamp_utc\n", destination)

    assert destination.read_text(encoding="utf-8") == "id,timestamp_utc\n"
    assert list(destination.parent.iterdir()) == [destination]
