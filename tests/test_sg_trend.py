from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from forge_companion.sg_trend import build_trend_report
from forge_companion.telemetry import DeviceKind, TelemetryReading, parse_rapt_telemetry

PILL = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("remainder", [1, 2], ids=["equal-duplicate", "distinct-100ns"])
def test_quality_counts_distinct_exact_instants_from_real_parser(remainder):
    rows = parse_rapt_telemetry(
        [
            {
                "id": f"{i:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": timestamp,
                "temperature": 20.0,
                "gravity": gravity,
                "battery": 90.0,
                "rssi": -50.0,
            }
            for i, (timestamp, gravity) in enumerate(
                [
                    ("2026-09-02T00:00:00.0000001Z", 1050),
                    (f"2026-09-02T00:00:00.000000{remainder}Z", 1050 if remainder == 1 else 1045),
                    ("2026-09-02T01:00:00Z", 1040),
                ],
                1,
            )
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=PILL,
    )
    assert len(rows) == 3
    segment = build_trend_report(
        rows,
        phase_history=[{"phase": "fermentation", "start_date": "2026-09-01"}],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 2, 1, tzinfo=UTC),
    ).segments[0]
    assert segment.sg_status == "OK"
    assert segment.endpoint_trend == ("NO_TREND" if remainder == 1 else "FALLING")


@pytest.mark.parametrize("position", [0, 1, 2], ids=["first", "interior", "latest"])
@pytest.mark.parametrize("swap_ids", [False, True])
def test_conflicting_exact_timestamp_fails_closed_through_real_parser(position, swap_ids):
    payload = [
        {
            "id": f"{i:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "createdOn": f"2026-09-02T00:{minute:02d}:00.0000001Z",
            "temperature": 20.0,
            "gravity": gravity,
            "battery": 90.0,
            "rssi": -50.0,
        }
        for i, (minute, gravity) in enumerate([(0, 1050), (30, 1045), (59, 1040)], 1)
    ]
    payload.append(
        {
            **payload[position],
            "id": "ffffffff-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "gravity": 1030 if position == 0 else 1060,
        }
    )
    if swap_ids:
        payload[position]["id"], payload[-1]["id"] = payload[-1]["id"], payload[position]["id"]
    rows = parse_rapt_telemetry(payload, kind=DeviceKind.HYDROMETER, device_id=PILL)
    assert len(rows) == 4  # Read-only consumers must retain both distinct-ID observations.
    segment = build_trend_report(
        rows,
        phase_history=[{"phase": "fermentation", "start_date": "2026-09-01"}],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 2, 1, tzinfo=UTC),
    ).segments[0]
    assert segment.endpoint_trend == "NO_TREND"
    assert segment.sg_status == "CONFLICTING_SG_AT_TIMESTAMP"
    assert segment.first_sg == (None if position == 0 else Decimal("1.050"))
    assert segment.latest_sg == (None if position == 2 else Decimal("1.040"))
    assert segment.delta_sg is None
    assert segment.rate_sg_per_day is None


def reading(timestamp: datetime, gravity: float | None, *, remainder: int = 0, key: str = "a"):
    return TelemetryReading(
        source="rapt",
        device_kind=DeviceKind.HYDROMETER,
        device_id=PILL,
        reading_id=f"{key * 8}-{key * 4}-4{key * 3}-8{key * 3}-{key * 12}",
        observed_at=timestamp,
        observed_at_submicrosecond_ns=remainder,
        temperature_c=20.0,
        gravity_raw=gravity,
        gravity_velocity_raw=999.0,
        target_temperature_c=None,
        battery_percent=None,
        rssi=None,
    )


def test_phase_boundary_days_are_excluded_in_supplied_zone_and_segments_stay_separate():
    rows = (
        reading(datetime(2026, 10, 24, 22, 30, tzinfo=UTC), 1050.0),  # Oct 25 locally
        reading(datetime(2026, 10, 25, 23, 30, tzinfo=UTC), 1040.0, key="b"),
        reading(datetime(2026, 10, 26, 23, 30, tzinfo=UTC), 1030.0, key="c"),  # transition day
        reading(datetime(2026, 10, 27, 23, 30, tzinfo=UTC), 1020.0, key="d"),
    )
    report = build_trend_report(
        rows,
        phase_history=[
            {"phase": "fermentation", "start_date": "2026-10-25"},
            {"phase": "cold-crash", "start_date": "2026-10-27"},
        ],
        phase_timezone=ZoneInfo("Europe/Berlin"),
        query_end=datetime(2026, 10, 28, 1, tzinfo=UTC),
    )
    assert report.excluded_boundary_observations == 2
    assert [item.phase for item in report.segments] == ["fermentation", "cold-crash"]
    assert [item.observations for item in report.segments] == [1, 1]
    assert all(item.endpoint_trend == "NO_TREND" for item in report.segments)


