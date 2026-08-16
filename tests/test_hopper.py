import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from forge_companion.hopper import (
    HopperPlanValidationError,
    HopperPulseRejectedError,
    HopperPulseVerificationError,
    HopperStatus,
    arm_hopper_plan,
    create_hopper_plan,
    fire_hopper_plan,
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


def test_create_cloud_pulse_plan_stores_server_and_device_id_without_key() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=5000,
        now=CREATED_AT,
        plan_id=PLAN_ID,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )

    summary = validate_hopper_plan(payload)
    assert summary.status is HopperStatus.DRAFT
    assert payload["action"] == {
        "kind": "cloud-pulse",
        "pulse_duration_ms": 5000,
        "server": "shelly-82-eu.shelly.cloud",
        "device_id": "5432046e5f58",
    }
    assert "auth_key" not in payload["action"]
    assert "auth_key" not in json.dumps(payload)


def test_fire_cloud_pulse_plan_transitions_through_complete_cycle() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        plan_id=PLAN_ID,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    from forge_companion.shelly_cloud import ShellyCloudPulseResult, ShellyCloudSwitchStatus

    class FakeActuator:
        def __init__(self, **kwargs: object) -> None:
            self.seen = kwargs
            self.pulse_called = False

        def __enter__(self) -> "FakeActuator":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def pulse(self, channel: int = 0, toggle_after_seconds: float = 1.0) -> object:
            self.pulse_called = True
            return ShellyCloudPulseResult(
                accepted=True,
                readback=ShellyCloudSwitchStatus(
                    device_id="5432046e5f58",
                    channel=0,
                    online=True,
                    output=False,
                    source="cloud",
                ),
            )

    actuator = FakeActuator()
    completed = fire_hopper_plan(
        armed,
        at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
        actuator=actuator,
        persist=lambda changed: None,
    )

    summary = validate_hopper_plan(completed)
    assert summary.status is HopperStatus.LOCKED
    assert actuator.pulse_called is True
    assert [event["status"] for event in completed["state"]["events"]] == [
        "DRAFT",
        "ARMED",
        "FIRE_REQUESTED",
        "PULSE_ACTIVE",
        "VERIFIED_OFF",
        "LOCKED",
    ]
    verified_at = datetime.fromisoformat(completed["state"]["events"][4]["at"])
    assert verified_at > datetime(2026, 7, 23, 18, 1, 1, 500_000, tzinfo=UTC)


def test_create_cloud_plan_rejects_pulse_longer_than_actuator_limit() -> None:
    with pytest.raises(ValueError, match="at most 5000"):
        create_hopper_plan(
            trigger_at=TRIGGER_AT,
            pulse_duration_ms=5_001,
            now=CREATED_AT,
            server="shelly-82-eu.shelly.cloud",
            device_id="5432046E5F58",
        )


def test_fire_persists_fire_requested_before_calling_actuator() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    persisted: list[dict[str, object]] = []

    class FailingActuator:
        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            assert persisted
            assert validate_hopper_plan(persisted[-1]).status is HopperStatus.FIRE_REQUESTED
            raise RuntimeError("ambiguous transport failure")

    with pytest.raises(RuntimeError, match="ambiguous"):
        fire_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
            actuator=FailingActuator(),
            persist=lambda changed: persisted.append(deepcopy(changed)),
        )

    assert validate_hopper_plan(persisted[-1]).status is HopperStatus.FIRE_REQUESTED


def test_fire_classifies_provider_rejection_without_response_body() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=5000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    from forge_companion.shelly_cloud import ShellyCloudPulseResult

    class RejectedActuator:
        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            return ShellyCloudPulseResult(
                accepted=False,
                readback=None,
                response_status=422,
            )

    persisted: list[dict[str, object]] = []
    with pytest.raises(HopperPulseRejectedError) as exc_info:
        fire_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
            actuator=RejectedActuator(),
            persist=lambda changed: persisted.append(deepcopy(changed)),
        )

    assert exc_info.value.response_status == 422
    assert "response" not in str(exc_info.value).lower()
    assert validate_hopper_plan(persisted[-1]).status is HopperStatus.FIRE_REQUESTED

    class ForbiddenActuator:
        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            raise AssertionError("a consumed plan must not invoke the actuator")

    with pytest.raises(ValueError, match="only an armed hopper plan can be fired"):
        fire_hopper_plan(
            persisted[-1],
            at=datetime(2026, 7, 23, 18, 2, tzinfo=UTC),
            actuator=ForbiddenActuator(),
            persist=lambda changed: (_ for _ in ()).throw(
                AssertionError("a consumed plan must not be persisted again")
            ),
        )


