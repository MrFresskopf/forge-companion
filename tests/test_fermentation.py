from datetime import UTC, datetime, timedelta

import pytest

from forge_companion.fermentation import analyze_readings, parse_readings


def test_parse_readings_accepts_observed_brewforge_fields() -> None:
    payload = {
        "data": [
            {
                "id": "reading-1",
                "timestamp": "2026-07-17T08:00:00.000Z",
                "gravity": 1.0123,
                "temperature": 29.4,
                "pressure": None,
                "ph": None,
                "comment": "RAPT Pill",
            }
        ]
    }

    result = parse_readings(payload)

    assert result.rejected == ()
    assert len(result.readings) == 1
    reading = result.readings[0]
    assert reading.id == "reading-1"
    assert reading.timestamp == datetime(2026, 7, 17, 8, tzinfo=UTC)
    assert reading.gravity == 1.0123
    assert reading.temperature == 29.4
    assert reading.pressure is None
    assert reading.ph is None
    assert reading.comment == "RAPT Pill"


def test_parse_readings_sorts_out_of_order_records_chronologically() -> None:
    payload = {
        "data": [
            {"id": "late", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011},
            {"id": "early", "timestamp": "2026-07-17T08:00:00Z", "gravity": 1.012},
        ]
    }

    result = parse_readings(payload)

    assert [reading.id for reading in result.readings] == ["early", "late"]


def test_parse_readings_rejects_non_finite_gravity_without_losing_valid_records() -> None:
    payload = {
        "data": [
            {"id": "bad", "timestamp": "2026-07-17T08:00:00Z", "gravity": float("nan")},
            {"id": "good", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011},
        ]
    }

    result = parse_readings(payload)

    assert [reading.id for reading in result.readings] == ["good"]
    assert result.rejected == ("reading 0: gravity must be a finite number",)


def test_parse_readings_rejects_oversized_integer_without_losing_valid_records() -> None:
    payload = {
        "data": [
            {"id": "bad", "timestamp": "2026-07-17T08:00:00Z", "gravity": 10**10_000},
            {"id": "good", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011},
        ]
    }

    result = parse_readings(payload)

    assert [reading.id for reading in result.readings] == ["good"]
    assert result.rejected == ("reading 0: gravity must be a finite number",)


def test_analyze_readings_computes_descriptive_metrics() -> None:
    parsed = parse_readings(
        {
            "data": [
                {
                    "id": "1",
                    "timestamp": "2026-07-17T06:00:00Z",
                    "gravity": 1.014,
                    "temperature": 28.0,
                },
                {
                    "id": "2",
                    "timestamp": "2026-07-17T07:00:00Z",
                    "gravity": 1.012,
                    "temperature": 29.0,
                },
                {
                    "id": "3",
                    "timestamp": "2026-07-17T09:00:00Z",
                    "gravity": 1.010,
                    "temperature": 30.0,
                },
                {"id": "bad", "timestamp": "not-a-date", "gravity": 1.0},
            ]
        }
    )

    metrics = analyze_readings(parsed, report_time=datetime(2026, 7, 17, 10, tzinfo=UTC))

    assert metrics.accepted_count == 3
    assert metrics.rejected_count == 1
    assert metrics.started_at == datetime(2026, 7, 17, 6, tzinfo=UTC)
    assert metrics.ended_at == datetime(2026, 7, 17, 9, tzinfo=UTC)
    assert metrics.duration == timedelta(hours=3)
    assert metrics.latest_gravity == 1.010
    assert metrics.gravity_delta == -0.004
    assert metrics.latest_temperature == 30.0
    assert metrics.minimum_temperature == 28.0
    assert metrics.maximum_temperature == 30.0
    assert metrics.largest_gap == timedelta(hours=2)
    assert metrics.freshness == timedelta(hours=1)


def test_analyze_readings_fits_24_hour_gravity_slope() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "1", "timestamp": "2026-07-16T09:00:00Z", "gravity": 1.020},
                {"id": "2", "timestamp": "2026-07-16T21:00:00Z", "gravity": 1.015},
                {"id": "3", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010},
            ]
        }
    )

    metrics = analyze_readings(parsed, report_time=datetime(2026, 7, 17, 10, tzinfo=UTC))

    assert metrics.gravity_slope_per_day == -0.01
    assert metrics.trend_note == "least-squares slope over the latest 24 hours"


def test_analyze_readings_rejects_non_finite_gravity_change() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "1", "timestamp": "2026-07-17T08:00:00Z", "gravity": -1e308},
                {"id": "2", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1e308},
            ]
        }
    )

    with pytest.raises(ValueError, match="gravity change is not finite"):
        analyze_readings(parsed, report_time=datetime(2026, 7, 17, 10, tzinfo=UTC))


def test_parse_readings_deduplicates_exact_records_but_retains_timestamp_conflicts() -> None:
    exact = {"id": "1", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010}
    payload = {
        "data": [
            exact,
            dict(exact),
            {"id": "2", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011},
        ]
    }

    result = parse_readings(payload)

    assert [reading.id for reading in result.readings] == ["1", "2"]
    assert result.rejected == ("reading 1: exact duplicate",)
    assert result.conflicting_timestamps == (datetime(2026, 7, 17, 9, tzinfo=UTC),)


def test_parse_readings_rejects_missing_identifier() -> None:
    result = parse_readings({"data": [{"timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010}]})

    assert result.readings == ()
    assert result.rejected == ("reading 0: id is missing",)


def test_parse_readings_does_not_include_invalid_timestamp_data_in_reason() -> None:
    result = parse_readings(
        {
            "data": [
                {
                    "id": "bad",
                    "timestamp": "<script>|raw-record-marker",
                    "gravity": 1.010,
                }
            ]
        }
    )

    assert result.readings == ()
    assert result.rejected == ("reading 0: timestamp is not valid ISO 8601",)


def test_parse_readings_rejects_timestamp_that_overflows_utc_normalization() -> None:
    result = parse_readings(
        {
            "data": [
                {
                    "id": "boundary",
                    "timestamp": "0001-01-01T00:00:00+23:59",
                    "gravity": 1.010,
                }
            ]
        }
    )

    assert result.readings == ()
    assert len(result.rejected) == 1
    assert result.rejected[0].startswith("reading 0:")


def test_analyze_readings_rejects_future_latest_timestamp() -> None:
    parsed = parse_readings(
        {"data": [{"id": "1", "timestamp": "2026-07-17T10:00:00Z", "gravity": 1.010}]}
    )

    with pytest.raises(ValueError, match="latest reading is after report time"):
        analyze_readings(parsed, report_time=datetime(2026, 7, 17, 9, tzinfo=UTC))
