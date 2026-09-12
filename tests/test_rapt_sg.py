from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal, Inexact, Rounded, localcontext
from math import nextafter

import pytest

from forge_companion.rapt_sg import normalize_rapt_sg
from forge_companion.sg_trend import exact_ns
from forge_companion.spunding_advisor import AdvisorStatus, advise_telemetry_sg
from forge_companion.telemetry import DeviceKind, TelemetryValidationError, parse_rapt_telemetry

DEVICE = "00000000-0000-0000-0000-000000000001"


def parsed(*gravities):
    return parse_rapt_telemetry(
        [
            {
                "id": f"00000000-0000-0000-0000-{index + 10:012d}",
                "createdOn": f"2026-07-17T10:00:00.000000{index + 1}Z",
                "temperature": 20.25,
                "gravity": gravity,
                "gravityVelocity": -2.5,
                "battery": 85.5,
                "rssi": -61,
            }
            for index, gravity in enumerate(gravities)
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=DEVICE,
    )


def test_explicit_conversion_preserves_metadata_missing_sg_and_input():
    raw = parsed(1012.5, None)
    before = [asdict(item) for item in raw]
    normalized = normalize_rapt_sg(raw, gravity_interpretation="sg-times-1000")
    assert isinstance(normalized, tuple)
    assert [item.gravity_raw for item in normalized] == [1.0125, None]
    for original, result in zip(raw, normalized, strict=True):
        assert result is not original
        expected = asdict(original)
        expected["gravity_raw"] = result.gravity_raw
        assert asdict(result) == expected
        assert result.observed_at_exact == original.observed_at_exact
    assert [asdict(item) for item in raw] == before


@pytest.mark.parametrize("interpretation", [None, "sg", "", "SG-times-1000", True])
def test_interpretation_is_required_and_never_inferred(interpretation):
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(parsed(1012), gravity_interpretation=interpretation)


def test_omitted_interpretation_is_not_a_default():
    with pytest.raises(TypeError):
        normalize_rapt_sg(parsed(1012))


@pytest.mark.parametrize("gravity", [1012, None])
def test_marker_rejects_double_normalization_even_without_gravity(gravity):
    normalized = normalize_rapt_sg(parsed(gravity), gravity_interpretation="sg-times-1000")
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(normalized, gravity_interpretation="sg-times-1000")


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "other"},
        {"device_kind": DeviceKind.TEMPERATURE_CONTROLLER},
        {"device_kind": "hydrometer"},
        {"device_id": "different"},
    ],
)
def test_entire_stream_rejected_for_wrong_source_kind_or_mixed_device(changes):
    raw = parsed(1012, 1011)
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(
            (raw[0], replace(raw[1], **changes)), gravity_interpretation="sg-times-1000"
        )


@pytest.mark.parametrize(
    "gravity",
    [
        True,
        False,
        float("nan"),
        float("inf"),
        -float("inf"),
        899.99,
        1200.01,
        1.012,
        "1012",
        Decimal("1012"),
        10**400,
    ],
    ids=[
        "true",
        "false",
        "nan",
        "inf",
        "negative-inf",
        "low",
        "high",
        "already-sg",
        "string",
        "decimal",
        "huge-int",
    ],
)
def test_invalid_gravity_fails_closed(gravity):
    raw = parsed(1012, 1011)
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(
            (raw[0], replace(raw[1], gravity_raw=gravity)),
            gravity_interpretation="sg-times-1000",
        )


def test_precision_loss_is_rejected_not_silently_merged():
    raw = parsed(1012, nextafter(1012.0, float("inf")))
    raw = (raw[0], replace(raw[1], observed_at_submicrosecond_ns=100))
    # These two distinct raw float values collapse to the same SG float.
    assert raw[0].gravity_raw != raw[1].gravity_raw
    assert raw[0].gravity_raw / 1000 == raw[1].gravity_raw / 1000
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(raw, gravity_interpretation="sg-times-1000")


@pytest.mark.parametrize("precision", [1, 2, 28, 60])
def test_conversion_does_not_depend_on_decimal_context(precision):
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = normalize_rapt_sg(
            parsed(900, 1012.5, 1200), gravity_interpretation="sg-times-1000"
        )
    assert [item.gravity_raw for item in result] == [0.9, 1.0125, 1.2]


