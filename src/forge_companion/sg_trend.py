"""Pure model for the experimental, descriptive vessel SG trend report."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from forge_companion.telemetry import DeviceKind, TelemetryReading

NANOSECONDS_PER_SECOND = 1_000_000_000
QUALITY_WINDOW_NS = 90 * 60 * NANOSECONDS_PER_SECOND


@dataclass(frozen=True)
class TrendSegment:
    phase: str
    sequence: int
    observations: int
    missing_sg: int
    first_at: str | None
    latest_at: str | None
    first_sg: Decimal | None
    latest_sg: Decimal | None
    delta_sg: Decimal | None
    duration_ns: int | None
    rate_sg_per_day: Decimal | None
    max_gap_ns: int | None
    gap_status: str
    coverage_status: str
    endpoint_trend: str
    sg_status: str


@dataclass(frozen=True)
class TrendReport:
    excluded_boundary_observations: int
    unattributed_before_start: int
    segments: tuple[TrendSegment, ...]


def exact_ns(reading: TelemetryReading) -> int:
    """Return an exact integer epoch offset, including RAPT's final 100 ns digit."""
    utc = reading.observed_at.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = utc - epoch
    return (
        (elapsed.days * 86_400 + elapsed.seconds) * NANOSECONDS_PER_SECOND
        + elapsed.microseconds * 1_000
        + reading.observed_at_submicrosecond_ns
    )


def build_trend_report(
    readings: tuple[TelemetryReading, ...],
    *,
    phase_history: list[dict[str, object]],
    phase_timezone: ZoneInfo,
    query_end: datetime,
) -> TrendReport:
    """Describe contiguous phases from validated, in-window single-device readings.

    Callers must also supply validated phase history; this pure function does not
    validate device binding, query-window membership, or phase-history ordering.
    """
    events = [
        (str(item["phase"]), date.fromisoformat(str(item["start_date"]))) for item in phase_history
    ]
    boundary_days = {event_date for _, event_date in events}
    groups: dict[int, list[TelemetryReading]] = {}
    excluded = 0
    before = 0
    for reading in readings:
        if reading.source != "rapt" or reading.device_kind is not DeviceKind.HYDROMETER:
            raise ValueError("trend input contains an unexpected telemetry source")
        local_day = reading.observed_at.astimezone(phase_timezone).date()
        if local_day in boundary_days:
            excluded += 1
            continue
        eligible = [index for index, (_, day) in enumerate(events) if day < local_day]
        if not eligible:
            before += 1
            continue
        groups.setdefault(eligible[-1], []).append(reading)

    end_ns = _datetime_ns(query_end)
    segments = tuple(
        _segment(events[index][0], index + 1, groups[index], end_ns=end_ns)
        for index in sorted(groups)
    )
    return TrendReport(excluded, before, segments)


def _datetime_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("query end must be timezone-aware")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = value.astimezone(UTC) - epoch
    return (
        elapsed.days * 86_400 + elapsed.seconds
    ) * NANOSECONDS_PER_SECOND + elapsed.microseconds * 1_000


def _segment(
    phase: str, sequence: int, readings: list[TelemetryReading], *, end_ns: int
) -> TrendSegment:
    ordered = sorted(readings, key=lambda item: (exact_ns(item), item.reading_id))
    missing = sum(item.gravity_raw is None for item in ordered)
    measured = [item for item in ordered if item.gravity_raw is not None]
    if not measured:
        return TrendSegment(
            phase,
            sequence,
            len(ordered),
            missing,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "NO_DATA",
            "NO_DATA",
            "NO_TREND",
            "NO_DATA",
        )
    instants = [exact_ns(item) for item in measured]
    values_by_instant: dict[int, set[Decimal]] = {}
    for instant, item in zip(instants, measured, strict=True):
        values_by_instant.setdefault(instant, set()).add(Decimal(str(item.gravity_raw)) / 1000)
    conflicts = {instant for instant, values in values_by_instant.items() if len(values) > 1}
    gaps = [right - left for left, right in zip(instants, instants[1:], strict=False)]
    max_gap = max(gaps) if gaps else None
    gap_status = "LARGE_GAPS" if max_gap is not None and max_gap > QUALITY_WINDOW_NS else "OK"
    age = end_ns - instants[-1]
    coverage_status = "STALE" if age > QUALITY_WINDOW_NS else "CURRENT_AT_QUERY_END"
    first_sg = None if instants[0] in conflicts else Decimal(str(measured[0].gravity_raw)) / 1000
    latest_sg = None if instants[-1] in conflicts else Decimal(str(measured[-1].gravity_raw)) / 1000
    distinct = instants[-1] > instants[0]
    duration = instants[-1] - instants[0] if distinct else None
    delta = (
        latest_sg - first_sg
        if distinct and not conflicts and first_sg is not None and latest_sg is not None
        else None
    )
    rate = (
        delta * Decimal(86_400 * NANOSECONDS_PER_SECOND) / Decimal(duration)
        if duration is not None and delta is not None
        else None
    )
    complete = (
        len(values_by_instant) >= 3
        and distinct
        and not missing
        and not conflicts
        and gap_status == "OK"
        and coverage_status == "CURRENT_AT_QUERY_END"
    )
    direction = "NO_TREND"
    if complete and delta is not None:
        direction = "FALLING" if delta < 0 else "RISING" if delta > 0 else "UNCHANGED"
    return TrendSegment(
        phase,
        sequence,
        len(ordered),
        missing,
        measured[0].observed_at_exact,
        measured[-1].observed_at_exact,
        first_sg,
        latest_sg,
        delta,
        duration,
        rate,
        max_gap,
        gap_status,
        coverage_status,
        direction,
        "CONFLICTING_SG_AT_TIMESTAMP" if conflicts else "OK",
    )
