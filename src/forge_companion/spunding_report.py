"""Deterministic terminal rendering for simulation-only spunding advice."""

import math
import re
import unicodedata
from datetime import UTC, timedelta

from forge_companion.spunding_advisor import AdvisorResult


def _duration(value: timedelta | None) -> str:
    if value is None:
        return "not available"
    seconds = max(0, int(value.total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _safe_text(value: str, *, limit: int = 160) -> str:
    value = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", value)
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character for character in value
    )
    normalized = " ".join(without_controls.split()).replace("|", "\\|")
    if len(normalized) > limit:
        return normalized[: limit - 3] + "..."
    return normalized


def render_spunding_advice(result: AdvisorResult) -> str:
    """Render evidence without implying that an actuator command is safe."""
    if result.gravity_slope_per_day is not None and math.isfinite(result.gravity_slope_per_day):
        trend = f"{result.gravity_slope_per_day:+.4f} SG/day (descriptive only)"
    else:
        note = _safe_text(result.trend_note or "not available")
        trend = f"not available — {note}"

    lines = [
        f"Spunding advisor: {result.status.value}",
        f"Reason: {_safe_text(result.reason)}",
        "Simulation only: no device command was sent.",
        "Safety: this does not verify pressure, valve position, regulator, or PRV safety.",
        f"Trigger SG: {result.trigger_sg:.4f}",
        f"Latest reading age: {_duration(result.latest_age)}",
        f"Largest confirmation gap: {_duration(result.largest_confirmation_gap)}",
        f"Trend: {trend}",
        "Evidence:",
    ]
    if not result.evidence:
        lines.append("- none")
    else:
        lines.extend(
            "- "
            f"{_safe_text(item.reading_id)} | "
            f"{item.timestamp.astimezone(UTC).isoformat()} | "
            f"{item.gravity:.4f} SG"
            for item in result.evidence
        )
    return "\n".join(lines) + "\n"
