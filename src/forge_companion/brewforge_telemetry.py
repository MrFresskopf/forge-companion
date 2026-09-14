"""Pure adapter from stored BrewForge readings to neutral telemetry."""

from datetime import datetime
from uuid import UUID

from forge_companion.telemetry import (
    DeviceKind,
    TelemetryReading,
    TelemetryValidationError,
    _optional_finite_number,
    _utc_timestamp,
)


def _canonical_brew_id(value: object) -> str:
    if not isinstance(value, str):
        raise TelemetryValidationError("BrewForge telemetry contains an invalid brew identifier")
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise TelemetryValidationError(
            "BrewForge telemetry contains an invalid brew identifier"
        ) from None
    if canonical != value:
        raise TelemetryValidationError("BrewForge telemetry contains an invalid brew identifier")
    return canonical


def _canonical_reading_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise TelemetryValidationError("BrewForge telemetry contains an invalid reading identifier")
    return value


def _measurement(value: object, *, field: str) -> float | None:
    try:
        number = _optional_finite_number(value, field=field)
    except TelemetryValidationError:
        raise TelemetryValidationError(f"BrewForge telemetry contains an invalid {field}") from None
    if number is not None and (
        (field == "gravity" and not 0.9 <= number <= 1.2)
        or (field == "temperature" and number < -273.15)
    ):
        raise TelemetryValidationError(f"BrewForge telemetry contains an invalid {field}")
    return number


def adapt_brewforge_readings(
    payload: object,
    *,
    brew_id: str,
    gravity_unit: str,
    temperature_unit: str,
) -> tuple[TelemetryReading, ...]:
    """Adapt a complete decoded ``{data: [...]}`` response without I/O or policy.

    The caller must assert exact ``sg`` and ``c`` units. Those are the only units
    supported by the observed BrewForge contract; values are never magnitude-
    detected or converted. The canonical brew UUID identifies the stored stream,
    not a physical device. Opaque source reading IDs are preserved exactly.

    Validation is atomic. The adapter chooses no clock, freshness, calibration,
    completion, safety, advisor, or actuator policy. Observed pressure, pH, and
    comment fields are validated but not represented by ``TelemetryReading``;
    additional fields remain forward-compatible with the existing parser policy.
    """
    if gravity_unit != "sg" or temperature_unit != "c":
        raise TelemetryValidationError("BrewForge telemetry units must be explicit: sg and c")
    canonical_brew_id = _canonical_brew_id(brew_id)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise TelemetryValidationError("BrewForge telemetry response is not a collection")
    records = payload["data"]
    result: list[TelemetryReading] = []
    identifiers: set[str] = set()
    instants: set[tuple[datetime, int]] = set()
    for item in records:
        if not isinstance(item, dict):
            raise TelemetryValidationError("BrewForge telemetry contains an invalid record")
        reading_id = _canonical_reading_id(item.get("id"))
        try:
            observed_at, remainder_ns = _utc_timestamp(item.get("timestamp"))
        except TelemetryValidationError:
            raise TelemetryValidationError(
                "BrewForge telemetry contains an invalid timestamp"
            ) from None
        instant = (observed_at, remainder_ns)
        if reading_id in identifiers or instant in instants:
            raise TelemetryValidationError(
                "BrewForge telemetry contains duplicate or conflicting identity or time"
            )
        identifiers.add(reading_id)
        instants.add(instant)
        _measurement(item.get("pressure"), field="pressure")
        _measurement(item.get("ph"), field="ph")
        comment = item.get("comment")
        if comment is not None and not isinstance(comment, str):
            raise TelemetryValidationError("BrewForge telemetry contains an invalid comment")
        result.append(
            TelemetryReading(
                source="brewforge",
                device_kind=DeviceKind.HYDROMETER,
                device_id=canonical_brew_id,
                reading_id=reading_id,
                observed_at=observed_at,
                observed_at_submicrosecond_ns=remainder_ns,
                temperature_c=_measurement(item.get("temperature"), field="temperature"),
                temperature_unit=temperature_unit,
                gravity_raw=_measurement(item.get("gravity"), field="gravity"),
                gravity_unit=gravity_unit,
                gravity_velocity_raw=None,
                target_temperature_c=None,
                battery_percent=None,
                rssi=None,
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda reading: (
                reading.observed_at,
                reading.observed_at_submicrosecond_ns,
                reading.reading_id,
            ),
        )
    )