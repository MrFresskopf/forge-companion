"""Deterministic terminal rendering for simulation-only spunding advice."""

import math
from datetime import UTC, timedelta

from forge_companion.spunding_advisor import AdvisorResult
from forge_companion.terminal_text import safe_terminal_text


def _duration(value: timedelta | None) -> str:
    if value is None:
        return "not available"
    seconds = max(0, int(value.total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def render_spunding_advice(result: AdvisorResult) -> str:
    """Render evidence without implying that an actuator command is safe."""
    if result.gravity_slope_per_day is not None and math.isfinite(result.gravity_slope_per_day):
        trend = f"{result.gravity_slope_per_day:+.4f} SG/day (descriptive only)"
    else:
        note = safe_terminal_text(result.trend_note or "not available")
        trend = f"not available — {note}"

    lines = [
        f"Spunding advisor: {result.status.value}",
        f"Reason: {safe_terminal_text(result.reason)}",
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
            f"{safe_terminal_text(item.reading_id)} | "
            f"{item.timestamp.astimezone(UTC).isoformat()} | "
            f"{item.gravity:.4f} SG"
            for item in result.evidence
        )
    return "\n".join(lines) + "\n"
