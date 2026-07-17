"""Validation and analysis of stored BrewForge fermentation readings."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class FermentationReading:
    """One validated BrewForge fermentation reading."""

    id: str
    timestamp: datetime
    gravity: float
    temperature: float | None
    pressure: float | None
    ph: float | None
    comment: str | None


@dataclass(frozen=True)
class ParseResult:
    """Accepted readings and explanations for rejected input records."""

    readings: tuple[FermentationReading, ...]
    rejected: tuple[str, ...]
    conflicting_timestamps: tuple[datetime, ...]


@dataclass(frozen=True)
class FermentationMetrics:
    """Descriptive facts about a validated reading series."""

    accepted_count: int
    rejected_count: int
    started_at: datetime
    ended_at: datetime
    duration: timedelta
    latest_gravity: float
    gravity_delta: float
    latest_temperature: float | None
    minimum_temperature: float | None
    maximum_temperature: float | None
    largest_gap: timedelta
    freshness: timedelta
    gravity_slope_per_day: float | None
    trend_note: str


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def parse_readings(payload: object) -> ParseResult:
    """Parse the observed ``{data: [...]}`` BrewForge readings envelope."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise TypeError("readings response must contain a list-shaped data field")

    readings: list[FermentationReading] = []
    rejected: list[str] = []
    seen: set[FermentationReading] = set()
    readings_by_timestamp: dict[datetime, FermentationReading] = {}
    conflicting_timestamps: set[datetime] = set()
    for index, item in enumerate(payload["data"]):
        try:
            if not isinstance(item, dict):
                raise TypeError("reading is not an object")
            reading_id = item.get("id")
            if not isinstance(reading_id, str) or not reading_id.strip():
                raise TypeError("id is missing")
            timestamp_raw = item.get("timestamp")
            if not isinstance(timestamp_raw, str):
                raise TypeError("timestamp is not a string")
            try:
                timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("timestamp is not valid ISO 8601") from error
            if timestamp.tzinfo is None:
                raise ValueError("timestamp has no timezone")
            gravity = _optional_number(item.get("gravity"), "gravity")
            if gravity is None:
                raise TypeError("gravity is missing")
            comment = item.get("comment")
            if comment is not None and not isinstance(comment, str):
                raise TypeError("comment is not a string")
            reading = FermentationReading(
                id=reading_id,
                timestamp=timestamp.astimezone(UTC),
                gravity=gravity,
                temperature=_optional_number(item.get("temperature"), "temperature"),
                pressure=_optional_number(item.get("pressure"), "pressure"),
                ph=_optional_number(item.get("ph"), "ph"),
                comment=comment,
            )
            if reading in seen:
                rejected.append(f"reading {index}: exact duplicate")
                continue
            previous = readings_by_timestamp.get(reading.timestamp)
            if previous is not None and previous != reading:
                conflicting_timestamps.add(reading.timestamp)
            else:
                readings_by_timestamp[reading.timestamp] = reading
            seen.add(reading)
            readings.append(reading)
        except (OverflowError, TypeError, ValueError) as error:
            rejected.append(f"reading {index}: {error}")
    readings.sort(key=lambda reading: (reading.timestamp, reading.id))
    return ParseResult(
        readings=tuple(readings),
        rejected=tuple(rejected),
        conflicting_timestamps=tuple(sorted(conflicting_timestamps)),
    )


def _gravity_trend(
    readings: tuple[FermentationReading, ...],
) -> tuple[float | None, str]:
    cutoff = readings[-1].timestamp - timedelta(hours=24)
    window = [reading for reading in readings if reading.timestamp >= cutoff]
    if len(window) < 3 or window[-1].timestamp - window[0].timestamp < timedelta(hours=6):
        return None, "insufficient recent data for a 24-hour slope"
    gravity_range = max(reading.gravity for reading in window) - min(
        reading.gravity for reading in window
    )
    if not math.isfinite(gravity_range):
        return None, "gravity range is too large for a finite slope"
    if gravity_range < 0.0008:
        return None, "gravity range is within the configured RAPT noise floor"

    origin = window[0].timestamp
    x_values = [(reading.timestamp - origin).total_seconds() / 86400 for reading in window]
    y_origin = window[0].gravity
    y_values = [reading.gravity - y_origin for reading in window]
    try:
        x_mean = math.fsum(x_values) / len(x_values)
        y_mean = math.fsum(y_values) / len(y_values)
        denominator = math.fsum((x - x_mean) ** 2 for x in x_values)
        numerator = math.fsum(
            (x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values, strict=True)
        )
    except OverflowError:
        return None, "gravity values are too large for a finite slope"
    if denominator == 0:
        return None, "insufficient timestamp spread for a slope"
    slope = numerator / denominator
    if not math.isfinite(slope):
        return None, "gravity values are too large for a finite slope"
    return round(slope, 6), "least-squares slope over the latest 24 hours"


def analyze_readings(parsed: ParseResult, report_time: datetime) -> FermentationMetrics:
    """Compute non-predictive metrics for a validated reading series."""
    if report_time.tzinfo is None:
        raise ValueError("report time must include a timezone")
    if not parsed.readings:
        raise ValueError("no valid fermentation readings")

    readings = parsed.readings
    report_time_utc = report_time.astimezone(UTC)
    if readings[-1].timestamp > report_time_utc:
        raise ValueError("latest reading is after report time")
    temperatures = [reading.temperature for reading in readings if reading.temperature is not None]
    gaps = [
        current.timestamp - previous.timestamp
        for previous, current in zip(readings, readings[1:], strict=False)
    ]
    gravity_delta = readings[-1].gravity - readings[0].gravity
    if not math.isfinite(gravity_delta):
        raise ValueError("gravity change is not finite")
    gravity_slope, trend_note = _gravity_trend(readings)
    return FermentationMetrics(
        accepted_count=len(readings),
        rejected_count=len(parsed.rejected),
        started_at=readings[0].timestamp,
        ended_at=readings[-1].timestamp,
        duration=readings[-1].timestamp - readings[0].timestamp,
        latest_gravity=readings[-1].gravity,
        gravity_delta=round(gravity_delta, 6),
        latest_temperature=readings[-1].temperature,
        minimum_temperature=min(temperatures) if temperatures else None,
        maximum_temperature=max(temperatures) if temperatures else None,
        largest_gap=max(gaps, default=timedelta(0)),
        freshness=report_time_utc - readings[-1].timestamp,
        gravity_slope_per_day=gravity_slope,
        trend_note=trend_note,
    )
