import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from forge_companion.fermentation import analyze_readings, parse_readings
from forge_companion.fermentation_report import render_markdown, write_markdown


def test_render_markdown_is_honest_and_escapes_table_comments() -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "1",
                    "timestamp": "2026-07-16T09:00:00Z",
                    "gravity": 1.020,
                    "temperature": 28.0,
                    "comment": "start | calibrated",
                },
                {
                    "id": "2",
                    "timestamp": "2026-07-16T21:00:00Z",
                    "gravity": 1.015,
                    "temperature": 29.0,
                },
                {
                    "id": "3",
                    "timestamp": "2026-07-17T09:00:00Z",
                    "gravity": 1.010,
                    "temperature": 30.0,
                },
            ]
        }
    )
    report_time = datetime(2026, 7, 17, 10, tzinfo=UTC)
    metrics = analyze_readings(parsed, report_time=report_time)

    report = render_markdown(
        brew_name="Example Wit",
        brew_id="brew-123",
        parsed=parsed,
        metrics=metrics,
        report_time=report_time,
        temperature_unit="C",
    )

    assert report.startswith("# Fermentation Brief: Example Wit\n")
    assert "| Latest gravity | 1.0100 SG |" in report
    assert "| 24-hour gravity slope | -0.0100 SG/day |" in report
    assert "| Temperature range | 28.0–30.0 °C |" in report
    assert "- Conflicting timestamps: **0**" in report
    assert "start \\| calibrated" in report
    assert "does **not** declare fermentation complete" in report
    assert "does not prove that every upstream transmission arrived" in report


def test_render_markdown_neutralizes_active_content_in_brew_name() -> None:
    parsed = parse_readings(
        {"data": [{"id": "1", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010}]}
    )
    report_time = datetime(2026, 7, 17, 10, tzinfo=UTC)

    report = render_markdown(
        brew_name="<img src=x onerror=alert(1)> [remote](https://evil.invalid)",
        brew_id="brew-123",
        parsed=parsed,
        metrics=analyze_readings(parsed, report_time=report_time),
        report_time=report_time,
        temperature_unit=None,
    )

    heading = report.splitlines()[0]
    assert heading == (
        r"# Fermentation Brief: \<img src\=x onerror\=alert\(1\)\> "
        r"\[remote\]\(https\:\/\/evil\.invalid\)"
    )
    assert "<img src=x" not in heading
    assert "[remote](https://evil.invalid)" not in heading


def test_render_markdown_does_not_guess_temperature_unit() -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "1",
                    "timestamp": "2026-07-17T09:00:00Z",
                    "gravity": 1.010,
                    "temperature": 30.0,
                }
            ]
        }
    )
    report_time = datetime(2026, 7, 17, 10, tzinfo=UTC)

    report = render_markdown(
        brew_name="Example Wit",
        brew_id="brew-123",
        parsed=parsed,
        metrics=analyze_readings(parsed, report_time=report_time),
        report_time=report_time,
        temperature_unit=None,
    )

    assert "Temperature (raw API value)" in report
    assert "30.0 °C" not in report


def test_render_markdown_suppresses_non_finite_extreme_gravity_trend() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "1", "timestamp": "2026-07-16T09:00:00Z", "gravity": 1e308},
                {"id": "2", "timestamp": "2026-07-16T21:00:00Z", "gravity": -1e308},
                {"id": "3", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1e308},
            ]
        }
    )
    report_time = datetime(2026, 7, 17, 10, tzinfo=UTC)
    metrics = analyze_readings(parsed, report_time=report_time)

    report = render_markdown(
        brew_name="Extreme but finite",
        brew_id="brew-123",
        parsed=parsed,
        metrics=metrics,
        report_time=report_time,
        temperature_unit=None,
    )

    assert metrics.gravity_slope_per_day is None
    assert "not available — gravity range is too large for a finite slope" in report
    assert re.search(r"(?<![A-Za-z])(inf|nan)(?![A-Za-z])", report, re.IGNORECASE) is None


def test_render_markdown_lists_sanitized_bounded_rejection_details() -> None:
    parsed = parse_readings(
        {"data": [{"id": "good", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010}]}
    )
    rejected = (
        "reading 0: bad | value\n# heading **bold** <script>" + "x" * 200,
        *(f"reading {index}: invalid" for index in range(1, 12)),
    )
    parsed = replace(parsed, rejected=rejected)
    report_time = datetime(2026, 7, 17, 10, tzinfo=UTC)

    report = render_markdown(
        brew_name="Example Wit",
        brew_id="brew-123",
        parsed=parsed,
        metrics=analyze_readings(parsed, report_time=report_time),
        report_time=report_time,
        temperature_unit=None,
    )

    details = report.split("### Rejection details\n\n", 1)[1].split("\n\n## Recent readings", 1)[0]
    assert "bad \\| value \\# heading \\*\\*bold\\*\\* &lt;script&gt;" in details
    assert "\n# heading" not in details
    assert "<script>" not in details
    assert "x" * 161 not in details
    assert details.count("\n- reading ") == 9
    assert "- … 2 additional rejection reasons omitted." in details


def test_write_markdown_atomically_leaves_only_destination(tmp_path: Path) -> None:
    destination = tmp_path / "reports" / "brief.md"

    write_markdown("# Brief\n", destination)

    assert destination.read_text(encoding="utf-8") == "# Brief\n"
    assert list(destination.parent.iterdir()) == [destination]
