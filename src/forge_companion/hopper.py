"""Digest-protected one-shot plans for rehearsal or guarded remote hop drops."""

import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from forge_companion.file_io import (
    AtomicDestinationExistsError,
    atomic_create_text,
    atomic_write_text,
)

_FORMAT = "forge-companion-hopper-plan-v1"
_CANONICALIZATION = "json-sort-keys-compact-utf8-without-digest"
_MAX_CLOUD_PULSE_DURATION_MS = 1_000


class HopperStatus(StrEnum):
    """Lifecycle states for a rehearsal or guarded Cloud one-shot plan."""

    DRAFT = "DRAFT"
    ARMED = "ARMED"
    FIRE_REQUESTED = "FIRE_REQUESTED"
    PULSE_ACTIVE = "PULSE_ACTIVE"
    VERIFIED_OFF = "VERIFIED_OFF"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class HopperPlanSummary:
    """Validated plan metadata without a hardware command path."""

    plan_id: str
    status: HopperStatus
    created_at: datetime
    trigger_at: datetime
    pulse_duration_ms: int


class HopperPlanValidationError(ValueError):
    """Report an invalid local hopper plan."""


class HopperPlanBusyError(RuntimeError):
    """Report that another process owns the local hopper-plan lock."""


class HopperPlanExistsError(RuntimeError):
    """Report that a new hopper plan would overwrite an existing destination."""


