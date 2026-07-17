"""Pure, simulation-only spunding threshold advice."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from forge_companion.fermentation import ParseResult, analyze_readings, parse_readings


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
