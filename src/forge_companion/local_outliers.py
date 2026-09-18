"""Pure, offline local-neighbor outlier evidence for one telemetry stream.

A finding is not validation, calibration, a sensor-fault conclusion, or permission
for completion, packaging, safety, or actuation. No finding likewise does not
validate or calibrate an observation. This module performs no I/O and chooses no
source, clock, persistence, progress, diagnosis, or control policy.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from math import isfinite
from uuid import UUID

from forge_companion.telemetry import DeviceKind, TelemetryReading

_NANOSECONDS_PER_SECOND = 1_000_000_000


class LocalOutlierValidationError(ValueError):
    """The explicit offline selection or policy is invalid; no result was produced."""


@dataclass(frozen=True)
class LocalOutlierObservation:
    """One immutable selected observation, retaining its source identity and exact time."""

    source: str
    device_kind: DeviceKind
    device_id: str
    reading_id: str
    observed_at_ns: int
    value: Decimal


@dataclass(frozen=True)
class LocalOutlierAssessment:
    """A candidate with usable strict local neighbors; absence is not validation."""

    metric: str
    observation: LocalOutlierObservation
    left_observation: LocalOutlierObservation
    right_observation: LocalOutlierObservation
    expected_value: Decimal
    residual: Decimal
    threshold: Decimal


@dataclass(frozen=True)
class LocalOutlierFinding(LocalOutlierAssessment):
    """A local residual strictly greater than its explicit threshold, not sensor proof."""


@dataclass(frozen=True)
class LocalOutlierResult:
    """Immutable offline evidence; unassessed candidates are intentionally absent.

    ``assessed_observations`` contains only metric observations with strict usable
    bracketing neighbors. An empty ``findings`` tuple is not validation or calibration.
    Findings do not prove a sensor fault or permit completion, packaging, safety, or
    actuation.
    """

    readings: tuple[TelemetryReading, ...]
    window_start_ns: int
    window_end_ns: int
    max_neighbor_gap_ns: int
    assessed_observations: tuple[LocalOutlierAssessment, ...]
    findings: tuple[LocalOutlierFinding, ...]


def evaluate_local_outliers(
    readings: tuple[TelemetryReading, ...],
    *,
    gravity_unit: str | None,
    temperature_unit: str | None,
    gravity_threshold: str | None,
    temperature_threshold: str | None,
    window_start_ns: int,
    window_end_ns: int,
    max_neighbor_gap_ns: int,
) -> LocalOutlierResult:
    """Evaluate explicit SG and/or C local-neighbor residual evidence atomically.

    A declared metric requires an exact matching unit on every reading and a finite,
    nonnegative Decimal-string threshold. Each usable candidate is interpolated from
    its closest strict metric neighbors when both candidate-neighbor gaps are at most
    ``max_neighbor_gap_ns``. Values at exactly the threshold are not findings.
    """
    _window(window_start_ns, window_end_ns)
    _gap(max_neighbor_gap_ns)
    metrics = _metrics(gravity_unit, temperature_unit, gravity_threshold, temperature_threshold)
    if not isinstance(readings, tuple):
        raise LocalOutlierValidationError("readings must be an immutable tuple")

    stream: tuple[str, DeviceKind, str] | None = None
    ids: set[str] = set()
    instants: set[int] = set()
    validated: list[tuple[int, TelemetryReading]] = []
    for reading in readings:
        instant, identity = _reading(reading, metrics)
        if stream is None:
            stream = identity
        elif identity != stream:
            raise LocalOutlierValidationError("readings must be one source device stream")
        if reading.reading_id in ids or instant in instants:
            raise LocalOutlierValidationError("readings contain duplicate exact time or identity")
        if not window_start_ns <= instant <= window_end_ns:
            raise LocalOutlierValidationError("reading lies outside the selected window")
        ids.add(reading.reading_id)
        instants.add(instant)
        validated.append((instant, reading))

    ordered = tuple(sorted(validated, key=lambda item: item[0]))
    assessments: list[LocalOutlierAssessment] = []
    findings: list[LocalOutlierFinding] = []
    for metric, threshold in metrics:
        measured: list[tuple[int, TelemetryReading, Decimal]] = []
        for instant, reading in ordered:
            value = _value(reading, metric)
            if value is not None:
                measured.append((instant, reading, value))
        for index in range(1, len(measured) - 1):
            left = measured[index - 1]
            candidate = measured[index]
            right = measured[index + 1]
            if (
                candidate[0] - left[0] > max_neighbor_gap_ns
                or right[0] - candidate[0] > max_neighbor_gap_ns
            ):
                continue
            assessment = _assessment(metric, threshold, left, candidate, right)
            assessments.append(assessment)
            if assessment.residual > threshold:
                findings.append(
                    LocalOutlierFinding(
                        metric=assessment.metric,
                        observation=assessment.observation,
                        left_observation=assessment.left_observation,
                        right_observation=assessment.right_observation,
                        expected_value=assessment.expected_value,
                        residual=assessment.residual,
                        threshold=assessment.threshold,
                    )
                )
    return LocalOutlierResult(
        readings=readings,
        window_start_ns=window_start_ns,
        window_end_ns=window_end_ns,
        max_neighbor_gap_ns=max_neighbor_gap_ns,
        assessed_observations=tuple(assessments),
        findings=tuple(findings),
    )


def _metrics(
    gravity_unit: object,
    temperature_unit: object,
    gravity_threshold: object,
    temperature_threshold: object,
) -> tuple[tuple[str, Decimal], ...]:
    declared: list[tuple[str, Decimal]] = []
    for unit, threshold, expected_unit, metric in (
        (gravity_unit, gravity_threshold, "sg", "gravity-sg"),
        (temperature_unit, temperature_threshold, "c", "temperature-c"),
    ):
        if unit is None:
            if threshold is not None:
                raise LocalOutlierValidationError("an undeclared metric cannot have a threshold")
            continue
        if unit != expected_unit:
            raise LocalOutlierValidationError(f"{metric} unit must be explicit {expected_unit}")
        declared.append((metric, _threshold(threshold, metric)))
    if not declared:
        raise LocalOutlierValidationError("at least one metric unit must be explicit")
    return tuple(declared)


def _threshold(value: object, metric: str) -> Decimal:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LocalOutlierValidationError(
            f"{metric} threshold must be a finite nonnegative Decimal string"
        )
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise LocalOutlierValidationError(
            f"{metric} threshold must be a finite nonnegative Decimal string"
        ) from None
    if not parsed.is_finite() or parsed < 0:
        raise LocalOutlierValidationError(
            f"{metric} threshold must be a finite nonnegative Decimal string"
        )
    return parsed


def _window(start: object, end: object) -> None:
    if type(start) is not int or type(end) is not int or start > end:
        raise LocalOutlierValidationError("selected window must be an inclusive exact ns window")


def _gap(value: object) -> None:
    if type(value) is not int or value < 0:
        raise LocalOutlierValidationError("max neighbor gap must be a nonnegative exact ns integer")


def _reading(
    reading: object, metrics: tuple[tuple[str, Decimal], ...]
) -> tuple[int, tuple[str, DeviceKind, str]]:
    if not isinstance(reading, TelemetryReading):
        raise LocalOutlierValidationError("input contains an invalid reading")
    source = _opaque_identifier(reading.source)
    device_id = _opaque_identifier(reading.device_id)
    _opaque_identifier(reading.reading_id)
    if source in {"rapt", "brewforge"}:
        _canonical_uuid(device_id)
    if source == "rapt":
        _canonical_uuid(reading.reading_id)
    if not isinstance(reading.device_kind, DeviceKind):
        raise LocalOutlierValidationError("input contains an invalid stream identity")
    if (
        not isinstance(reading.observed_at, datetime)
        or reading.observed_at.utcoffset() is None
        or type(reading.observed_at_submicrosecond_ns) is not int
        or reading.observed_at_submicrosecond_ns not in range(0, 1000, 100)
    ):
        raise LocalOutlierValidationError("input contains an invalid exact timestamp")
    for name in (
        "temperature_c",
        "gravity_raw",
        "gravity_velocity_raw",
        "target_temperature_c",
        "battery_percent",
        "rssi",
        "control_temperature_c",
    ):
        _finite_or_none(getattr(reading, name))
    if reading.gravity_unit not in (None, "sg") or reading.temperature_unit not in (None, "c"):
        raise LocalOutlierValidationError("input contains an invalid unit identity")
    for metric, _ in metrics:
        unit = reading.gravity_unit if metric == "gravity-sg" else reading.temperature_unit
        expected = "sg" if metric == "gravity-sg" else "c"
        if unit != expected:
            raise LocalOutlierValidationError(f"reading {metric} unit must be explicit {expected}")
    try:
        instant = _exact_ns(reading)
    except (OverflowError, ValueError):
        raise LocalOutlierValidationError("input contains an invalid exact timestamp") from None
    return instant, (source, reading.device_kind, device_id)


def _opaque_identifier(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise LocalOutlierValidationError("input contains an invalid stream identity")
    return value


def _canonical_uuid(value: str) -> None:
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise LocalOutlierValidationError("input contains an invalid stream identity") from None
    if canonical != value:
        raise LocalOutlierValidationError("input contains an invalid stream identity")


def _finite_or_none(value: object) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LocalOutlierValidationError("input contains an invalid numeric value")
    try:
        if not isfinite(float(value)):
            raise ValueError
    except (OverflowError, ValueError):
        raise LocalOutlierValidationError("input contains an invalid numeric value") from None


def _exact_ns(reading: TelemetryReading) -> int:
    utc = reading.observed_at.astimezone(UTC)
    elapsed = utc - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        (elapsed.days * 86_400 + elapsed.seconds) * _NANOSECONDS_PER_SECOND
        + elapsed.microseconds * 1_000
        + reading.observed_at_submicrosecond_ns
    )


def _value(reading: TelemetryReading, metric: str) -> Decimal | None:
    value = reading.gravity_raw if metric == "gravity-sg" else reading.temperature_c
    return None if value is None else Decimal(str(value))


def _observation(
    instant: int, reading: TelemetryReading, value: Decimal
) -> LocalOutlierObservation:
    return LocalOutlierObservation(
        source=reading.source,
        device_kind=reading.device_kind,
        device_id=reading.device_id,
        reading_id=reading.reading_id,
        observed_at_ns=instant,
        value=value,
    )


def _assessment(
    metric: str,
    threshold: Decimal,
    left: tuple[int, TelemetryReading, Decimal],
    candidate: tuple[int, TelemetryReading, Decimal],
    right: tuple[int, TelemetryReading, Decimal],
) -> LocalOutlierAssessment:
    with localcontext() as context:
        context.prec = 80
        expected = left[2] + (right[2] - left[2]) * Decimal(candidate[0] - left[0]) / Decimal(
            right[0] - left[0]
        )
        residual = abs(candidate[2] - expected)
    if not expected.is_finite() or not residual.is_finite():
        raise LocalOutlierValidationError("derived local outlier evidence is not finite")
    return LocalOutlierAssessment(
        metric=metric,
        observation=_observation(candidate[0], candidate[1], candidate[2]),
        left_observation=_observation(left[0], left[1], left[2]),
        right_observation=_observation(right[0], right[1], right[2]),
        expected_value=expected,
        residual=residual,
        threshold=threshold,
    )
