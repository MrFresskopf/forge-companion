"""Source-neutral telemetry values for brewery monitoring.

RAPT readings are uniquely identified by reading ID; equal ``createdOn`` timestamps
are retained and ordered deterministically by timestamp and reading ID. RFC3339
fractions are limited to seven digits; sub-microsecond nanoseconds are retained
separately from Python's datetime precision.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol
from uuid import UUID


class DeviceKind(StrEnum):
    """Supported physical telemetry device families."""

    HYDROMETER = "hydrometer"
    TEMPERATURE_CONTROLLER = "temperature-controller"


@dataclass(frozen=True)
class TelemetryReading:
    """A validated measurement independent of its upstream API.

    RAPT's OpenAPI does not specify gravity units. Both raw gravity
    fields preserve upstream numbers without
    conversion; consumers must not assume verified SG or SG/day units.
    Controller measurements may be absent and must never be replaced by zero.
    ``observed_at`` is the UTC microsecond floor; ``observed_at_submicrosecond_ns``
    adds 0..900 ns in 100 ns steps. Compare both for exact time, and use
    ``observed_at_exact`` for canonical text without redundant fractional padding.
    """

    source: str
    device_kind: DeviceKind
    device_id: str
    reading_id: str
    observed_at: datetime
    temperature_c: float | None
    gravity_raw: float | None
    gravity_velocity_raw: float | None
    target_temperature_c: float | None
    battery_percent: float | None
    rssi: float | None
    control_temperature_c: float | None = None
    observed_at_submicrosecond_ns: int = 0

    @property
    def observed_at_exact(self) -> str:
        """Canonical UTC timestamp retaining the exact instant (not lexical padding)."""
        utc = self.observed_at.astimezone(UTC)
        if self.observed_at_submicrosecond_ns:
            base = utc.isoformat(timespec="microseconds")
            return f"{base[:-6]}{self.observed_at_submicrosecond_ns // 100}+00:00"
        return utc.isoformat()


class TelemetryValidationError(ValueError):
    """Report invalid telemetry without reflecting upstream values."""


class RaptTelemetryClient(Protocol):
    """Narrow RAPT methods required by the telemetry adapter."""

    def get_hydrometer_telemetry(
        self, *, device_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]: ...

    def get_temperature_controller_telemetry(
        self, *, device_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class RaptTelemetrySource:
    """Normalize one selected RAPT device behind the common reading contract."""

    client: RaptTelemetryClient
    kind: DeviceKind

    def get_readings(
        self, *, device_id: str, start: datetime, end: datetime
    ) -> tuple[TelemetryReading, ...]:
        if not isinstance(self.kind, DeviceKind):
            raise TelemetryValidationError("RAPT telemetry contains an invalid device kind")
        _canonical_uuid(device_id)
        try:
            if (
                not isinstance(start, datetime)
                or not isinstance(end, datetime)
                or start.utcoffset() is None
                or end.utcoffset() is None
            ):
                raise ValueError
            start, end = start.astimezone(UTC), end.astimezone(UTC)
            if start >= end:
                raise ValueError
        except (OSError, OverflowError, ValueError):
            raise TelemetryValidationError("RAPT telemetry interval is invalid") from None
        if self.kind is DeviceKind.HYDROMETER:
            payload = self.client.get_hydrometer_telemetry(
                device_id=device_id, start=start, end=end
            )
        else:
            payload = self.client.get_temperature_controller_telemetry(
                device_id=device_id, start=start, end=end
            )
        readings = parse_rapt_telemetry(payload, kind=self.kind, device_id=device_id)
        if any(
            not (start, 0)
            <= (reading.observed_at, reading.observed_at_submicrosecond_ns)
            <= (end, 0)
            for reading in readings
        ):
            raise TelemetryValidationError("RAPT telemetry lies outside the requested window")
        return readings


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise TelemetryValidationError("RAPT telemetry contains an invalid identifier")
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise TelemetryValidationError("RAPT telemetry contains an invalid identifier") from None
    if canonical != value:
        raise TelemetryValidationError("RAPT telemetry contains an invalid identifier")
    return canonical


def _finite_number(value: object, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryValidationError(f"RAPT telemetry contains an invalid {field}")
    try:
        parsed = float(value)
    except OverflowError:
        raise TelemetryValidationError(f"RAPT telemetry contains an invalid {field}") from None
    if not isfinite(parsed):
        raise TelemetryValidationError(f"RAPT telemetry contains an invalid {field}")
    return parsed


def _optional_finite_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field=field)


def _utc_timestamp(value: object) -> tuple[datetime, int]:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,7})?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        value,
    ):
        raise TelemetryValidationError("RAPT telemetry contains an invalid timestamp")
    if value.endswith("-00:00"):
        raise TelemetryValidationError("RAPT telemetry contains an invalid timestamp")
    # Extract the seventh digit before datetime parsing; never silently discard it.
    fraction = re.search(r"\.([0-9]+)", value)
    remainder_ns = 0
    if fraction is not None and len(fraction[1]) == 7:
        remainder_ns = int(fraction[1][-1]) * 100
        value = value[: fraction.end() - 1] + value[fraction.end() :]
    try:
        parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC), remainder_ns
    except (OSError, OverflowError, ValueError):
        raise TelemetryValidationError("RAPT telemetry contains an invalid timestamp") from None


def parse_rapt_telemetry(
    payload: object,
    *,
    kind: DeviceKind,
    device_id: str,
) -> tuple[TelemetryReading, ...]:
    """Validate and normalize one complete RAPT telemetry response."""
    if not isinstance(kind, DeviceKind):
        raise TelemetryValidationError("RAPT telemetry contains an invalid device kind")
    canonical_device_id = _canonical_uuid(device_id)
    if not isinstance(payload, list):
        raise TelemetryValidationError("RAPT telemetry response is not a collection")

    readings: list[TelemetryReading] = []
    identifiers: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise TelemetryValidationError("RAPT telemetry contains an invalid record")
        number = _finite_number if kind is DeviceKind.HYDROMETER else _optional_finite_number
        temperature = number(item.get("temperature"), field="temperature")
        rssi = number(item.get("rssi"), field="RSSI")
        if kind is DeviceKind.HYDROMETER:
            gravity = _optional_finite_number(item.get("gravity"), field="gravity")
            gravity_velocity_raw = _optional_finite_number(
                item.get("gravityVelocity"), field="gravity velocity"
            )
            battery = _finite_number(item.get("battery"), field="battery")
            target_temperature = None
            control_temperature = None
        else:
            gravity = None
            gravity_velocity_raw = None
            battery = None
            target_temperature = _optional_finite_number(
                item.get("targetTemperature"), field="target temperature"
            )
            control_temperature = _optional_finite_number(
                item.get("controlDeviceTemperature"), field="control device temperature"
            )
        reading_id = _canonical_uuid(item.get("id"))
        observed_at, remainder_ns = _utc_timestamp(item.get("createdOn"))
        if reading_id in identifiers:
            raise TelemetryValidationError(
                "RAPT telemetry contains duplicate or conflicting records"
            )
        identifiers.add(reading_id)
        readings.append(
            TelemetryReading(
                source="rapt",
                device_kind=kind,
                device_id=canonical_device_id,
                reading_id=reading_id,
                observed_at=observed_at,
                observed_at_submicrosecond_ns=remainder_ns,
                temperature_c=temperature,
                gravity_raw=gravity,
                gravity_velocity_raw=gravity_velocity_raw,
                target_temperature_c=target_temperature,
                battery_percent=battery,
                rssi=rssi,
                control_temperature_c=control_temperature,
            )
        )
    return tuple(
        sorted(
            readings,
            key=lambda reading: (
                reading.observed_at,
                reading.observed_at_submicrosecond_ns,
                reading.reading_id,
            ),
        )
    )