def test_fire_classifies_accepted_pulse_without_verified_off_readback() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=5000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    from forge_companion.shelly_cloud import ShellyCloudPulseResult, ShellyCloudSwitchStatus

    class UnverifiedActuator:
        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            return ShellyCloudPulseResult(
                accepted=True,
                readback=ShellyCloudSwitchStatus(
                    device_id="5432046e5f58",
                    channel=0,
                    online=True,
                    output=True,
                    source="cloud",
                ),
                response_status=200,
            )

    with pytest.raises(HopperPulseVerificationError):
        fire_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
            actuator=UnverifiedActuator(),
            persist=lambda changed: None,
        )


def test_fire_rejects_cloud_profile_that_does_not_match_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    from forge_companion import shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-196-eu.shelly.cloud",
        device_id="aaaaaaaaaaaa",
        auth_key="synthetic-cloud-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(
            profile=profile,
            source="keyring",
        ),
    )

    with pytest.raises(ValueError, match="does not match"):
        fire_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
            persist=lambda changed: None,
        )


def test_fire_closes_owned_actuator_when_initial_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-cloud-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )
    exited = False

    class TrackingActuator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "TrackingActuator":
            return self

        def __exit__(self, *args: object) -> None:
            nonlocal exited
            exited = True

        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            raise AssertionError("pulse must not be sent")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", TrackingActuator)

    def fail_persist(changed: dict[str, object]) -> None:
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        fire_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
            persist=fail_persist,  # type: ignore[arg-type]
        )

    assert exited is True


def test_validation_rejects_resigned_cloud_pulse_above_actuator_limit() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        plan_id=PLAN_ID,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    payload["action"]["pulse_duration_ms"] = 5_001
    _resign(payload)

    with pytest.raises(HopperPlanValidationError, match="pulse duration"):
        validate_hopper_plan(payload)


def test_validation_rejects_resigned_simulation_with_cloud_fields() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1500,
        now=CREATED_AT,
        plan_id=PLAN_ID,
    )
    payload["action"]["server"] = "shelly-82-eu.shelly.cloud"
    payload["action"]["device_id"] = "5432046e5f58"
    _resign(payload)

    with pytest.raises(HopperPlanValidationError, match="schema validation"):
        validate_hopper_plan(payload)


def test_validation_rejects_resigned_noncanonical_cloud_target() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        plan_id=PLAN_ID,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    payload["action"]["server"] = "SHELLY-82-EU.SHELLY.CLOUD"
    payload["action"]["device_id"] = "5432046E5F58"
    _resign(payload)

    with pytest.raises(HopperPlanValidationError, match="schema validation"):
        validate_hopper_plan(payload)


def test_fire_final_persistence_failure_leaves_durable_requested_snapshot() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    from forge_companion.shelly_cloud import ShellyCloudPulseResult, ShellyCloudSwitchStatus

    pulse_count = 0

    class FakeActuator:
        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            nonlocal pulse_count
            pulse_count += 1
            return ShellyCloudPulseResult(
                accepted=True,
                readback=ShellyCloudSwitchStatus(
                    device_id="5432046e5f58",
                    channel=0,
                    online=True,
                    output=False,
                    source="cloud",
                ),
            )

    persisted: list[dict[str, object]] = []

    def persist(changed: dict[str, object]) -> None:
        if persisted:
            raise OSError("final persistence failed")
        persisted.append(deepcopy(changed))

    with pytest.raises(OSError, match="final persistence failed"):
        fire_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
            persist=persist,  # type: ignore[arg-type]
            actuator=FakeActuator(),
        )

    assert pulse_count == 1
    assert len(persisted) == 1
    assert validate_hopper_plan(persisted[0]).status is HopperStatus.FIRE_REQUESTED


def test_simulate_rejects_cloud_one_shot_plan() -> None:
    payload = create_hopper_plan(
        trigger_at=TRIGGER_AT,
        pulse_duration_ms=1000,
        now=CREATED_AT,
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))

    with pytest.raises(ValueError, match="simulation plan"):
        simulate_hopper_plan(
            armed,
            at=datetime(2026, 7, 23, 18, 1, tzinfo=UTC),
        )
