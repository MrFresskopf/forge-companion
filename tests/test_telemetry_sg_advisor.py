"""Offline, source-neutral threshold advice; no actuator permission."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from itertools import permutations

import pytest

from forge_companion import spunding_advisor
from forge_companion.sg_trend import exact_ns
from forge_companion.telemetry import DeviceKind, TelemetryReading

BASE = TelemetryReading(
    source="test-source",
    device_kind=DeviceKind.HYDROMETER,
    device_id="device",
    reading_id="first",
    observed_at=datetime(2026, 1, 1, tzinfo=UTC),
    temperature_c=None,
    gravity_raw=1.02,
    gravity_velocity_raw=None,
    target_temperature_c=None,
    battery_percent=None,
    rssi=None,
)
SECOND = replace(BASE, reading_id="second", observed_at_submicrosecond_ns=100)


def advise(readings=(BASE, SECOND), **overrides):
    policy = dict(
        gravity_unit="sg",
        now_ns=exact_ns(SECOND),
        trigger_sg=Decimal("1.020"),
        max_age_ns=100,
        max_gap_ns=100,
        confirmations=2,
    )
    policy.update(overrides)
    return spunding_advisor.advise_telemetry_sg(readings, **policy)


def test_above_threshold_is_wait():
    assert advise(trigger_sg=Decimal("1.019999999999999999999999999999")) is (
        spunding_advisor.AdvisorStatus.WAIT
    )


def test_uninterpreted_gravity_is_no_decision():
    assert advise(gravity_unit=None) is spunding_advisor.AdvisorStatus.NO_DECISION


@pytest.mark.parametrize(
    "field,value",
    [
        ("trigger_sg", value)
        for value in [
            True,
            None,
            1.02,
            "1.02",
            Decimal("NaN"),
            Decimal("sNaN"),
            Decimal("Infinity"),
            Decimal("0.8999"),
            Decimal("1.2001"),
        ]
    ]
    + [
        (field, value)
        for field in ["max_age_ns", "max_gap_ns"]
        for value in [True, None, 0, -1, 1.0, float("nan"), float("inf")]
    ]
    + [("confirmations", value) for value in [True, None, 1, 6, 2.0]]
    + [("now_ns", value) for value in [True, None, 1.0, float("inf")]],
)
def test_invalid_policy_raises_value_error(field, value):
    with pytest.raises(ValueError):
        advise(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("gravity_raw", value)
        for value in [None, True, "1.02", float("nan"), float("inf"), 0.89, 1.21]
    ]
    + [
        ("source", ""),
        ("device_id", ""),
        ("reading_id", None),
        ("device_kind", "hydrometer"),
        ("observed_at", datetime(2026, 1, 1)),
        ("observed_at", None),
    ]
    + [("observed_at_submicrosecond_ns", value) for value in [True, -100, 1000, 1, 100.0]],
)
def test_malformed_reading_fails_closed(field, value):
    assert advise((replace(BASE, **{field: value}), SECOND)) is (
        spunding_advisor.AdvisorStatus.NO_DECISION
    )


@pytest.mark.parametrize("readings", [None, (), (None,), "bad"])
def test_missing_or_malformed_collection_fails_closed(readings):
    assert advise(readings) is spunding_advisor.AdvisorStatus.NO_DECISION


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "other"),
        ("device_id", "other"),
        ("device_kind", DeviceKind.TEMPERATURE_CONTROLLER),
    ],
)
def test_mixed_streams_fail_closed(field, value):
    assert advise((BASE, replace(SECOND, **{field: value}))) is (
        spunding_advisor.AdvisorStatus.NO_DECISION
    )


def test_same_instant_conflicting_gravity_fails_closed():
    conflict = replace(BASE, reading_id="conflict", gravity_raw=1.01)
    assert advise((BASE, SECOND, conflict)) is spunding_advisor.AdvisorStatus.NO_DECISION


def test_duplicates_cannot_inflate_confirmations():
    duplicate = replace(BASE, reading_id="duplicate")
    assert advise((BASE, duplicate)) is spunding_advisor.AdvisorStatus.NO_DECISION


@pytest.mark.parametrize(
    "policy",
    [
        {"now_ns": exact_ns(SECOND) - 100},
        {"now_ns": exact_ns(SECOND) + 200},
        {"max_gap_ns": 99},
        {"confirmations": 3},
    ],
)
def test_quality_gates_fail_closed(policy):
    assert advise(**policy) is spunding_advisor.AdvisorStatus.NO_DECISION


def test_only_latest_distinct_confirmations_determine_wait():
    older = replace(
        BASE, reading_id="older", observed_at=datetime(2025, 1, 1, tzinfo=UTC), gravity_raw=1.1
    )
    assert advise((older, BASE, SECOND)) is spunding_advisor.AdvisorStatus.CONDITION_MET


def test_reused_reading_id_at_different_instant_fails_closed():
    assert advise((BASE, replace(SECOND, reading_id=BASE.reading_id))) is (
        spunding_advisor.AdvisorStatus.NO_DECISION
    )


def test_threshold_equality_is_condition_met():
    assert advise() is spunding_advisor.AdvisorStatus.CONDITION_MET


def test_oversized_integer_gravity_fails_closed():
    assert advise((replace(BASE, gravity_raw=10**5000), SECOND)) is (
        spunding_advisor.AdvisorStatus.NO_DECISION
    )


@pytest.mark.parametrize("source", ["rapt", "brewforge", "unknown-local-source"])
@pytest.mark.parametrize(
    "trigger,status",
    [
        (Decimal("1.02"), spunding_advisor.AdvisorStatus.CONDITION_MET),
        (Decimal("1.01"), spunding_advisor.AdvisorStatus.WAIT),
    ],
)
def test_source_and_order_do_not_change_advice(source, trigger, status):
    observations = tuple(replace(item, source=source) for item in (BASE, SECOND, BASE))
    for ordered in permutations(observations):
        assert advise(ordered, trigger_sg=trigger) is status


@pytest.mark.parametrize(
    "age,status",
    [
        (0, spunding_advisor.AdvisorStatus.CONDITION_MET),
        (100, spunding_advisor.AdvisorStatus.CONDITION_MET),
        (200, spunding_advisor.AdvisorStatus.NO_DECISION),
        (-100, spunding_advisor.AdvisorStatus.NO_DECISION),
    ],
)
def test_exact_age_boundary(age, status):
    assert advise(now_ns=exact_ns(SECOND) + age) is status


def test_duplicate_distinct_ids_do_not_hide_confirmation_gap():
    duplicate = replace(SECOND, reading_id="duplicate")
    assert advise((BASE, SECOND, duplicate), confirmations=3) is (
        spunding_advisor.AdvisorStatus.NO_DECISION
    )


@pytest.mark.parametrize("unit", [None, "", "raw", "SG", "plato", True, 1])
def test_only_explicit_sg_interpretation_is_accepted(unit):
    assert advise(gravity_unit=unit) is spunding_advisor.AdvisorStatus.NO_DECISION


def test_offset_equivalent_instants_deduplicate():
    offset = timezone(timedelta(hours=2))
    duplicate = replace(BASE, reading_id="offset", observed_at=BASE.observed_at.astimezone(offset))
    assert advise((BASE, duplicate)) is spunding_advisor.AdvisorStatus.NO_DECISION
    assert advise((BASE, duplicate, SECOND)) is spunding_advisor.AdvisorStatus.CONDITION_MET


def test_gap_one_tick_beyond_limit_fails_closed():
    later = replace(SECOND, observed_at_submicrosecond_ns=200)
    assert advise((BASE, later), now_ns=exact_ns(later)) is (
        spunding_advisor.AdvisorStatus.NO_DECISION
    )


def test_conflict_is_checked_even_outside_latest_confirmation_selection():
    later = replace(SECOND, reading_id="later", observed_at_submicrosecond_ns=200)
    conflict = replace(BASE, reading_id="conflict", gravity_raw=1.01)
    for ordered in permutations((BASE, SECOND, later, conflict)):
        assert advise(ordered, now_ns=exact_ns(later)) is spunding_advisor.AdvisorStatus.NO_DECISION
