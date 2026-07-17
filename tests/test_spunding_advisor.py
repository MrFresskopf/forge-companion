from datetime import UTC, datetime, timedelta

import pytest

from forge_companion.fermentation import analyze_readings, parse_readings
from forge_companion.spunding_advisor import (
    AdvisorConfig,
    AdvisorStatus,
    advise_spunding,
    advise_spunding_payload,
)

NOW = datetime(2026, 7, 17, 10, tzinfo=UTC)
CONFIG = AdvisorConfig(
    trigger_sg=1.012,
    max_age=timedelta(minutes=90),
    max_gap=timedelta(minutes=120),
    confirmations=2,
)


def test_config_accepts_explicit_safe_bounds() -> None:
    config = AdvisorConfig(
        trigger_sg=1.012,
        max_age=timedelta(minutes=90),
        max_gap=timedelta(minutes=120),
        confirmations=2,
    )

    assert config.trigger_sg == 1.012
    assert AdvisorStatus.CONDITION_MET.value == "CONDITION_MET"


@pytest.mark.parametrize("trigger", [float("nan"), float("inf"), 0.8999, 1.2001])
def test_config_rejects_invalid_trigger_sg(trigger: float) -> None:
    with pytest.raises(ValueError, match="trigger SG"):
        AdvisorConfig(
            trigger_sg=trigger,
            max_age=timedelta(minutes=90),
            max_gap=timedelta(minutes=120),
            confirmations=2,
        )


@pytest.mark.parametrize("field", ["max_age", "max_gap"])
def test_config_rejects_non_positive_durations(field: str) -> None:
    values = {
        "trigger_sg": 1.012,
        "max_age": timedelta(minutes=90),
        "max_gap": timedelta(minutes=120),
        "confirmations": 2,
    }
    values[field] = timedelta(0)

    with pytest.raises(ValueError, match=field.replace("_", " ")):
        AdvisorConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("confirmations", [1, 6, True])
def test_config_rejects_confirmations_outside_two_to_five(
    confirmations: int,
) -> None:
    with pytest.raises(ValueError, match="confirmations"):
        AdvisorConfig(
            trigger_sg=1.012,
            max_age=timedelta(minutes=90),
            max_gap=timedelta(minutes=120),
            confirmations=confirmations,
        )


def test_advisor_payload_makes_no_decision_for_malformed_envelope() -> None:
    result = advise_spunding_payload(
        {"unexpected": []},
        config=CONFIG,
        as_of=NOW,
    )

    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "readings response is malformed"
    assert result.evidence == ()


def test_advisor_makes_no_decision_without_accepted_readings() -> None:
    parsed = parse_readings({"data": []})

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "no valid fermentation readings"
    assert result.evidence == ()


def test_advisor_makes_no_decision_when_any_reading_was_rejected() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "good", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011},
                {"id": "bad", "timestamp": "not-a-date", "gravity": 1.010},
            ]
        }
    )

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "one or more readings were rejected"
    assert result.evidence == ()


def test_advisor_makes_no_decision_for_implausible_gravity() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "a", "timestamp": "2026-07-17T09:00:00Z", "gravity": 0.5},
                {"id": "b", "timestamp": "2026-07-17T10:00:00Z", "gravity": 0.4},
            ]
        }
    )

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "one or more gravity readings are outside plausible SG bounds"
    assert result.evidence == ()


def test_advisor_makes_no_decision_for_conflicting_timestamps() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "a", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011},
                {"id": "b", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010},
            ]
        }
    )
    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)
    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "readings contain timestamp conflicts"


def test_advisor_makes_no_decision_with_too_few_confirmations() -> None:
    parsed = parse_readings(
        {"data": [{"id": "a", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.011}]}
    )
    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)
    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "insufficient confirmation readings"


