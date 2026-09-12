"""Explicit, pure offline RAPT gravity conversion for the neutral SG advisor."""

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal

from forge_companion.sg_trend import exact_ns
from forge_companion.telemetry import (
    DeviceKind,
    TelemetryReading,
    TelemetryValidationError,
    _canonical_uuid,
    _finite_number,
)


@dataclass(frozen=True)
class SgTelemetryReading(TelemetryReading):
    """SG-valued gravity_raw; all other fields (including velocity) remain raw.

    This in-memory marker is not a persisted format or proof of upstream units.
    """


def normalize_rapt_sg(
    readings: tuple[TelemetryReading, ...], *, gravity_interpretation: str
) -> tuple[SgTelemetryReading, ...]:
    """Convert a complete raw RAPT tuple using an explicit caller assertion.

    Only ``gravity_interpretation="sg-times-1000"`` is accepted; it does not
    verify RAPT units. Invalid input/conversion raises TelemetryValidationError
    without returning partial results. Empty input and missing SG are preserved.
    All metadata fields, ordering, and exact 100 ns instants survive unchanged.
    Only gravity_raw changes.
    No time, freshness, gap, threshold, or confirmation policy is chosen here.

    The output marker subclasses TelemetryReading for advise_telemetry_sg with
    explicit gravity_unit="sg". Do not pass it to raw-RAPT consumers (sg_trend
    already divides raw gravity by 1000). This bridge rejects subclasses,
    including its own output, even when every SG is missing.

    Decimal(str(raw)) is shifted by changing its exponent, independently of
    ambient Decimal precision/traps. Since the existing advisor consumes floats
    via Decimal(str(value)), conversion is rejected unless that round trip
    preserves the exact shifted decimal. Precision already lost by the upstream
    float parser cannot be recovered. Velocity is NOT converted: its unit remains
    unknown. Conflicts/duplicate IDs are preserved for the advisor to reject.
    """
    if gravity_interpretation != "sg-times-1000":
        raise TelemetryValidationError("RAPT SG interpretation must be explicit")
    if not isinstance(readings, tuple):
        raise TelemetryValidationError("RAPT SG input must be a tuple")
    result = []
    device_id = None
    for item in readings:
        if (
            type(item) is not TelemetryReading
            or item.source != "rapt"
            or item.device_kind is not DeviceKind.HYDROMETER
            or (device_id is not None and item.device_id != device_id)
        ):
            raise TelemetryValidationError("RAPT SG input must be one raw hydrometer stream")
        device_id = _canonical_uuid(item.device_id)
        _canonical_uuid(item.reading_id)
        if (
            not isinstance(item.observed_at, datetime)
            or type(item.observed_at_submicrosecond_ns) is not int
            or item.observed_at_submicrosecond_ns not in range(0, 1000, 100)
        ):
            raise TelemetryValidationError("RAPT SG input contains an invalid timestamp")
        try:
            if item.observed_at.utcoffset() is None:
                raise ValueError
            exact_ns(item)
        except (ValueError, OverflowError):
            raise TelemetryValidationError("RAPT SG input contains an invalid timestamp") from None
        for name in (
            "temperature_c",
            "gravity_raw",
            "gravity_velocity_raw",
            "target_temperature_c",
            "battery_percent",
            "rssi",
            "control_temperature_c",
        ):
            value = getattr(item, name)
            if value is not None:
                _finite_number(value, field=name)
        sg = None
        if item.gravity_raw is not None:
            if not 900 <= item.gravity_raw <= 1200:
                raise TelemetryValidationError("RAPT SG gravity must be between 900 and 1200")
            raw = Decimal(str(item.gravity_raw)).as_tuple()
            assert isinstance(raw.exponent, int)  # Finite numbers have integer exponents.
            exact_sg = Decimal((raw.sign, raw.digits, raw.exponent - 3))
            sg = float(exact_sg)
            if Decimal(str(sg)) != exact_sg:
                raise TelemetryValidationError("RAPT SG conversion would lose decimal precision")
        values = {field.name: getattr(item, field.name) for field in fields(TelemetryReading)}
        values["gravity_raw"] = sg
        result.append(SgTelemetryReading(**values))
    return tuple(result)
