from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from forge_companion.brewforge_telemetry import adapt_brewforge_readings
from forge_companion.fermentation_progress import (
    FermentationProgressValidationError,
    evaluate_fermentation_progress,
)
from forge_companion.rapt_sg import normalize_rapt_sg
from forge_companion.telemetry import DeviceKind, TelemetryReading, parse_rapt_telemetry

DEVICE = "00000000-0000-0000-0000-000000000001"
BREW = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
WINDOW_START_NS = 1_784_246_400_000_000_000
WINDOW_END_NS = WINDOW_START_NS + 86_400_000_000_000


def test_equivalent_adapted_and_normalized_streams_have_identical_progress_evidence() -> None:
    brewforge = adapt_brewforge_readings(
        {
            "data": [
                {
                    "id": "brew-early",
                    "timestamp": "2026-07-17T00:00:00Z",
                    "gravity": 1.050,
                    "temperature": 20.0,
                },
                {
                    "id": "brew-latest",
                    "timestamp": "2026-07-18T00:00:00Z",
                    "gravity": 1.025,
                    "temperature": 20.0,
                },
            ]
        },
        brew_id=BREW,
        gravity_unit="sg",
        temperature_unit="c",
    )
    rapt = normalize_rapt_sg(
        parse_rapt_telemetry(
            [
                {
                    "id": "00000000-0000-0000-0000-000000000010",
                    "createdOn": "2026-07-17T00:00:00Z",
                    "temperature": 20.0,
                    "gravity": 1050,
                    "battery": 90.0,
                    "rssi": -50.0,
                },
                {
                    "id": "00000000-0000-0000-0000-000000000011",
                    "createdOn": "2026-07-18T00:00:00Z",
                    "temperature": 20.0,
                    "gravity": 1025,
                    "battery": 90.0,
                    "rssi": -50.0,
                },
            ],
            kind=DeviceKind.HYDROMETER,
            device_id=DEVICE,
        ),
        gravity_interpretation="sg-times-1000",
    )

    brewforge_result = evaluate_fermentation_progress(
        brewforge,
        gravity_unit="sg",
        original_gravity="1.050",
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
    )
    rapt_result = evaluate_fermentation_progress(
        rapt,
        gravity_unit="sg",
        original_gravity="1.050",
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
    )

    assert brewforge_result.latest_sg == rapt_result.latest_sg == Decimal("1.025")
    assert brewforge_result.apparent_attenuation_percent == rapt_result.apparent_attenuation_percent
    assert brewforge_result.average_sg_slope_per_day == rapt_result.average_sg_slope_per_day
    assert brewforge_result.average_sg_slope_per_day == Decimal("-0.025")
    assert (
        brewforge_result.rate_label
        == "average SG slope over selected observation interval (SG/day; not forecast)"
    )


def _reading(*, reading_id: str, gravity: float, remainder: int = 0) -> TelemetryReading:
    return adapt_brewforge_readings(
        {
            "data": [
                {
                    "id": reading_id,
                    "timestamp": f"2026-07-17T00:00:00.000000{remainder // 100}Z",
                    "gravity": gravity,
                    "temperature": None,
                }
            ]
        },
        brew_id=BREW,
        gravity_unit="sg",
        temperature_unit="c",
    )[0]