def test_exact_100ns_gap_duration_decimal_delta_and_actual_duration_rate():
    first = reading(datetime(2026, 9, 2, 0, tzinfo=UTC), 1050.0, remainder=100)
    middle = reading(datetime(2026, 9, 2, 0, 30, tzinfo=UTC), 1049.5, key="c")
    latest = reading(datetime(2026, 9, 2, 1, tzinfo=UTC), 1049.0, remainder=200, key="b")
    report = build_trend_report(
        (latest, middle, first),
        phase_history=[{"phase": "fermentation", "start_date": "2026-09-01"}],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 2, 1, 30, tzinfo=UTC),
    )
    segment = report.segments[0]
    assert segment.duration_ns == 3_600_000_000_100
    assert segment.max_gap_ns == 1_800_000_000_200
    assert segment.first_sg == Decimal("1.05")
    assert segment.delta_sg == Decimal("-0.001")
    assert segment.rate_sg_per_day == Decimal("-0.02399999999933333333335185185")
    assert segment.endpoint_trend == "FALLING"


def test_missing_measurement_large_gap_and_end_relative_age_are_incomplete():
    rows = (
        reading(datetime(2026, 9, 2, 0, tzinfo=UTC), 1050.0),
        reading(datetime(2026, 9, 2, 0, 30, tzinfo=UTC), None, key="b"),
        reading(datetime(2026, 9, 2, 2, tzinfo=UTC), 1049.0, key="c"),
    )
    segment = build_trend_report(
        rows,
        phase_history=[{"phase": "fermentation", "start_date": "2026-09-01"}],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 2, 4, tzinfo=UTC),
    ).segments[0]
    assert segment.missing_sg == 1
    assert segment.gap_status == "LARGE_GAPS"
    assert segment.coverage_status == "STALE"
    assert segment.endpoint_trend == "NO_TREND"


def test_narrow_source_preserves_missing_gravity_without_needing_velocity():
    (row,) = parse_rapt_telemetry(
        [
            {
                "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-02T00:00:00Z",
                "temperature": 20.0,
                "gravity": None,
                "battery": 90.0,
                "rssi": -50.0,
            }
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=PILL,
    )
    assert row.gravity_raw is None
    assert row.gravity_velocity_raw is None


def test_observations_earlier_than_context_start_are_not_attributed():
    report = build_trend_report(
        (reading(datetime(2026, 8, 31, 12, tzinfo=UTC), 1050.0),),
        phase_history=[{"phase": "fermentation", "start_date": "2026-09-01"}],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 1, tzinfo=UTC),
    )
    assert report.unattributed_before_start == 1
    assert report.segments == ()


def test_return_to_same_phase_creates_a_new_segment_instead_of_combining():
    report = build_trend_report(
        (
            reading(datetime(2026, 9, 2, 12, tzinfo=UTC), 1050.0),
            reading(datetime(2026, 9, 4, 12, tzinfo=UTC), 1040.0, key="b"),
            reading(datetime(2026, 9, 6, 12, tzinfo=UTC), 1030.0, key="c"),
        ),
        phase_history=[
            {"phase": "fermentation", "start_date": "2026-09-01"},
            {"phase": "cold-crash", "start_date": "2026-09-03"},
            {"phase": "fermentation", "start_date": "2026-09-05"},
        ],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 6, 13, tzinfo=UTC),
    )
    assert [(item.phase, item.sequence) for item in report.segments] == [
        ("fermentation", 1),
        ("cold-crash", 2),
        ("fermentation", 3),
    ]


def test_boundary_only_response_is_explicitly_empty_not_a_failure():
    report = build_trend_report(
        (reading(datetime(2026, 9, 1, 12, tzinfo=UTC), 1050.0),),
        phase_history=[{"phase": "fermentation", "start_date": "2026-09-01"}],
        phase_timezone=ZoneInfo("UTC"),
        query_end=datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert report.excluded_boundary_observations == 1
    assert report.segments == ()
