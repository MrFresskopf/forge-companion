import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

import forge_companion.file_io as file_io
from forge_companion.fermentation import (
    FermentationReading,
    ParseResult,
    analyze_readings,
    parse_readings,
)
from forge_companion.fermentation_html import render_html, write_html


def test_render_html_is_self_contained_and_escapes_dynamic_text() -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "first",
                    "timestamp": "2026-07-18T08:00:00Z",
                    "gravity": 1.05,
                    "temperature": 28.5,
                    "comment": "Pitch <script>alert(1)</script>",
                },
                {
                    "id": "second",
                    "timestamp": "2026-07-18T20:00:00Z",
                    "gravity": 1.03,
                    "temperature": 29.0,
                    "comment": None,
                },
            ]
        }
    )
    report_time = datetime(2026, 7, 18, 21, tzinfo=UTC)
    metrics = analyze_readings(parsed, report_time=report_time)

    document = render_html(
        title='<Lithuanian "Session" Wit>',
        brew_id="54d34560-f1af-49f0-9a26-6caca3397f75",
        parsed=parsed,
        metrics=metrics,
        report_time=report_time,
        temperature_unit="C",
    )

    assert document.startswith("<!doctype html>")
    assert (
        'http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\""
    ) in document
    assert "Fermentation Report: &lt;Lithuanian &quot;Session&quot; Wit&gt;" in document
    assert "Pitch &lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert '<svg viewBox="0 0 760 300" role="img"' in document
    assert 'class="gravity-line"' in document
    assert 'class="temperature-line"' in document
    assert '<div class="label">Starting gravity</div><div class="value">1.0500 SG</div>' in document
    assert "Latest gravity" in document
    assert "1.0300 SG" in document
    assert "28.5–29.0 °C" in document
    assert "Latest-reading age" in document
    assert "1h 0m" in document
    assert "insufficient recent data for a 24-hour slope" in document
    assert "https://" not in document
    assert "http://" not in document
    assert "<script" not in document.lower()
    assert "<link" not in document.lower()
    assert "<img" not in document.lower()


def test_render_html_sanitizes_and_bounds_untrusted_text() -> None:
    base = parse_readings(
        {
            "data": [
                {
                    "id": "reading",
                    "timestamp": "2026-07-18T08:00:00Z",
                    "gravity": 1.04,
                    "comment": "normal\u202e<script>comment</script>",
                }
            ]
        }
    )
    parsed = ParseResult(
        readings=base.readings,
        rejected=tuple(f"bad\u202e<script>reason {index}</script>" for index in range(12)),
        conflicting_timestamps=(),
    )
    report_time = datetime(2026, 7, 18, 9, tzinfo=UTC)
    metrics = analyze_readings(parsed, report_time=report_time)

    document = render_html(
        title="Unsafe\u202e title",
        brew_id="54d34560-f1af-49f0-9a26-6caca3397f75",
        parsed=parsed,
        metrics=metrics,
        report_time=report_time,
        temperature_unit=None,
    )

    assert "\u202e" not in document
    assert "Unsafe title" in document
    assert "normal &lt;script&gt;comment&lt;/script&gt;" in document
    assert "bad &lt;script&gt;reason 0&lt;/script&gt;" in document
    assert "bad &lt;script&gt;reason 9&lt;/script&gt;" in document
    assert "bad &lt;script&gt;reason 10&lt;/script&gt;" not in document
    assert "2 additional rejection reasons omitted" in document
    assert "<script>" not in document.lower()


@pytest.mark.parametrize(
    ("first_temperature", "second_temperature"),
    [
        (-1e308, 1e308),
        (-1.7206369255607505e308, -5.448231578756787e103),
        (sys.float_info.max, sys.float_info.max),
        (-sys.float_info.max, -sys.float_info.max),
    ],
)
def test_render_html_scales_extreme_finite_temperatures_without_nonfinite_svg(
    first_temperature: float, second_temperature: float
) -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "cold",
                    "timestamp": "2026-07-18T07:00:00Z",
                    "gravity": 1.041,
                    "temperature": first_temperature,
                },
                {
                    "id": "hot",
                    "timestamp": "2026-07-18T08:00:00Z",
                    "gravity": 1.04,
                    "temperature": second_temperature,
                },
            ]
        }
    )
    report_time = datetime(2026, 7, 18, 9, tzinfo=UTC)
    metrics = analyze_readings(parsed, report_time=report_time)

    document = render_html(
        title="Extreme finite temperatures",
        brew_id="54d34560-f1af-49f0-9a26-6caca3397f75",
        parsed=parsed,
        metrics=metrics,
        report_time=report_time,
        temperature_unit=None,
    )
    svg = document.split("<svg", 1)[1].split("</svg>", 1)[0].lower()

    assert "nan" not in svg
    assert "inf" not in svg
    assert svg.count('<text x="704"') == 2
    assert "e+" in svg
    if first_temperature < 0 < second_temperature:
        assert '<text x="704" y="38">1.' in svg
        assert '<text x="704" y="242">-1.' in svg


def test_render_html_bounds_extreme_gravity_axis_labels() -> None:
    timestamp = datetime(2026, 7, 18, 8, tzinfo=UTC)
    parsed = ParseResult(
        readings=(
            FermentationReading(
                id="extreme-gravity",
                timestamp=timestamp,
                gravity=sys.float_info.max,
                temperature=None,
                pressure=None,
                ph=None,
                comment=None,
            ),
        ),
        rejected=(),
        conflicting_timestamps=(),
    )
    metrics = analyze_readings(parsed, report_time=datetime(2026, 7, 18, 9, tzinfo=UTC))

    document = render_html(
        title="Extreme gravity",
        brew_id="54d34560-f1af-49f0-9a26-6caca3397f75",
        parsed=parsed,
        metrics=metrics,
        report_time=datetime(2026, 7, 18, 9, tzinfo=UTC),
        temperature_unit=None,
    )
    svg = document.split("<svg", 1)[1].split("</svg>", 1)[0].lower()

    assert "e+308" in svg
    assert len(svg) < 5_000


def test_write_html_preserves_existing_file_and_cleans_temp_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.html"
    destination.write_text("existing", encoding="utf-8")

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(file_io.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        write_html("replacement", destination)

    assert destination.read_text(encoding="utf-8") == "existing"
    assert list(tmp_path.glob(".report.html.*.tmp")) == []


def test_render_html_omits_temperature_trace_when_no_temperature_exists() -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "only",
                    "timestamp": "2026-07-18T08:00:00Z",
                    "gravity": 1.04,
                    "temperature": None,
                }
            ]
        }
    )
    report_time = datetime(2026, 7, 18, 9, tzinfo=UTC)
    metrics = analyze_readings(parsed, report_time=report_time)

    document = render_html(
        title="Single reading",
        brew_id="54d34560-f1af-49f0-9a26-6caca3397f75",
        parsed=parsed,
        metrics=metrics,
        report_time=report_time,
        temperature_unit=None,
    )

    assert 'class="gravity-line"' in document
    assert 'class="gravity-point"' in document
    assert 'class="temperature-line"' not in document
    assert "Temperature (raw)" not in document
    assert 'aria-label="Gravity over time"' in document
    assert 'aria-label="Gravity and temperature over time"' not in document
    assert "not available" in document
