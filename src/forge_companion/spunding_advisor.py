"""Pure, simulation-only spunding threshold advice."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from forge_companion.fermentation import ParseResult, analyze_readings, parse_readings
from forge_companion.sg_trend import exact_ns
from forge_companion.telemetry import DeviceKind, TelemetryReading


def advise_telemetry_sg(
    readings: tuple[TelemetryReading, ...],
    *,
    gravity_unit: str | None,
    now_ns: int,
    trigger_sg: Decimal,
    max_age_ns: int,
    max_gap_ns: int,
    confirmations: int,
) -> "AdvisorStatus":
    """Evaluate neutral readings; never permission to actuate or a safety claim.

    All policy is required: ``now_ns`` is an integer UTC Unix epoch offset;
    positive age/gap limits are integer nanoseconds (inclusive), and confirmations
    is 2..5. Invalid policy raises ValueError. ``gravity_unit="sg"`` explicitly
    declares gravity_raw to be SG; missing/other interpretations fail closed.
    This does not verify upstream units or convert raw RAPT gravity.

    Malformed or mixed-stream tuples, missing/implausible SG, conflicting SG at
    an exact instant, and reading IDs reused at different instants give NO_DECISION.
    Equal SG observations at one instant count once, regardless of reading ID.
    Other measurement fields are irrelevant to this SG-only evaluation.
    All input is validated, then the latest distinct confirmations are selected.
    Only the latest age and gaps within that selection are gated, as in the legacy
    advisor. WAIT means at least one selected SG exceeds the Decimal threshold;
    CONDITION_MET means all are at or below it, not fermentation completion.
    """
    return explain_telemetry_sg(
        readings,
        gravity_unit=gravity_unit,
        now_ns=now_ns,
        trigger_sg=trigger_sg,
        max_age_ns=max_age_ns,
        max_gap_ns=max_gap_ns,
        confirmations=confirmations,
    ).status


class TelemetrySgReason(StrEnum):
    """First diagnostic blocker, or the selected SG comparison outcome."""

    UNDECLARED_UNIT = "UNDECLARED_UNIT"
    INVALID_COLLECTION = "INVALID_COLLECTION"
    NO_READINGS = "NO_READINGS"
    INVALID_READING = "INVALID_READING"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    MISSING_SG = "MISSING_SG"
    INVALID_SG = "INVALID_SG"
    MIXED_STREAM = "MIXED_STREAM"
    CONFLICTING_SG = "CONFLICTING_SG"
    REUSED_ID = "REUSED_ID"
    INSUFFICIENT_CONFIRMATIONS = "INSUFFICIENT_CONFIRMATIONS"
    FUTURE = "FUTURE"
    STALE = "STALE"
    GAP_EXCEEDED = "GAP_EXCEEDED"
    ABOVE_TRIGGER = "ABOVE_TRIGGER"
    AT_OR_BELOW_TRIGGER = "AT_OR_BELOW_TRIGGER"


@dataclass(frozen=True)
class TelemetrySgEvidence:
    """One exact-time candidate observation, not quality approval."""

    observed_at_ns: int
    sg: Decimal


@dataclass(frozen=True)
class TelemetrySgResult:
    """Immutable simulation diagnostics; never permission to actuate."""

    status: "AdvisorStatus"
    reason: TelemetrySgReason
    evidence: tuple[TelemetrySgEvidence, ...]
    distinct_observations: int | None
    latest_age_ns: int | None
    largest_confirmation_gap_ns: int | None


def explain_telemetry_sg(
    readings: tuple[TelemetryReading, ...],
    *,
    gravity_unit: str | None,
    now_ns: int,
    trigger_sg: Decimal,
    max_age_ns: int,
    max_gap_ns: int,
    confirmations: int,
) -> TelemetrySgResult:
    """Explain SG candidates with the same required policy as ``advise_telemetry_sg``.

    Invalid policy raises ValueError before any observation checks. Unit, collection,
    or full-series integrity failures return empty evidence and None metrics. Otherwise
    evidence contains the latest distinct N instants (or all available if fewer), in
    ascending order, even when insufficient, future, stale, or gap-blocked. These are
    candidates, not quality-approved confirmations. ``distinct_observations`` counts
    the full valid series; latest age is signed; the largest gap is populated only
    for a complete N-candidate selection. All times are exact integer nanoseconds and
    SG is Decimal(str(gravity_raw)), not recovered upstream precision.

    The reason is the first blocker in validation/traversal order, not an exhaustive
    or permutation-invariant error list. Quality precedence is insufficient, future,
    stale, gap, then threshold. No credentials, I/O, defaults, or actuator permission.
    """
    if (
        not isinstance(trigger_sg, Decimal)
        or not trigger_sg.is_finite()
        or not Decimal("0.9") <= trigger_sg <= Decimal("1.2")
    ):
        raise ValueError("trigger SG must be a finite Decimal between 0.9000 and 1.2000")
    if any(type(value) is not int for value in (now_ns, max_age_ns, max_gap_ns, confirmations)):
        raise ValueError("time, limits and confirmations must be integers, not booleans")
    if max_age_ns <= 0 or max_gap_ns <= 0 or not 2 <= confirmations <= 5:
        raise ValueError("age and gap must be positive; confirmations must be between 2 and 5")

    def unusable(reason: TelemetrySgReason) -> TelemetrySgResult:
        return TelemetrySgResult(AdvisorStatus.NO_DECISION, reason, (), None, None, None)

    if gravity_unit != "sg":
        return unusable(TelemetrySgReason.UNDECLARED_UNIT)
    if not isinstance(readings, tuple):
        return unusable(TelemetrySgReason.INVALID_COLLECTION)
    if not readings:
        return unusable(TelemetrySgReason.NO_READINGS)
    values: dict[int, Decimal] = {}
    identifiers: dict[str, int] = {}
    stream: tuple[str, DeviceKind, str] | None = None
    for item in readings:
        if not isinstance(item, TelemetryReading):
            return unusable(TelemetrySgReason.INVALID_READING)
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (item.source, item.device_id, item.reading_id)
        ) or not isinstance(item.device_kind, DeviceKind):
            return unusable(TelemetrySgReason.INVALID_READING)
        if (
            not isinstance(item.observed_at, datetime)
            or type(item.observed_at_submicrosecond_ns) is not int
            or item.observed_at_submicrosecond_ns not in range(0, 1000, 100)
        ):
            return unusable(TelemetrySgReason.INVALID_TIMESTAMP)
        if item.gravity_raw is None:
            return unusable(TelemetrySgReason.MISSING_SG)
        if isinstance(item.gravity_raw, bool) or not isinstance(item.gravity_raw, (int, float)):
            return unusable(TelemetrySgReason.INVALID_SG)
        try:
            if item.observed_at.utcoffset() is None:
                return unusable(TelemetrySgReason.INVALID_TIMESTAMP)
            instant = exact_ns(item)
        except (ValueError, OverflowError):
            return unusable(TelemetrySgReason.INVALID_TIMESTAMP)
        try:
            sg = Decimal(str(item.gravity_raw))
        except ValueError:
            return unusable(TelemetrySgReason.INVALID_SG)
        if not sg.is_finite() or not Decimal("0.9") <= sg <= Decimal("1.2"):
            return unusable(TelemetrySgReason.INVALID_SG)
        identity = (item.source, item.device_kind, item.device_id)
        if stream is not None and identity != stream:
            return unusable(TelemetrySgReason.MIXED_STREAM)
        stream = identity
        if instant in values and values[instant] != sg:
            return unusable(TelemetrySgReason.CONFLICTING_SG)
        if item.reading_id in identifiers and identifiers[item.reading_id] != instant:
            return unusable(TelemetrySgReason.REUSED_ID)
        values[instant] = sg
        identifiers[item.reading_id] = instant
    ordered = sorted(values)
    selected = ordered[-confirmations:]
    latest_age = now_ns - ordered[-1]
    largest_gap = (
        max(right - left for left, right in zip(selected, selected[1:], strict=False))
        if len(selected) == confirmations
        else None
    )
    status = AdvisorStatus.NO_DECISION
    if len(ordered) < confirmations:
        reason = TelemetrySgReason.INSUFFICIENT_CONFIRMATIONS
    elif latest_age < 0:
        reason = TelemetrySgReason.FUTURE
    elif latest_age > max_age_ns:
        reason = TelemetrySgReason.STALE
    elif largest_gap is not None and largest_gap > max_gap_ns:
        reason = TelemetrySgReason.GAP_EXCEEDED
    elif any(values[instant] > trigger_sg for instant in selected):
        reason = TelemetrySgReason.ABOVE_TRIGGER
        status = AdvisorStatus.WAIT
    else:
        reason = TelemetrySgReason.AT_OR_BELOW_TRIGGER
        status = AdvisorStatus.CONDITION_MET
    return TelemetrySgResult(
        status,
        reason,
        tuple(TelemetrySgEvidence(instant, values[instant]) for instant in selected),
        len(values),
        latest_age,
        largest_gap,
    )


class AdvisorStatus(StrEnum):
    """Possible simulation outcomes; none represents an actuator command."""

    NO_DECISION = "NO_DECISION"
    WAIT = "WAIT"
    CONDITION_MET = "CONDITION_MET"


@dataclass(frozen=True)
class AdvisorConfig:
    """Explicit limits for one advisor evaluation."""

    trigger_sg: float
    max_age: timedelta
    max_gap: timedelta
    confirmations: int = 2

    def __post_init__(self) -> None:
        if (
            isinstance(self.trigger_sg, bool)
            or not isinstance(self.trigger_sg, (int, float))
            or not math.isfinite(float(self.trigger_sg))
            or not 0.9 <= self.trigger_sg <= 1.2
        ):
            raise ValueError("trigger SG must be finite and between 0.9000 and 1.2000")
        if self.max_age <= timedelta(0):
            raise ValueError("max age must be positive")
        if self.max_gap <= timedelta(0):
            raise ValueError("max gap must be positive")
        if (
            isinstance(self.confirmations, bool)
            or not isinstance(self.confirmations, int)
            or not 2 <= self.confirmations <= 5
        ):
            raise ValueError("confirmations must be an integer between 2 and 5")


@dataclass(frozen=True)
class AdvisorEvidence:
    """One validated reading used by an advisor result."""

    reading_id: str
    timestamp: datetime
    gravity: float


@dataclass(frozen=True)
class AdvisorResult:
    """Evidence and rationale for one simulation-only evaluation."""

    status: AdvisorStatus
    reason: str
    trigger_sg: float
    evidence: tuple[AdvisorEvidence, ...]
    latest_age: timedelta | None
    largest_confirmation_gap: timedelta | None
    gravity_slope_per_day: float | None
    trend_note: str | None


def _no_decision(
    config: AdvisorConfig,
    reason: str,
    *,
    evidence: tuple[AdvisorEvidence, ...] = (),
    latest_age: timedelta | None = None,
    largest_confirmation_gap: timedelta | None = None,
) -> AdvisorResult:
    return AdvisorResult(
        status=AdvisorStatus.NO_DECISION,
        reason=reason,
        trigger_sg=config.trigger_sg,
        evidence=evidence,
        latest_age=latest_age,
        largest_confirmation_gap=largest_confirmation_gap,
        gravity_slope_per_day=None,
        trend_note=None,
    )


def advise_spunding_payload(
    payload: object,
    *,
    config: AdvisorConfig,
    as_of: datetime,
) -> AdvisorResult:
    """Parse an API payload and fail closed when its envelope is malformed."""
    if as_of.tzinfo is None:
        raise ValueError("advisor time must include a timezone")
    try:
        parsed = parse_readings(payload)
    except (TypeError, ValueError):
        return _no_decision(config, "readings response is malformed")
    return advise_spunding(parsed, config=config, as_of=as_of)


def advise_spunding(
    parsed: ParseResult,
    *,
    config: AdvisorConfig,
    as_of: datetime,
) -> AdvisorResult:
    """Evaluate validated telemetry without sending any device command."""
    if as_of.tzinfo is None:
        raise ValueError("advisor time must include a timezone")
    if not parsed.readings:
        return _no_decision(config, "no valid fermentation readings")
    if parsed.rejected:
        return _no_decision(config, "one or more readings were rejected")
    if any(not 0.9 <= reading.gravity <= 1.2 for reading in parsed.readings):
        return _no_decision(
            config,
            "one or more gravity readings are outside plausible SG bounds",
        )
    if parsed.conflicting_timestamps:
        return _no_decision(config, "readings contain timestamp conflicts")

    as_of_utc = as_of.astimezone(UTC)
    latest_age = as_of_utc - parsed.readings[-1].timestamp
    if latest_age < timedelta(0):
        return _no_decision(config, "latest reading is after advisor time")
    if latest_age > config.max_age:
        return _no_decision(config, "latest reading is stale", latest_age=latest_age)
    if len(parsed.readings) < config.confirmations:
        return _no_decision(
            config,
            "insufficient confirmation readings",
            latest_age=latest_age,
        )

    confirmation_readings = parsed.readings[-config.confirmations :]
    evidence = tuple(
        AdvisorEvidence(
            reading_id=reading.id,
            timestamp=reading.timestamp,
            gravity=reading.gravity,
        )
        for reading in confirmation_readings
    )
    confirmation_gaps = tuple(
        current.timestamp - previous.timestamp
        for previous, current in zip(
            confirmation_readings,
            confirmation_readings[1:],
            strict=False,
        )
    )
    largest_gap = max(confirmation_gaps, default=timedelta(0))
    if largest_gap > config.max_gap:
        return _no_decision(
            config,
            "confirmation gap exceeds configured maximum",
            evidence=evidence,
            latest_age=latest_age,
            largest_confirmation_gap=largest_gap,
        )

    condition_met = all(reading.gravity <= config.trigger_sg for reading in confirmation_readings)
    status = AdvisorStatus.CONDITION_MET if condition_met else AdvisorStatus.WAIT
    reason = (
        "all confirmation readings are at or below trigger SG"
        if condition_met
        else "not all confirmation readings are at or below trigger SG"
    )
    metrics = analyze_readings(parsed, report_time=as_of_utc)
    return AdvisorResult(
        status=status,
        reason=reason,
        trigger_sg=config.trigger_sg,
        evidence=evidence,
        latest_age=latest_age,
        largest_confirmation_gap=largest_gap,
        gravity_slope_per_day=metrics.gravity_slope_per_day,
        trend_note=metrics.trend_note,
    )
