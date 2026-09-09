"""Validated local, non-secret vessel associations (experimental version 1)."""

import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from forge_companion.file_io import atomic_write_text
from forge_companion.preferences import preferences_path

SCHEMA_VERSION = "forge-companion-vessels-v1"
MAX_FILE_BYTES = 64 * 1024
_BINDING_FIELDS = {
    "vessel_id",
    "hydrometer",
    "temperature_controller",
    "gravity_interpretation",
}
_VESSEL_ID = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,63})\Z")
_GRAVITY_INTERPRETATIONS = {"unknown", "sg-times-1000"}


class VesselValidationError(ValueError):
    """Report invalid vessel input or an invalid persisted association file."""


class VesselBusyError(RuntimeError):
    """Report that another process is changing the vessel associations."""


def vessels_path() -> Path:
    """Return the platform-native path for non-secret vessel associations."""
    return preferences_path().with_name("vessels.json")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VesselValidationError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise VesselValidationError(f"Invalid JSON numeric literal: {value}.")


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise VesselValidationError("Device identifiers must be strings.")
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        raise VesselValidationError("Device identifier is not a UUID.") from None
    if str(parsed) != value:
        raise VesselValidationError("Device identifier must be a canonical lowercase UUID.")
    return value


def _validate_binding(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _BINDING_FIELDS:
        raise VesselValidationError("Vessel binding has an unsupported shape.")
    vessel_id = value["vessel_id"]
    gravity = value["gravity_interpretation"]
    if not isinstance(vessel_id, str) or _VESSEL_ID.fullmatch(vessel_id) is None:
        raise VesselValidationError("Vessel ID is invalid.")
    if not isinstance(gravity, str) or gravity not in _GRAVITY_INTERPRETATIONS:
        raise VesselValidationError("Gravity interpretation is invalid.")
    hydrometer = _canonical_uuid(value["hydrometer"])
    controller = _canonical_uuid(value["temperature_controller"])
    if hydrometer == controller:
        raise VesselValidationError("A vessel requires two distinct devices.")
    return {
        "vessel_id": vessel_id,
        "hydrometer": hydrometer,
        "temperature_controller": controller,
        "gravity_interpretation": gravity,
    }


def _validate_payload(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "vessels"}:
        raise VesselValidationError("Vessel file has an unsupported shape.")
    if payload["schema_version"] != SCHEMA_VERSION or not isinstance(payload["vessels"], list):
        raise VesselValidationError("Vessel file has an unsupported schema.")
    bindings = [_validate_binding(item) for item in payload["vessels"]]
    vessel_ids: set[str] = set()
    device_ids: set[str] = set()
    for binding in bindings:
        devices = {binding["hydrometer"], binding["temperature_controller"]}
        if binding["vessel_id"] in vessel_ids or devices & device_ids:
            raise VesselValidationError("Vessel file contains duplicate associations.")
        vessel_ids.add(binding["vessel_id"])
        device_ids.update(devices)
    return bindings


def load_vessels() -> list[dict[str, str]]:
    """Load a strictly validated association file, or an empty absent file."""
    source = vessels_path()
    try:
        with source.open("rb") as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return []
    except OSError:
        raise
    if len(content) > MAX_FILE_BYTES:
        raise VesselValidationError("Vessel file is too large.")
    try:
        text = content.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise VesselValidationError("Vessel file is invalid or unreadable.") from None
    return _validate_payload(payload)


@contextmanager
def _vessels_lock() -> Iterator[None]:
    destination = vessels_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_name(f".{destination.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise VesselBusyError("Vessel associations are busy or locked.") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def bind_vessel(binding: dict[str, str], *, replace: bool = False) -> None:
    """Atomically bind two unique devices to one vessel without network access."""
    validated = _validate_binding(binding)
    if not isinstance(replace, bool):
        raise VesselValidationError("Replace must be a boolean.")
    with _vessels_lock():
        bindings = load_vessels()
        existing = any(item["vessel_id"] == validated["vessel_id"] for item in bindings)
        if existing and not replace:
            raise VesselValidationError("Vessel already bound; use --replace explicitly.")
        remaining = [item for item in bindings if item["vessel_id"] != validated["vessel_id"]]
        devices = {validated["hydrometer"], validated["temperature_controller"]}
        if any(
            devices & {item["hydrometer"], item["temperature_controller"]} for item in remaining
        ):
            raise VesselValidationError("Device already bound to another vessel.")
        content = json.dumps(
            {"schema_version": SCHEMA_VERSION, "vessels": [*remaining, validated]},
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        if len(content.encode("utf-8")) + 1 > MAX_FILE_BYTES:
            raise VesselValidationError("Vessel file would be too large.")
        atomic_write_text(content + "\n", vessels_path(), newline="\n")