@pytest.mark.parametrize("readings", [None, [], [None], (None,)])
def test_malformed_collection_rejected(readings):
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(readings, gravity_interpretation="sg-times-1000")


@pytest.mark.parametrize(
    "changes",
    [
        {"device_id": ""},
        {"reading_id": "bad"},
        {"observed_at": datetime(2026, 1, 1)},
        {"observed_at_submicrosecond_ns": True},
        {"observed_at_submicrosecond_ns": 101},
        {"observed_at_submicrosecond_ns": 1000},
        {"temperature_c": True},
        {"gravity_velocity_raw": float("inf")},
    ],
)
def test_malformed_metadata_rejected(changes):
    with pytest.raises(TelemetryValidationError):
        normalize_rapt_sg(
            (replace(parsed(1012)[0], **changes),), gravity_interpretation="sg-times-1000"
        )


def evaluate(readings, **overrides):
    policy = dict(
        gravity_unit="sg",
        now_ns=exact_ns(parsed(1012, 1011)[-1]),
        trigger_sg=Decimal("1.012"),
        max_age_ns=100,
        max_gap_ns=100,
        confirmations=2,
    )
    policy.update(overrides)
    return advise_telemetry_sg(readings, **policy)


@pytest.mark.parametrize(
    ("gravities", "status"),
    [
        ((1013, 1011), AdvisorStatus.WAIT),
        ((1012, 1011), AdvisorStatus.CONDITION_MET),
        ((1012, None), AdvisorStatus.NO_DECISION),
        ((None, None), AdvisorStatus.NO_DECISION),
        ((), AdvisorStatus.NO_DECISION),
        ((1012,), AdvisorStatus.NO_DECISION),
    ],
)
def test_real_parser_bridge_existing_advisor(gravities, status):
    raw = parsed(*gravities)
    assert evaluate(raw) is AdvisorStatus.NO_DECISION
    converted = normalize_rapt_sg(raw, gravity_interpretation="sg-times-1000")
    assert evaluate(converted) is status


@pytest.mark.parametrize(
    ("age", "gap", "status"),
    [
        (100, 100, AdvisorStatus.CONDITION_MET),
        (200, 100, AdvisorStatus.NO_DECISION),
        (-100, 100, AdvisorStatus.NO_DECISION),
        (0, 99, AdvisorStatus.NO_DECISION),
    ],
)
def test_exact_100ns_age_and_gap_boundaries(age, gap, status):
    readings = normalize_rapt_sg(parsed(1012, 1011), gravity_interpretation="sg-times-1000")
    assert exact_ns(readings[1]) - exact_ns(readings[0]) == 100
    assert evaluate(readings, now_ns=exact_ns(readings[-1]) + age, max_gap_ns=gap) is status


@pytest.mark.parametrize("conflict_index", [0, 1, 2])
def test_raw_conflicts_survive_conversion_at_every_position(conflict_index):
    raw = parsed(1012, 1012, 1012)
    conflict = replace(raw[conflict_index], reading_id=DEVICE, gravity_raw=1011)
    converted = normalize_rapt_sg((*raw, conflict), gravity_interpretation="sg-times-1000")
    assert evaluate(converted, now_ns=exact_ns(raw[-1])) is AdvisorStatus.NO_DECISION


def test_duplicate_inflation_and_reused_ids_do_not_supply_confirmation():
    raw = parsed(1012, 1012)
    duplicate = replace(raw[0], reading_id=raw[1].reading_id)
    converted = normalize_rapt_sg((raw[0], duplicate), gravity_interpretation="sg-times-1000")
    assert evaluate(converted) is AdvisorStatus.NO_DECISION
    reused = replace(raw[1], reading_id=raw[0].reading_id)
    converted = normalize_rapt_sg((raw[0], reused), gravity_interpretation="sg-times-1000")
    assert evaluate(converted) is AdvisorStatus.NO_DECISION


@pytest.mark.parametrize(
    "name", ["now_ns", "trigger_sg", "max_age_ns", "max_gap_ns", "confirmations", "gravity_unit"]
)
def test_existing_advisor_requires_every_policy_argument(name):
    policy = dict(
        gravity_unit="sg",
        now_ns=0,
        trigger_sg=Decimal("1.012"),
        max_age_ns=100,
        max_gap_ns=100,
        confirmations=2,
    )
    del policy[name]
    with pytest.raises(TypeError):
        advise_telemetry_sg((), **policy)
