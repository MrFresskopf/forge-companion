"""Shared validation of neutral telemetry stream identity and exact timestamp."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from forge_companion.telemetry import DeviceKind, TelemetryReading

_NANOSECONDS_PER_SECOND = 1_000_000_000


class TelemetryIdentityValidationError(ValueError):
    """A neutral telemetry reading has invalid identity or exact-time metadata."""


@dataclass(frozen=True)
class TelemetryIdentity:
    """Validated stream and observation identity, preserving the exact instant."""

    source: str
    device_kind: DeviceKind
    device_id: str
    reading_id: str
    observed_at_ns: int

    @property
    def stream(self) -> tuple[str, DeviceKind, str]:
        """Return the source-neutral stream identity."""
        return self.source, self.device_kind, self.device_id


def validate_telemetry_identity(reading: object) -> TelemetryIdentity:
    """Validate one neutral reading's identity and exact time without metric policy."""
    if not isinstance(reading, TelemetryReading):
        raise TelemetryIdentityValidationError("input contains an invalid reading")
    source = _opaque_identifier(reading.source)
    device_id = _opaque_identifier(reading.device_id)
    reading_id = _opaque_identifier(reading.reading_id)
    if source in {"rapt", "brewforge"}:
        _canonical_uuid(device_id)
    if source == "rapt":
        _canonical_uuid(reading_id)
    if not isinstance(reading.device_kind, DeviceKind):
        raise TelemetryIdentityValidationError("input contains an invalid stream identity")
    if (
        not isinstance(reading.observed_at, datetime)
        or reading.observed_at.utcoffset() is None
        or type(reading.observed_at_submicrosecond_ns) is not int
        or reading.observed_at_submicrosecond_ns not in range(0, 1000, 100)
    ):
        raise TelemetryIdentityValidationError("input contains an invalid exact timestamp")
    try:
        observed_at_ns = _exact_ns(reading.observed_at, reading.observed_at_submicrosecond_ns)
    except (OverflowError, ValueError):
        raise TelemetryIdentityValidationError(
            "input contains an invalid exact timestamp"
        ) from None
    return TelemetryIdentity(source, reading.device_kind, device_id, reading_id, observed_at_ns)


def _opaque_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise TelemetryIdentityValidationError("input contains an invalid stream identity")
    return value


def _canonical_uuid(value: str) -> None:
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise TelemetryIdentityValidationError(
            "input contains an invalid stream identity"
        ) from None
    if canonical != value:
        raise TelemetryIdentityValidationError("input contains an invalid stream identity")


def _exact_ns(observed_at: datetime, remainder_ns: int) -> int:
    utc = observed_at.astimezone(UTC)
    elapsed = utc - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (elapsed.days * 86_400 + elapsed.seconds) * _NANOSECONDS_PER_SECOND
        + elapsed.microseconds * 1_000
        + remainder_ns
    )
