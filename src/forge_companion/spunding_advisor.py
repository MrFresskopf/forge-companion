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
    if gravity_unit != "sg" or not isinstance(readings, tuple) or not readings:
        return AdvisorStatus.NO_DECISION
    values: dict[int, Decimal] = {}
    identifiers: dict[str, int] = {}
    stream: tuple[str, DeviceKind, str] | None = None
    for item in readings:
        if not isinstance(item, TelemetryReading):
            return AdvisorStatus.NO_DECISION
        if (
            any(
                not isinstance(value, str) or not value.strip()
                for value in (item.source, item.device_id, item.reading_id)
            )
            or not isinstance(item.device_kind, DeviceKind)
            or not isinstance(item.observed_at, datetime)
            or type(item.observed_at_submicrosecond_ns) is not int
            or item.observed_at_submicrosecond_ns not in range(0, 1000, 100)
            or isinstance(item.gravity_raw, bool)
            or not isinstance(item.gravity_raw, (int, float))
        ):
            return AdvisorStatus.NO_DECISION
        try:
            if item.observed_at.utcoffset() is None:
                return AdvisorStatus.NO_DECISION
            instant = exact_ns(item)
        except (ValueError, OverflowError):
            return AdvisorStatus.NO_DECISION
        try:
            sg = Decimal(str(item.gravity_raw))
        except ValueError:
            return AdvisorStatus.NO_DECISION
        if not sg.is_finite() or not Decimal("0.9") <= sg <= Decimal("1.2"):
            return AdvisorStatus.NO_DECISION
        identity = (item.source, item.device_kind, item.device_id)
        if stream is not None and identity != stream:
            return AdvisorStatus.NO_DECISION
        stream = identity
        if instant in values and values[instant] != sg:
            return AdvisorStatus.NO_DECISION
        if item.reading_id in identifiers and identifiers[item.reading_id] != instant:
            return AdvisorStatus.NO_DECISION
        values[instant] = sg
        identifiers[item.reading_id] = instant
    ordered = sorted(values)
    if len(ordered) < confirmations or not 0 <= now_ns - ordered[-1] <= max_age_ns:
        return AdvisorStatus.NO_DECISION
    selected = ordered[-confirmations:]
    if any(right - left > max_gap_ns for left, right in zip(selected, selected[1:], strict=False)):
        return AdvisorStatus.NO_DECISION
    condition_met = all(values[instant] <= trigger_sg for instant in selected)
    return AdvisorStatus.CONDITION_MET if condition_met else AdvisorStatus.WAIT


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
