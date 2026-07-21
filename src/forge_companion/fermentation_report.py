"""Deterministic Markdown rendering for fermentation briefs."""

import html
import re
import string
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from forge_companion.fermentation import FermentationMetrics, ParseResult
from forge_companion.file_io import atomic_write_text

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_MARKDOWN_PUNCTUATION = frozenset(string.punctuation)


def _duration(value: timedelta) -> str:
    seconds = int(value.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return sign + " ".join(parts)


def _table_text(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def _rejection_text(reason: str) -> str:
    normalized = " ".join(reason.split())
    if len(normalized) > 160:
        normalized = normalized[:157] + "..."
    escaped = html.escape(normalized, quote=False).replace("\\", "\\\\")
    for character in ("`", "*", "_", "[", "]", "#", "|"):
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _markdown_heading_text(value: str) -> str:
    without_ansi = _ANSI_ESCAPE.sub("", value)
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in without_ansi
    )
    normalized = " ".join(without_controls.split())[:160] or "Unnamed brew"
    return "".join(
        f"\\{character}" if character in _MARKDOWN_PUNCTUATION else character
        for character in normalized
    )


def _temperature(value: float | None, unit: str | None) -> str:
    if value is None:
        return "not available"
    suffix = f" °{unit}" if unit is not None else " (raw API value)"
    return f"{value:.1f}{suffix}"


def render_markdown(
    *,
    brew_name: str,
    brew_id: str,
    parsed: ParseResult,
    metrics: FermentationMetrics,
    report_time: datetime,
    temperature_unit: str | None,
) -> str:
    """Render one shareable, non-predictive Markdown brief."""
    if temperature_unit not in {None, "C", "F"}:
        raise ValueError("temperature unit must be C or F")

    safe_name = _markdown_heading_text(brew_name)
    temperature_label = (
        f"Temperature (°{temperature_unit})"
        if temperature_unit is not None
        else "Temperature (raw API value)"
    )
    slope = (
        f"{metrics.gravity_slope_per_day:+.4f} SG/day"
        if metrics.gravity_slope_per_day is not None
        else f"not available — {metrics.trend_note}"
    )
    if metrics.minimum_temperature is None or metrics.maximum_temperature is None:
        temperature_range = "not available"
    elif temperature_unit is None:
        temperature_range = (
            f"{metrics.minimum_temperature:.1f}–{metrics.maximum_temperature:.1f} (raw API values)"
        )
    else:
        temperature_range = (
            f"{metrics.minimum_temperature:.1f}–{metrics.maximum_temperature:.1f} "
            f"°{temperature_unit}"
        )

    lines = [
        f"# Fermentation Brief: {safe_name}",
        "",
        f"> BrewForge brew ID: `{brew_id}`  ",
        f"> Generated: {report_time.isoformat()}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Latest gravity | {metrics.latest_gravity:.4f} SG |",
        f"| Gravity change | {metrics.gravity_delta:+.4f} SG |",
        f"| 24-hour gravity slope | {slope} |",
        f"| Latest temperature | {_temperature(metrics.latest_temperature, temperature_unit)} |",
        f"| Temperature range | {temperature_range} |",
        f"| Observation period | {_duration(metrics.duration)} |",
        f"| Latest-reading age | {_duration(metrics.freshness)} |",
        "",
        "## Data quality",
        "",
        f"- Accepted readings: **{metrics.accepted_count}**",
        f"- Rejected readings: **{metrics.rejected_count}**",
        f"- Conflicting timestamps: **{len(parsed.conflicting_timestamps)}**",
        f"- Largest observed telemetry gap: **{_duration(metrics.largest_gap)}**",
        f"- Trend method: {metrics.trend_note}.",
    ]
    if parsed.rejected:
        lines.extend(["", "### Rejection details", ""])
        lines.extend(f"- {_rejection_text(reason)}" for reason in parsed.rejected[:10])
        omitted_count = len(parsed.rejected) - 10
        if omitted_count > 0:
            lines.append(f"- … {omitted_count} additional rejection reasons omitted.")
    lines.extend(
        [
            "",
            "## Recent readings",
            "",
            f"| Timestamp (UTC) | Gravity (SG) | {temperature_label} | Comment |",
            "|---|---:|---:|---|",
        ]
    )
    for reading in parsed.readings[-12:]:
        lines.append(
            "| "
            f"{reading.timestamp.isoformat()} | {reading.gravity:.4f} | "
            f"{_temperature(reading.temperature, temperature_unit)} | "
            f"{_table_text(reading.comment or '')} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation boundaries",
            "",
            (
                "- This report describes stored BrewForge telemetry; it does not prove "
                "that every upstream transmission arrived."
            ),
            "- Gravity slopes can include hydrometer noise and are descriptive, not predictions.",
            (
                "- This brief does **not** declare fermentation complete and must not "
                "trigger hardware or pressure-control actions."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_markdown(content: str, destination: Path) -> None:
    """Write a report atomically without a predictable shared temp path."""
    atomic_write_text(content, destination, newline="\n")