def test_advisor_makes_no_decision_for_future_latest_reading() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "a", "timestamp": "2026-07-17T10:00:00Z", "gravity": 1.011},
                {"id": "b", "timestamp": "2026-07-17T11:00:00Z", "gravity": 1.010},
            ]
        }
    )
    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)
    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "latest reading is after advisor time"


def test_advisor_makes_no_decision_for_stale_latest_reading() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "a", "timestamp": "2026-07-17T07:00:00Z", "gravity": 1.011},
                {"id": "b", "timestamp": "2026-07-17T08:00:00Z", "gravity": 1.010},
            ]
        }
    )
    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)
    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "latest reading is stale"
    assert result.latest_age == timedelta(hours=2)


def test_advisor_makes_no_decision_for_excessive_confirmation_gap() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "a", "timestamp": "2026-07-17T06:59:00Z", "gravity": 1.011},
                {"id": "b", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.010},
            ]
        }
    )
    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)
    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "confirmation gap exceeds configured maximum"
    assert result.largest_confirmation_gap == timedelta(hours=2, minutes=1)


def test_advisor_rejects_naive_evaluation_time() -> None:
    parsed = parse_readings({"data": []})
    with pytest.raises(ValueError, match="advisor time must include a timezone"):
        advise_spunding(parsed, config=CONFIG, as_of=datetime(2026, 7, 17, 10))


def test_advisor_waits_until_all_confirmation_readings_meet_threshold() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "r1", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.0130},
                {"id": "r2", "timestamp": "2026-07-17T10:00:00Z", "gravity": 1.0118},
            ]
        }
    )

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.status is AdvisorStatus.WAIT
    assert result.reason == "not all confirmation readings are at or below trigger SG"
    assert [item.reading_id for item in result.evidence] == ["r1", "r2"]


def test_advisor_reports_condition_met_after_all_confirmations() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "r1", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.0119},
                {"id": "r2", "timestamp": "2026-07-17T10:00:00Z", "gravity": 1.0117},
            ]
        }
    )

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.status is AdvisorStatus.CONDITION_MET
    assert result.reason == "all confirmation readings are at or below trigger SG"
    assert [item.gravity for item in result.evidence] == [1.0119, 1.0117]


def test_advisor_includes_existing_descriptive_gravity_trend() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "r0", "timestamp": "2026-07-17T02:00:00Z", "gravity": 1.0200},
                {"id": "r1", "timestamp": "2026-07-17T06:00:00Z", "gravity": 1.0160},
                {"id": "r2", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.0119},
                {"id": "r3", "timestamp": "2026-07-17T10:00:00Z", "gravity": 1.0117},
            ]
        }
    )
    expected = analyze_readings(parsed, report_time=NOW)

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.gravity_slope_per_day == expected.gravity_slope_per_day
    assert result.gravity_slope_per_day is not None
    assert result.trend_note == expected.trend_note


def test_advisor_preserves_noise_floor_explanation() -> None:
    parsed = parse_readings(
        {
            "data": [
                {"id": "r0", "timestamp": "2026-07-17T04:00:00Z", "gravity": 1.0119},
                {"id": "r1", "timestamp": "2026-07-17T09:00:00Z", "gravity": 1.0118},
                {"id": "r2", "timestamp": "2026-07-17T10:00:00Z", "gravity": 1.0117},
            ]
        }
    )

    result = advise_spunding(parsed, config=CONFIG, as_of=NOW)

    assert result.gravity_slope_per_day is None
    assert result.trend_note == "gravity range is within the configured RAPT noise floor"


def test_advisor_payload_makes_no_decision_for_timestamp_normalization_overflow() -> None:
    result = advise_spunding_payload(
        {
            "data": [
                {
                    "id": "boundary",
                    "timestamp": "0001-01-01T00:00:00+23:59",
                    "gravity": 1.010,
                }
            ]
        },
        config=CONFIG,
        as_of=NOW,
    )

    assert result.status is AdvisorStatus.NO_DECISION
    assert result.reason == "no valid fermentation readings"
    assert result.evidence == ()