@contextmanager
def hopper_plan_lock(plan_path: Path) -> Iterator[None]:
    """Exclusively guard one local plan transition without waiting or stale-lock recovery."""
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = plan_path.with_name(f".{plan_path.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise HopperPlanBusyError("hopper plan is busy or locked") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _plan_digest(payload: dict[str, Any]) -> str:
    unsigned = deepcopy(payload)
    unsigned["integrity"].pop("digest", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _utc_timestamp(value: datetime, *, field: str) -> datetime:
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be timezone-aware")
        return value.astimezone(UTC)
    except (OSError, OverflowError):
        raise ValueError(f"{field} is outside the supported timestamp range") from None


def create_hopper_plan(
    *,
    trigger_at: datetime,
    pulse_duration_ms: int,
    now: datetime | None = None,
    plan_id: UUID | None = None,
    brew_id: UUID | None = None,
    server: str | None = None,
    device_id: str | None = None,
) -> dict[str, Any]:
    """Create an offline simulation or Cloud-bound one-shot draft."""
    created_at = _utc_timestamp(now or datetime.now(UTC), field="creation time")
    trigger = _utc_timestamp(trigger_at, field="trigger time")
    if trigger <= created_at:
        raise ValueError("trigger time must be after creation time")
    if (
        isinstance(pulse_duration_ms, bool)
        or not isinstance(pulse_duration_ms, int)
        or pulse_duration_ms <= 0
    ):
        raise ValueError("pulse duration must be a positive integer of milliseconds")
    if pulse_duration_ms > 60_000:
        raise ValueError("simulated pulse duration must be at most 60000 milliseconds")
    if server is not None and pulse_duration_ms > _MAX_CLOUD_PULSE_DURATION_MS:
        raise ValueError("cloud pulse duration must be at most 1000 milliseconds")
    canonical_plan_id = str(plan_id or uuid4())
    if server is not None and device_id is not None:
        from forge_companion.shelly_cloud import normalize_cloud_device_id, normalize_cloud_server

        action: dict[str, Any] = {
            "kind": "cloud-pulse",
            "pulse_duration_ms": pulse_duration_ms,
            "server": normalize_cloud_server(server),
            "device_id": normalize_cloud_device_id(device_id),
        }
    elif server is None and device_id is None:
        action = {
            "kind": "simulated-pulse",
            "pulse_duration_ms": pulse_duration_ms,
        }
    else:
        raise ValueError("both server and device_id are required for a cloud-pulse plan")
    payload: dict[str, Any] = {
        "format": _FORMAT,
        "plan_id": canonical_plan_id,
        "created_at": created_at.isoformat(),
        "trigger_at": trigger.isoformat(),
        "brew_id": str(brew_id) if brew_id is not None else None,
        "action": action,
        "state": {
            "status": HopperStatus.DRAFT.value,
            "events": [{"status": HopperStatus.DRAFT.value, "at": created_at.isoformat()}],
        },
        "integrity": {
            "algorithm": "sha256",
            "canonicalization": _CANONICALIZATION,
        },
    }
    payload["integrity"]["digest"] = _plan_digest(payload)
    return payload


def _parse_utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise HopperPlanValidationError("hopper plan schema validation failed")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise HopperPlanValidationError("hopper plan schema validation failed") from None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
        or value != parsed.isoformat()
    ):
        raise HopperPlanValidationError("hopper plan schema validation failed")
    return parsed


def validate_hopper_plan(payload: dict[str, Any]) -> HopperPlanSummary:
    """Strictly validate an offline simulation or Cloud one-shot plan."""
    try:
        if payload.get("format") != _FORMAT:
            raise HopperPlanValidationError("unsupported hopper plan format")
        if set(payload) != {
            "format",
            "plan_id",
            "created_at",
            "trigger_at",
            "brew_id",
            "action",
            "state",
            "integrity",
        }:
            raise HopperPlanValidationError("hopper plan schema validation failed")

        raw_plan_id = payload["plan_id"]
        if not isinstance(raw_plan_id, str) or str(UUID(raw_plan_id)) != raw_plan_id:
            raise HopperPlanValidationError("hopper plan schema validation failed")
        brew_id = payload["brew_id"]
        if brew_id is not None and (not isinstance(brew_id, str) or str(UUID(brew_id)) != brew_id):
            raise HopperPlanValidationError("hopper plan schema validation failed")

        created_at = _parse_utc_timestamp(payload["created_at"])
        trigger_at = _parse_utc_timestamp(payload["trigger_at"])
        if trigger_at <= created_at:
            raise HopperPlanValidationError("hopper plan schema validation failed")

        action = payload["action"]
        state = payload["state"]
        integrity = payload["integrity"]
        if not isinstance(action, dict):
            raise HopperPlanValidationError("hopper plan schema validation failed")
        action_kind = action.get("kind")
        if action_kind == "simulated-pulse":
            if set(action) != {"kind", "pulse_duration_ms"}:
                raise HopperPlanValidationError("hopper plan schema validation failed")
        elif action_kind == "cloud-pulse":
            if set(action) != {"kind", "pulse_duration_ms", "server", "device_id"}:
                raise HopperPlanValidationError("hopper plan schema validation failed")
            from forge_companion.shelly_cloud import (
                normalize_cloud_device_id,
                normalize_cloud_server,
            )

            server = action.get("server")
            device_id = action.get("device_id")
            if (
                not isinstance(server, str)
                or not isinstance(device_id, str)
                or normalize_cloud_server(server) != server
                or normalize_cloud_device_id(device_id) != device_id
            ):
                raise HopperPlanValidationError("hopper plan schema validation failed")
        else:
            raise HopperPlanValidationError("hopper plan schema validation failed")
        if not isinstance(state, dict) or set(state) != {"status", "events"}:
            raise HopperPlanValidationError("hopper plan schema validation failed")
        if not isinstance(integrity, dict) or set(integrity) != {
            "algorithm",
            "canonicalization",
            "digest",
        }:
            raise HopperPlanValidationError("hopper plan schema validation failed")

        digest = integrity["digest"]
        if (
            integrity.get("algorithm") != "sha256"
            or integrity.get("canonicalization") != _CANONICALIZATION
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise HopperPlanValidationError("hopper plan integrity metadata is invalid")
        if not compare_digest(digest, _plan_digest(payload)):
            raise HopperPlanValidationError("hopper plan integrity check failed")

        pulse_duration_ms = action["pulse_duration_ms"]
        maximum_pulse_duration_ms = (
            _MAX_CLOUD_PULSE_DURATION_MS if action_kind == "cloud-pulse" else 60_000
        )
        if (
            isinstance(pulse_duration_ms, bool)
            or not isinstance(pulse_duration_ms, int)
            or not 1 <= pulse_duration_ms <= maximum_pulse_duration_ms
        ):
            raise HopperPlanValidationError("invalid pulse duration")

        events = state["events"]
        if not isinstance(events, list) or not 1 <= len(events) <= len(HopperStatus):
            raise HopperPlanValidationError("hopper plan state history is invalid")
        expected_statuses = list(HopperStatus)[: len(events)]
        event_times: list[datetime] = []
        for event, expected_status in zip(events, expected_statuses, strict=True):
            if (
                not isinstance(event, dict)
                or set(event) != {"status", "at"}
                or event.get("status") != expected_status.value
            ):
                raise HopperPlanValidationError("hopper plan state history is invalid")
            event_times.append(_parse_utc_timestamp(event["at"]))
        if event_times[0] != created_at or any(
            current < previous
            for previous, current in zip(event_times, event_times[1:], strict=False)
        ):
            raise HopperPlanValidationError("hopper plan state history is invalid")
        status = HopperStatus(state["status"])
        if status is not expected_statuses[-1]:
            raise HopperPlanValidationError("hopper plan state history is invalid")
        if len(event_times) >= 2 and event_times[1] >= trigger_at:
            raise HopperPlanValidationError("hopper plan state history is invalid")
        if len(event_times) >= 3 and any(at < trigger_at for at in event_times[2:]):
            raise HopperPlanValidationError("hopper plan state history is invalid")
        if len(event_times) >= 4 and event_times[3] != event_times[2]:
            raise HopperPlanValidationError("hopper plan state history is invalid")
        if len(event_times) >= 5:
            pulse_elapsed = event_times[4] - event_times[3]
            minimum_pulse = timedelta(milliseconds=pulse_duration_ms)
            if (action_kind == "simulated-pulse" and pulse_elapsed != minimum_pulse) or (
                action_kind == "cloud-pulse" and pulse_elapsed < minimum_pulse
            ):
                raise HopperPlanValidationError("hopper plan state history is invalid")
        if len(event_times) >= 6 and event_times[5] != event_times[4]:
            raise HopperPlanValidationError("hopper plan state history is invalid")
    except HopperPlanValidationError:
        raise
    except (KeyError, TypeError, ValueError):
        raise HopperPlanValidationError("hopper plan schema validation failed") from None
    return HopperPlanSummary(
        plan_id=raw_plan_id,
        status=status,
        created_at=created_at,
        trigger_at=trigger_at,
        pulse_duration_ms=pulse_duration_ms,
    )


def _append_status(payload: dict[str, Any], status: HopperStatus, at: datetime) -> dict[str, Any]:
    changed = deepcopy(payload)
    changed["state"]["status"] = status.value
    changed["state"]["events"].append({"status": status.value, "at": at.isoformat()})
    changed["integrity"]["digest"] = _plan_digest(changed)
    return changed


def arm_hopper_plan(payload: dict[str, Any], *, at: datetime) -> dict[str, Any]:
    """Explicitly arm one valid draft without contacting any device."""
    summary = validate_hopper_plan(payload)
    transition_time = _utc_timestamp(at, field="arming time")
    if summary.status is not HopperStatus.DRAFT:
        raise ValueError("only a draft hopper plan can be armed")
    if transition_time < summary.created_at:
        raise ValueError("hopper plan cannot be armed before plan creation")
    if transition_time >= summary.trigger_at:
        raise ValueError("hopper plan must be armed before its trigger time")
    return _append_status(payload, HopperStatus.ARMED, transition_time)


def simulate_hopper_plan(payload: dict[str, Any], *, at: datetime) -> dict[str, Any]:
    """Run the complete lifecycle in memory without waiting or contacting hardware."""
    summary = validate_hopper_plan(payload)
    if payload["action"]["kind"] != "simulated-pulse":
        raise ValueError("only a simulated-pulse plan can run as a simulation plan")
    simulation_time = _utc_timestamp(at, field="simulation time")
    if summary.status is not HopperStatus.ARMED:
        raise ValueError("only an armed hopper plan can be simulated")
    if simulation_time < summary.trigger_at:
        raise ValueError("hopper simulation cannot run before its trigger time")
    try:
        pulse_end = simulation_time + timedelta(milliseconds=summary.pulse_duration_ms)
    except OverflowError:
        raise ValueError("simulated pulse end exceeds timestamp range") from None
    changed = _append_status(payload, HopperStatus.FIRE_REQUESTED, simulation_time)
    changed = _append_status(changed, HopperStatus.PULSE_ACTIVE, simulation_time)
    changed = _append_status(changed, HopperStatus.VERIFIED_OFF, pulse_end)
    changed = _append_status(changed, HopperStatus.LOCKED, pulse_end)
    return changed


def fire_hopper_plan(
    payload: dict[str, Any],
    *,
    at: datetime,
    persist: Callable[[dict[str, Any]], None],
    actuator: Any = None,
) -> dict[str, Any]:
    """Execute one cloud pulse after durably recording its one-shot request."""
    summary = validate_hopper_plan(payload)
    fire_time = _utc_timestamp(at, field="fire time")
    if summary.status is not HopperStatus.ARMED:
        raise ValueError("only an armed hopper plan can be fired")
    if fire_time < summary.trigger_at:
        raise ValueError("hopper fire cannot run before its trigger time")
    action = payload["action"]
    if action.get("kind") != "cloud-pulse":
        raise ValueError("only a cloud-pulse plan can be fired")

    close_actuator = False
    if actuator is None:
        from forge_companion.shelly_cloud_credentials import resolve_profile

        resolved = resolve_profile()
        if resolved.profile is None:
            raise ValueError("hopper fire failed: no Shelly Cloud profile is stored")
        profile = resolved.profile
        if profile.server != action["server"] or profile.device_id != action["device_id"]:
            raise ValueError("stored Shelly Cloud profile does not match hopper plan")
        from forge_companion.shelly_cloud import ShellyCloudActuator

        actuator_instance = ShellyCloudActuator(
            server=profile.server,
            device_id=profile.device_id,
            auth_key=profile.auth_key,
        )
        actuator_instance.__enter__()
        close_actuator = True
    else:
        actuator_instance = actuator

    try:
        requested = _append_status(payload, HopperStatus.FIRE_REQUESTED, fire_time)
        persist(requested)
        result = actuator_instance.pulse(
            channel=0,
            toggle_after_seconds=summary.pulse_duration_ms / 1000.0,
        )
        if (
            not result.accepted
            or result.readback is None
            or not result.readback.online
            or result.readback.output is not False
        ):
            raise RuntimeError("Shelly Cloud pulse outcome is not verified OFF")
        verified_time = datetime.now(UTC)
        minimum_verified_time = fire_time + timedelta(milliseconds=summary.pulse_duration_ms)
        if verified_time < minimum_verified_time:
            raise RuntimeError("Shelly Cloud OFF read-back arrived before the pulse timer elapsed")
        changed = _append_status(requested, HopperStatus.PULSE_ACTIVE, fire_time)
        changed = _append_status(changed, HopperStatus.VERIFIED_OFF, verified_time)
        changed = _append_status(changed, HopperStatus.LOCKED, verified_time)
        persist(changed)
        return changed
    finally:
        if close_actuator:
            actuator_instance.__exit__(None, None, None)


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON numeric literal: {value}")


def load_hopper_plan(source: Path) -> dict[str, Any]:
    """Load strict JSON and validate one local hopper plan without network access."""
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise HopperPlanValidationError("hopper plan is invalid or unreadable") from None
    if not isinstance(payload, dict):
        raise HopperPlanValidationError("hopper plan schema validation failed")
    validate_hopper_plan(payload)
    return payload


def write_hopper_plan(payload: dict[str, Any], destination: Path) -> None:
    """Validate and atomically write one local hopper plan."""
    validate_hopper_plan(payload)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    atomic_write_text(serialized, destination, newline="\n")


def write_new_hopper_plan(payload: dict[str, Any], destination: Path) -> None:
    """Validate and atomically create one plan without replacing any destination."""
    validate_hopper_plan(payload)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    try:
        atomic_create_text(serialized, destination, newline="\n")
    except AtomicDestinationExistsError:
        raise HopperPlanExistsError("hopper plan destination already exists") from None
