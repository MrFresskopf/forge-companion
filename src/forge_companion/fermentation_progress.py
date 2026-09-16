"""Offline, observational fermentation-progress evidence from neutral SG telemetry.

This module makes no claim about completion, packaging safety, freshness, calibration,
temperature, expected final gravity, or permission to actuate. Its rate is an average
across the selected observations, not a forecast.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import UUID

from forge_companion.sg_trend import NANOSECONDS_PER_SECOND, exact_ns
from forge_companion.telemetry import DeviceKind, TelemetryReading

_SG_MIN = Decimal("0.9")
_SG_MAX = Decimal("1.2")
_WATER_SG = Decimal("1")
_DAY_NS = Decimal(86_400 * NANOSECONDS_PER_SECOND)
_CANONICAL_SG = re.compile(r"1\.[0-9]+")


class FermentationProgressValidationError(ValueError):
    """The supplied offline observation selection cannot provide evidence."""


@dataclass(frozen=True)
class ProgressObservation:
    """One selected endpoint, retaining source, reading identity, and exact time."""

    source: str
    device_kind: DeviceKind
    device_id: str
    reading_id: str
    observed_at_ns: int
    sg: Decimal


@dataclass(frozen=True)
class FermentationProgressResult:
    """Immutable observational evidence; ``average_sg_slope_per_day`` is not a forecast."""

    original_gravity: Decimal
    selected_window_start_ns: int
    selected_window_end_ns: int
    observation_interval_start_ns: int
    observation_interval_end_ns: int
    first_observation: ProgressObservation
    latest_observation: ProgressObservation
    observations: int
    latest_sg: Decimal
    apparent_attenuation_percent: Decimal
    average_sg_slope_per_day: Decimal
    rate_label: str = "average SG slope over selected observation interval (SG/day; not forecast)"


def evaluate_fermentation_progress(
    readings: tuple[TelemetryReading, ...],
    *,
    gravity_unit: str,
    original_gravity: str,
    window_start_ns: int,
    window_end_ns: int,
) -> FermentationProgressResult:
    """Return bounded evidence for one explicit, inclusive exact-time SG window.

    ``gravity_unit`` and every reading's unit must be exact ``"sg"``. Raw RAPT
    readings therefore require explicit normalization before this evaluator. Original
    gravity is a canonical decimal SG string; it is neither inferred nor persisted.
    All input is validated before a result is created, so invalid input yields no
    partial evidence.
    """
    original = _original_gravity(original_gravity)
    _window(window_start_ns, window_end_ns)
    if gravity_unit != "sg":
        raise FermentationProgressValidationError("gravity unit must be explicit sg")
    if not isinstance(readings, tuple) or not readings:
        raise FermentationProgressValidationError("readings must be a non-empty tuple")

    stream: tuple[str, DeviceKind, str] | None = None
    identifiers: set[str] = set()
    instants: set[int] = set()
    selected: list[tuple[int, TelemetryReading, Decimal]] = []
    for reading in readings:
        instant, sg, identity = _reading(reading)
        if stream is None:
            stream = identity
        elif identity != stream:
            raise FermentationProgressValidationError("readings must be one source device stream")
        if reading.reading_id in identifiers or instant in instants:
            raise FermentationProgressValidationError(
                "readings contain duplicate exact time or identity"
            )
        if not window_start_ns <= instant <= window_end_ns:
            raise FermentationProgressValidationError("reading lies outside the selected window")
        identifiers.add(reading.reading_id)
        instants.add(instant)
        selected.append((instant, reading, sg))

    if len(selected) < 2:
        raise FermentationProgressValidationError("at least two distinct observations are required")
    selected.sort(key=lambda item: item[0])
    first_time, first_reading, first_sg = selected[0]
    latest_time, latest_reading, latest_sg = selected[-1]
    duration_ns = latest_time - first_time
    if duration_ns <= 0:
        raise FermentationProgressValidationError("observation interval must be positive")
    if not _WATER_SG <= latest_sg <= original:
        raise FermentationProgressValidationError(
            "latest SG is outside the apparent attenuation domain"
        )

    attenuation, rate = _derived(original, first_sg, latest_sg, duration_ns)
    return FermentationProgressResult(
        original_gravity=original,
        selected_window_start_ns=window_start_ns,
        selected_window_end_ns=window_end_ns,
        observation_interval_start_ns=first_time,
        observation_interval_end_ns=latest_time,
        first_observation=_observation(first_reading, first_time, first_sg),
        latest_observation=_observation(latest_reading, latest_time, latest_sg),
        observations=len(selected),
        latest_sg=latest_sg,
        apparent_attenuation_percent=attenuation,
        average_sg_slope_per_day=rate,
    )


def _window(start: object, end: object) -> None:
    if type(start) is not int or type(end) is not int or start >= end:
        raise FermentationProgressValidationError(
            "selected window must be a positive exact interval"
        )


def _original_gravity(value: object) -> Decimal:
    if not isinstance(value, str) or _CANONICAL_SG.fullmatch(value) is None:
        raise FermentationProgressValidationError(
            "original gravity must be a canonical decimal SG string"
        )
    parsed = Decimal(value)
    if not parsed.is_finite() or not _WATER_SG < parsed <= _SG_MAX:
        raise FermentationProgressValidationError(
            "original gravity is outside the apparent attenuation domain"
        )
    return parsed


def _reading(reading: object) -> tuple[int, Decimal, tuple[str, DeviceKind, str]]:
    if not isinstance(reading, TelemetryReading):
        raise FermentationProgressValidationError("input contains an invalid reading")
    source = _opaque_identifier(reading.source, field="source")
    device_id = _opaque_identifier(reading.device_id, field="device")
    reading_id = _opaque_identifier(reading.reading_id, field="reading")
    if source in {"rapt", "brewforge"}:
        device_id = _canonical_uuid(device_id, field="device")
    if source == "rapt":
        reading_id = _canonical_uuid(reading_id, field="reading")
    if reading.device_kind is not DeviceKind.HYDROMETER:
        raise FermentationProgressValidationError("input contains an invalid stream identity")
    if reading.gravity_unit != "sg":
        raise FermentationProgressValidationError("reading gravity unit must be explicit sg")
    if (
        not isinstance(reading.observed_at, datetime)
        or type(reading.observed_at_submicrosecond_ns) is not int
        or reading.observed_at_submicrosecond_ns not in range(0, 1000, 100)
        or reading.observed_at.utcoffset() is None
    ):
        raise FermentationProgressValidationError("input contains an invalid exact timestamp")
    if (
        reading.gravity_raw is None
        or isinstance(reading.gravity_raw, bool)
        or not isinstance(reading.gravity_raw, (int, float))
    ):
        raise FermentationProgressValidationError("input contains an absent or invalid SG")
    sg = Decimal(str(reading.gravity_raw))
    if not sg.is_finite() or not _SG_MIN <= sg <= _SG_MAX:
        raise FermentationProgressValidationError("input contains an out-of-domain SG")
    try:
        instant = exact_ns(reading)
    except (OverflowError, ValueError):
        raise FermentationProgressValidationError(
            "input contains an invalid exact timestamp"
        ) from None
    return instant, sg, (source, reading.device_kind, device_id)


def _opaque_identifier(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise FermentationProgressValidationError("input contains an invalid stream identity")
    return value


def _canonical_uuid(value: str, *, field: str) -> str:
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise FermentationProgressValidationError(
            "input contains an invalid stream identity"
        ) from None
    if canonical != value:
        raise FermentationProgressValidationError("input contains an invalid stream identity")
    return canonical


def _derived(
    original: Decimal, first: Decimal, latest: Decimal, duration_ns: int
) -> tuple[Decimal, Decimal]:
    with localcontext() as context:
        context.prec = 50
        context.traps[Inexact] = False
        context.traps[Rounded] = False
        attenuation = (original - latest) * Decimal(100) / (original - _WATER_SG)
        rate = (latest - first) * _DAY_NS / Decimal(duration_ns)
    if (
        not attenuation.is_finite()
        or not rate.is_finite()
        or not Decimal(0) <= attenuation <= Decimal(100)
    ):
        raise FermentationProgressValidationError(
            "derived fermentation progress is not finite or in domain"
        )
    return attenuation, rate


def _observation(reading: TelemetryReading, instant: int, sg: Decimal) -> ProgressObservation:
    return ProgressObservation(
        source=reading.source,
        device_kind=reading.device_kind,
        device_id=reading.device_id,
        reading_id=reading.reading_id,
        observed_at_ns=instant,
        sg=sg,
    )
