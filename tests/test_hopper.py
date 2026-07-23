import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from forge_companion.hopper import (
    HopperPlanValidationError,
    HopperStatus,
    arm_hopper_plan,
    create_hopper_plan,
    load_hopper_plan,
    simulate_hopper_plan,
    validate_hopper_plan,
    write_hopper_plan,
)

CREATED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
TRIGGER_AT = datetime(2026, 7, 23, 18, 0, tzinfo=UTC)
PLAN_ID = UUID("4f18737c-102f-4f52-a0c3-69aa2c3f7281")
BREW_ID = UUID("fce879bf-bf02-437a-ad7c-4cbaa4aaf881")


def _resign(payload: dict[str, object]) -> None:
    unsigned = deepcopy(payload)
    integrity = unsigned["integrity"]
    assert isinstance(integrity, dict)
    integrity.pop("digest", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    target_integrity = payload["integrity"]
    assert isinstance(target_integrity, dict)
    target_integrity["digest"] = sha256(canonical).hexdigest()


def test_create_plan_produces_valid_draft_for_simulation_only() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
        brew_id=BREW_ID,
    )

    summary = validate_hopper_plan(payload)

    assert summary.plan_id == str(PLAN_ID)
    assert summary.status is HopperStatus.DRAFT
    assert summary.trigger_at == TRIGGER_AT
    assert summary.pulse_duration_ms == 1500
    assert payload["action"] == {
        "kind": "simulated-pulse",
        "pulse_duration_ms": 1500,
    }
    assert payload["brew_id"] == str(BREW_ID)
    assert payload["integrity"]["algorithm"] == "sha256"


def test_plan_file_round_trip_is_atomic_and_strict(tmp_path: Path) -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    destination = tmp_path / "automation" / "hopper-plan.json"

    write_hopper_plan(payload, destination)
    loaded = load_hopper_plan(destination)

    assert loaded == payload
    assert list(destination.parent.glob(f".{destination.name}.*.tmp")) == []


def test_plan_loader_rejects_duplicate_json_keys_without_reflecting_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "private-plan.json"
    source.write_text(
        '{"format":"forge-companion-hopper-plan-v1","format":"private brew"}',
        encoding="utf-8",
    )

    with pytest.raises(HopperPlanValidationError) as captured:
        load_hopper_plan(source)

    assert "private-plan" not in str(captured.value)
    assert "private brew" not in str(captured.value)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_plan_loader_rejects_non_finite_json_numbers(tmp_path: Path, constant: str) -> None:
    source = tmp_path / "hopper-plan.json"
    source.write_text(f'{{"pulse":{constant}}}', encoding="utf-8")

    with pytest.raises(HopperPlanValidationError, match="invalid or unreadable"):
        load_hopper_plan(source)


def test_validation_rejects_plan_content_changed_without_new_digest() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    changed = deepcopy(payload)
    changed["action"]["pulse_duration_ms"] = 9000

    with pytest.raises(HopperPlanValidationError, match="integrity"):
        validate_hopper_plan(changed)


def test_validation_rejects_resigned_unknown_fields() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    payload["device_uri"] = "http://example.invalid"
    _resign(payload)

    with pytest.raises(HopperPlanValidationError, match="schema"):
        validate_hopper_plan(payload)


@pytest.mark.parametrize(
    "alternate",
    [
        "2026-07-22T12:00:00Z",
        "2026-07-22 12:00:00+00:00",
        "20260722T120000+00:00",
        "2026-07-22T12:00:00-00:00",
    ],
)
def test_validation_rejects_resigned_noncanonical_utc_timestamp(alternate: str) -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    payload["created_at"] = alternate
    payload["state"]["events"][0]["at"] = alternate
    _resign(payload)

    with pytest.raises(HopperPlanValidationError, match="schema"):
        validate_hopper_plan(payload)


def test_create_plan_rejects_unbounded_simulated_pulse() -> None:
    with pytest.raises(ValueError, match="at most 60000"):
        create_hopper_plan(
            trigger_at=TRIGGER_AT,
            pulse_duration_ms=60_001,
            now=CREATED_AT,
        )


def test_validation_rejects_resigned_skipped_state_transition() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    state = payload["state"]
    state["status"] = "PULSE_ACTIVE"
    state["events"].append(
        {"status": "PULSE_ACTIVE", "at": datetime(2026, 7, 23, 18, 1, tzinfo=UTC).isoformat()}
    )
    _resign(payload)

    with pytest.raises(HopperPlanValidationError, match="state history"):
        validate_hopper_plan(payload)


def test_arm_transitions_a_valid_draft_before_trigger_time() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )

    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    summary = validate_hopper_plan(armed)
    assert summary.status is HopperStatus.ARMED
    assert [event["status"] for event in armed["state"]["events"]] == ["DRAFT", "ARMED"]
    assert payload["state"]["status"] == "DRAFT"


def test_arm_rejects_transition_before_plan_creation() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
    )

    with pytest.raises(ValueError, match="before plan creation"):
        arm_hopper_plan(payload, at=datetime(2026, 7, 22, 11, 59, tzinfo=UTC))


def test_simulation_runs_armed_plan_once_and_locks_it() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    completed = simulate_hopper_plan(
        armed,
        at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
    )

    assert validate_hopper_plan(completed).status is HopperStatus.LOCKED
    assert [event["status"] for event in completed["state"]["events"]] == [
        "DRAFT",
        "ARMED",
        "FIRE_REQUESTED",
        "PULSE_ACTIVE",
        "VERIFIED_OFF",
        "LOCKED",
    ]
    pulse_started = datetime.fromisoformat(completed["state"]["events"][3]["at"])
    verified_off = datetime.fromisoformat(completed["state"]["events"][4]["at"])
    locked = datetime.fromisoformat(completed["state"]["events"][5]["at"])
    assert (verified_off - pulse_started).total_seconds() == 1.5
    assert locked == verified_off
    with pytest.raises(ValueError, match="only an armed"):
        simulate_hopper_plan(
            completed,
            at=datetime(2026, 7, 23, 18, 2, tzinfo=UTC),
        )


def test_validation_rejects_resigned_incorrect_simulated_pulse_timing() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    completed = simulate_hopper_plan(
        armed,
        at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
    )
    pulse_started_at = completed["state"]["events"][3]["at"]
    completed["state"]["events"][4]["at"] = pulse_started_at
    completed["state"]["events"][5]["at"] = pulse_started_at
    _resign(completed)

    with pytest.raises(HopperPlanValidationError, match="state history"):
        validate_hopper_plan(completed)