def _pair(
    *, first_sg: float = 1.050, latest_sg: float = 1.025
) -> tuple[TelemetryReading, TelemetryReading]:
    first = _reading(reading_id="first", gravity=first_sg)
    latest = replace(
        _reading(reading_id="latest", gravity=latest_sg),
        observed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    return first, latest


def test_exact_100ns_endpoints_are_ordered_and_rate_is_signed() -> None:
    first = _reading(reading_id="first", gravity=1.020, remainder=100)
    latest = replace(
        _reading(reading_id="latest", gravity=1.030, remainder=200), observed_at=first.observed_at
    )

    result = evaluate_fermentation_progress(
        (latest, first),
        gravity_unit="sg",
        original_gravity="1.050",
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_START_NS + 1_000,
    )

    assert result.first_observation.reading_id == "first"
    assert result.latest_observation.reading_id == "latest"
    assert result.observation_interval_end_ns - result.observation_interval_start_ns == 100
    assert result.average_sg_slope_per_day == Decimal("8640000000.0")


@pytest.mark.parametrize(
    ("latest", "expected"),
    [(1.050, Decimal("0")), (1.000, Decimal("100"))],
)
def test_apparent_attenuation_includes_zero_and_hundred_percent_boundaries(
    latest: float, expected: Decimal
) -> None:
    result = evaluate_fermentation_progress(
        _pair(latest_sg=latest),
        gravity_unit="sg",
        original_gravity="1.050",
        window_start_ns=WINDOW_START_NS,
        window_end_ns=WINDOW_END_NS,
    )
    assert result.apparent_attenuation_percent == expected


@pytest.mark.parametrize(
    "changed",
    [
        {"source": " rapt"},
        {"device_id": "not-a-uuid"},
        {"reading_id": " padded-rapt-id "},
    ],
)
def test_known_rapt_stream_identifiers_must_preserve_rapt_contracts(
    changed: dict[str, object],
) -> None:
    first, latest = _pair()
    values = {"source": "rapt"} | changed
    invalid_first = replace(first, **cast(Any, values))
    invalid_latest = replace(latest, **cast(Any, values))

    with pytest.raises(FermentationProgressValidationError):
        evaluate_fermentation_progress(
            (invalid_first, invalid_latest),
            gravity_unit="sg",
            original_gravity="1.050",
            window_start_ns=WINDOW_START_NS,
            window_end_ns=WINDOW_END_NS,
        )


@pytest.mark.parametrize("reading_id", [" padded-brewforge-id ", "bad\nsource-id"])
def test_brewforge_opaque_reading_identifiers_remain_non_padded_and_printable(
    reading_id: str,
) -> None:
    first, latest = _pair()
    invalid_first = replace(first, reading_id=reading_id)

    with pytest.raises(FermentationProgressValidationError):
        evaluate_fermentation_progress(
            (invalid_first, latest),
            gravity_unit="sg",
            original_gravity="1.050",
            window_start_ns=WINDOW_START_NS,
            window_end_ns=WINDOW_END_NS,
        )


@pytest.mark.parametrize("gravity_unit", [None, "sg-times-1000", "SG"])
def test_raw_or_undeclared_gravity_units_are_refused(gravity_unit: object) -> None:
    raw = parse_rapt_telemetry(
        [
            {
                "id": "00000000-0000-0000-0000-000000000012",
                "createdOn": "2026-07-17T00:00:00Z",
                "temperature": 20.0,
                "gravity": 1050,
                "battery": 90.0,
                "rssi": -50.0,
            }
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=DEVICE,
    )[0]
    with pytest.raises(FermentationProgressValidationError):
        evaluate_fermentation_progress(
            (
                raw,
                replace(
                    raw,
                    reading_id="00000000-0000-0000-0000-000000000013",
                    observed_at=datetime(2026, 7, 18, tzinfo=UTC),
                ),
            ),
            gravity_unit=gravity_unit,  # type: ignore[arg-type]
            original_gravity="1.050",
            window_start_ns=WINDOW_START_NS,
            window_end_ns=WINDOW_END_NS,
        )


@pytest.mark.parametrize(
    "changed",
    [
        {"gravity_raw": None},
        {"gravity_raw": float("nan")},
        {"gravity_raw": 1.201},
        {"gravity_raw": 0.999},
        {"reading_id": "first"},
        {"device_id": "other"},
        {"observed_at": datetime(2026, 7, 19, tzinfo=UTC)},
    ],
)
def test_malformed_selection_fails_without_partial_evidence(changed: dict[str, object]) -> None:
    first, unmodified_latest = _pair()
    latest = replace(unmodified_latest, **cast(Any, changed))
    with pytest.raises(FermentationProgressValidationError):
        evaluate_fermentation_progress(
            (first, latest),
            gravity_unit="sg",
            original_gravity="1.050",
            window_start_ns=WINDOW_START_NS,
            window_end_ns=WINDOW_END_NS,
        )


@pytest.mark.parametrize("original", ["1", "1.0e0", "1.000", "1.201", "0.999", None])
def test_original_gravity_is_explicit_canonical_and_in_attenuation_domain(original: object) -> None:
    with pytest.raises(FermentationProgressValidationError):
        evaluate_fermentation_progress(
            _pair(),
            gravity_unit="sg",
            original_gravity=original,  # type: ignore[arg-type]
            window_start_ns=WINDOW_START_NS,
            window_end_ns=WINDOW_END_NS,
        )


def test_public_api_is_offline_observational_and_not_a_completion_claim() -> None:
    source = Path(__file__).parents[1] / "src/forge_companion/fermentation_progress.py"
    text = source.read_text(encoding="utf-8")
    assert "not a forecast" in text
    assert "completion" in text
    assert "packaging safety" in text
    assert "forge_companion.client" not in text
    assert "forge_companion.rapt" not in text
