"""Pure adapter for an explicitly selected native RAPT Pill stream in Brewfather."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isfinite

from forge_companion.telemetry import DeviceKind, TelemetryReading, TelemetryValidationError


def _opaque_identifier(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise TelemetryValidationError(f"Brewfather telemetry contains an invalid {field}")
    return value


def _optional_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryValidationError(f"Brewfather telemetry contains an invalid {field}")
    try:
        parsed = float(value)
    except OverflowError:
        raise TelemetryValidationError(
            f"Brewfather telemetry contains an invalid {field}"
        ) from None
    if not isfinite(parsed):
        raise TelemetryValidationError(f"Brewfather telemetry contains an invalid {field}")
    return parsed


def _timestamp_epoch_ms(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TelemetryValidationError("Brewfather telemetry contains an invalid timestamp")
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)
    except OverflowError:
        raise TelemetryValidationError(
            "Brewfather telemetry contains an invalid timestamp"
        ) from None


def adapt_brewfather_readings(
    payload: object,
    *,
    device_id: str,
    gravity_unit: str,
    temperature_unit: str,
    time_unit: str,
) -> tuple[TelemetryReading, ...]:
    """Normalize one explicitly selected native RAPT Pill stream without I/O.

    `sg`, `temp`, and integer `time` become meaningful only through the explicit
    caller declarations `sg`, `c`, and `epoch-ms`. The returned stream identity
    is Brewfather plus its opaque `deviceId`; it is not treated as a RAPT UUID.
    Battery and target temperature remain unlabelled upstream fields and are not
    translated into neutral units. Provider reading IDs are not assumed unique: a
    duplicate ID is preserved with a local occurrence suffix in source-response
    order. Every selected source record validates atomically.
    """
    if gravity_unit != "sg" or temperature_unit != "c" or time_unit != "epoch-ms":
        raise TelemetryValidationError(
            "Brewfather telemetry units must be explicit: sg, c, and epoch-ms"
        )
    selected_device_id = _opaque_identifier(device_id, field="device identifier")
    if not isinstance(payload, list):
        raise TelemetryValidationError("Brewfather telemetry response is not a collection")

    selected: list[dict[str, object]] = []
    raw_identifier_counts: dict[str, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise TelemetryValidationError("Brewfather telemetry contains an invalid record")
        if item.get("deviceId") != selected_device_id:
            continue
        if not isinstance(item.get("deviceType"), str):
            raise TelemetryValidationError("Brewfather telemetry contains an invalid device type")
        raw_identifier = _opaque_identifier(item.get("id"), field="reading identifier")
        selected.append(item)
        raw_identifier_counts[raw_identifier] = raw_identifier_counts.get(raw_identifier, 0) + 1

    result: list[TelemetryReading] = []
    duplicate_occurrences: dict[str, int] = {}
    for item in selected:
        raw_identifier = _opaque_identifier(item.get("id"), field="reading identifier")
        occurrence = duplicate_occurrences.get(raw_identifier, 0)
        duplicate_occurrences[raw_identifier] = occurrence + 1
        reading_id = (
            raw_identifier
            if raw_identifier_counts[raw_identifier] == 1
            else f"{raw_identifier}#{occurrence}"
        )
        gravity = _optional_number(item.get("sg"), field="gravity")
        if gravity is not None and not 0.9 <= gravity <= 1.2:
            raise TelemetryValidationError("Brewfather telemetry contains an invalid gravity")
        temperature = _optional_number(item.get("temp"), field="temperature")
        if temperature is None or temperature < -273.15:
            raise TelemetryValidationError("Brewfather telemetry contains an invalid temperature")
        rssi = _optional_number(item.get("rssi"), field="RSSI")
        result.append(
            TelemetryReading(
                source="brewfather",
                device_kind=DeviceKind.HYDROMETER,
                device_id=selected_device_id,
                reading_id=reading_id,
                observed_at=_timestamp_epoch_ms(item.get("time")),
                temperature_c=temperature,
                temperature_unit=temperature_unit,
                gravity_raw=gravity,
                gravity_unit=gravity_unit,
                gravity_velocity_raw=None,
                target_temperature_c=None,
                battery_percent=None,
                rssi=rssi,
            )
        )
    if not result:
        raise TelemetryValidationError("Brewfather telemetry has no selected device records")
    return tuple(sorted(result, key=lambda reading: (reading.observed_at, reading.reading_id)))
