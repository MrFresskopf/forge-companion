# ruff: noqa: E501
"""Self-contained HTML fermentation reports."""

import html
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from forge_companion.fermentation import FermentationMetrics, FermentationReading, ParseResult


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _normalized_text(value: object, *, limit: int) -> str:
    text = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", str(value))
    without_controls = "".join(
        " " if unicodedata.category(character).startswith("C") else character for character in text
    )
    normalized = " ".join(without_controls.split())
    if len(normalized) > limit:
        normalized = normalized[: limit - 3] + "..."
    return normalized


def _safe_text(value: object, *, limit: int) -> str:
    return _escape(_normalized_text(value, limit=limit))


def _duration(value: timedelta) -> str:
    seconds = max(0, int(value.total_seconds()))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _padded_range(
    values: list[float], *, ratio: float, minimum_padding: float
) -> tuple[float, float]:
    """Return finite padded bounds without subtracting extreme floats directly."""
    low, high = min(values), max(values)
    scale = max(abs(low), abs(high), 1.0)
    max_normalized = sys.float_info.max / scale
    low_normalized = low / scale
    high_normalized = high / scale

    if low == high:
        padding_normalized = max(ratio, minimum_padding / scale)
    else:
        padding_normalized = max(
            (high_normalized - low_normalized) * ratio,
            minimum_padding / scale,
        )

    padded_low = max(-max_normalized, low_normalized - padding_normalized)
    padded_high = min(max_normalized, high_normalized + padding_normalized)
    return _finite_product(padded_low, scale), _finite_product(padded_high, scale)


def _finite_product(value: float, scale: float) -> float:
    product = value * scale
    if math.isfinite(product):
        return product
    return math.copysign(sys.float_info.max, value)


def _scaled(value: float, low: float, high: float, top: float, bottom: float) -> float:
    if high == low:
        return (top + bottom) / 2
    scale = max(abs(value), abs(low), abs(high), 1.0)
    normalized_low = low / scale
    normalized_high = high / scale
    normalized_value = value / scale
    fraction = (normalized_value - normalized_low) / (normalized_high - normalized_low)
    return bottom - fraction * (bottom - top)


def _temperature_number(value: float) -> str:
    absolute = abs(value)
    if absolute >= 10_000 or (0 < absolute < 0.01):
        return f"{value:.3g}"
    return f"{value:.1f}"


def _gravity_number(value: float, *, signed: bool = False) -> str:
    absolute = abs(value)
    sign = "+" if signed else ""
    if absolute >= 10_000 or (0 < absolute < 0.0001):
        return format(value, f"{sign}.4g")
    return format(value, f"{sign}.4f")


def _x_position(reading: FermentationReading, readings: tuple[FermentationReading, ...]) -> float:
    left, right = 70.0, 690.0
    span = (readings[-1].timestamp - readings[0].timestamp).total_seconds()
    if span <= 0:
        return (left + right) / 2
    elapsed = (reading.timestamp - readings[0].timestamp).total_seconds()
    return left + (elapsed / span) * (right - left)


def _chart(parsed: ParseResult, temperature_unit: str | None) -> str:
    readings = parsed.readings
    gravities = [reading.gravity for reading in readings]
    gravity_low, gravity_high = _padded_range(gravities, ratio=0.08, minimum_padding=0.0005)
    gravity_points = " ".join(
        f"{_x_position(reading, readings):.1f},"
        f"{_scaled(reading.gravity, gravity_low, gravity_high, 32, 238):.1f}"
        for reading in readings
    )
    gravity_markers = "".join(
        f'<circle class="gravity-point" cx="{_x_position(reading, readings):.1f}" '
        f'cy="{_scaled(reading.gravity, gravity_low, gravity_high, 32, 238):.1f}" r="2.4" />'
        for reading in readings
    )

    with_temperature = tuple(reading for reading in readings if reading.temperature is not None)
    temperature_points = ""
    temperature_markers = ""
    temperature_low: float | None = None
    temperature_high: float | None = None
    if with_temperature:
        temperatures = [reading.temperature for reading in with_temperature]
        temperature_low, temperature_high = _padded_range(
            [value for value in temperatures if value is not None],
            ratio=0.08,
            minimum_padding=0.2,
        )
        temperature_points = " ".join(
            f"{_x_position(reading, readings):.1f},"
            f"{_scaled(reading.temperature, temperature_low, temperature_high, 32, 238):.1f}"
            for reading in with_temperature
            if reading.temperature is not None
        )
        temperature_markers = "".join(
            f'<circle class="temperature-point" cx="{_x_position(reading, readings):.1f}" '
            f'cy="{_scaled(reading.temperature, temperature_low, temperature_high, 32, 238):.1f}" r="2.2" />'
            for reading in with_temperature
            if reading.temperature is not None
        )

    unit = f"°{temperature_unit}" if temperature_unit is not None else "raw"
    temperature_line = (
        f'<polyline class="temperature-line" points="{temperature_points}" />'
        if temperature_points
        else ""
    )
    temperature_legend = (
        '<line class="temperature-line" x1="405" y1="289" x2="433" y2="289" />'
        f'<text x="439" y="293">Temperature ({unit})</text>'
        if temperature_points
        else ""
    )
    temperature_labels = ""
    if temperature_low is not None and temperature_high is not None:
        temperature_labels = (
            f'<text x="704" y="38">{_temperature_number(temperature_high)} {unit}</text>'
            f'<text x="704" y="242">{_temperature_number(temperature_low)} {unit}</text>'
        )

    start_label = readings[0].timestamp.strftime("%Y-%m-%d %H:%MZ")
    end_label = readings[-1].timestamp.strftime("%Y-%m-%d %H:%MZ")
    chart_label = "Gravity and temperature over time" if with_temperature else "Gravity over time"
    return f"""<svg viewBox="0 0 760 300" role="img" aria-label="{chart_label}">
      <line class="grid" x1="70" y1="32" x2="690" y2="32" />
      <line class="grid" x1="70" y1="135" x2="690" y2="135" />
      <line class="axis" x1="70" y1="238" x2="690" y2="238" />
      <text x="60" y="38" text-anchor="end">{_gravity_number(gravity_high)}</text>
      <text x="60" y="242" text-anchor="end">{_gravity_number(gravity_low)}</text>
      {temperature_labels}
      <polyline class="gravity-line" points="{gravity_points}" />
      {gravity_markers}
      {temperature_line}
      {temperature_markers}
      <text x="70" y="270">{start_label}</text>
      <text x="690" y="270" text-anchor="end">{end_label}</text>
      <g class="legend">
        <line class="gravity-line" x1="250" y1="289" x2="278" y2="289" />
        <text x="284" y="293">Gravity (SG)</text>
        {temperature_legend}
      </g>
    </svg>"""


def _temperature(value: float | None, temperature_unit: str | None) -> str:
    if value is None:
        return "not available"
    suffix = f" °{temperature_unit}" if temperature_unit is not None else " (raw API value)"
    return f"{_temperature_number(value)}{suffix}"


def render_html(
    *,
    title: str,
    brew_id: str,
    parsed: ParseResult,
    metrics: FermentationMetrics,
    report_time: datetime,
    temperature_unit: str | None,
) -> str:
    """Render a presentation-grade, dependency-free fermentation report."""
    if temperature_unit not in {None, "C", "F"}:
        raise ValueError("temperature unit must be C or F")
    if not parsed.readings:
        raise ValueError("no valid fermentation readings")

    normalized_title = _normalized_text(title, limit=160) or "Fermentation report"
    safe_title = _escape(normalized_title)
    unit = f"°{temperature_unit}" if temperature_unit is not None else "raw API value"
    if metrics.minimum_temperature is None or metrics.maximum_temperature is None:
        temperature_range = "not available"
    elif temperature_unit is None:
        temperature_range = (
            f"{_temperature_number(metrics.minimum_temperature)}–"
            f"{_temperature_number(metrics.maximum_temperature)} (raw API values)"
        )
    else:
        temperature_range = (
            f"{_temperature_number(metrics.minimum_temperature)}–"
            f"{_temperature_number(metrics.maximum_temperature)} "
            f"°{temperature_unit}"
        )
    slope = (
        f"{metrics.gravity_slope_per_day:+.4f} SG/day"
        if metrics.gravity_slope_per_day is not None
        else f"not available — {metrics.trend_note}"
    )

    rows = []
    for reading in parsed.readings[-12:]:
        comment = _safe_text(reading.comment or "", limit=240)
        rows.append(
            "<tr>"
            f"<td>{reading.timestamp.strftime('%Y-%m-%d %H:%M:%SZ')}</td>"
            f"<td>{_gravity_number(reading.gravity)}</td>"
            f"<td>{_temperature(reading.temperature, temperature_unit)}</td>"
            f"<td>{comment}</td>"
            "</tr>"
        )

    rejection_details = ""
    if parsed.rejected:
        rejection_rows = "".join(
            f"<li>{_safe_text(reason, limit=180)}</li>" for reason in parsed.rejected[:10]
        )
        omitted = len(parsed.rejected) - 10
        omitted_row = (
            f"<li>{omitted} additional rejection reasons omitted.</li>" if omitted > 0 else ""
        )
        rejection_details = (
            '<div class="rejections"><h3>Rejection details</h3>'
            f"<ul>{rejection_rows}{omitted_row}</ul></div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fermentation Report: {safe_title}</title>
<style>
:root {{
  --ivory: #FAF9F5; --white: #FFFFFF; --slate: #141413; --clay: #D97757;
  --olive: #788C5D; --rust: #B04A3F; --oat: #E3DACC; --gray-150: #F0EEE6;
  --gray-300: #D1CFC5; --gray-500: #87867F; --gray-700: #3D3D3A;
  --border: 1.5px solid var(--gray-300); --radius-panel: 12px; --radius-row: 8px;
  --serif: ui-serif, Georgia, "Times New Roman", serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 56px 24px 120px; background: var(--ivory); color: var(--gray-700); font-family: var(--sans); line-height: 1.6; }}
.page {{ max-width: 920px; margin: 0 auto; }}
header {{ margin-bottom: 38px; }}
.eyebrow, .meta, .label {{ font-family: var(--mono); font-size: 11px; color: var(--gray-500); }}
.eyebrow {{ letter-spacing: .08em; text-transform: uppercase; }}
h1, h2 {{ font-family: var(--serif); font-weight: 500; letter-spacing: -.01em; color: var(--slate); }}
h1 {{ font-size: 38px; margin: 5px 0 8px; }} h2 {{ font-size: 24px; margin: 0 0 15px; }}
section {{ margin-bottom: 48px; }}
.summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
.summary.primary {{ grid-template-columns: repeat(3, 1fr); }}
.card, .chart, .table-wrap {{ background: var(--white); border: var(--border); border-radius: var(--radius-panel); }}
.card {{ padding: 18px; }} .value {{ font-family: var(--serif); font-size: 27px; color: var(--slate); }}
.chart {{ padding: 20px; }} svg {{ display: block; width: 100%; height: auto; }}
.chart-caption {{ margin-top: 10px; font-size: 12px; color: var(--gray-500); }}
svg text {{ font: 11px var(--mono); fill: var(--gray-500); }}
.grid {{ stroke: var(--gray-150); }} .axis {{ stroke: var(--gray-300); stroke-width: 1.5; }}
.gravity-line, .temperature-line {{ fill: none; stroke-linecap: round; stroke-linejoin: round; stroke-width: 3; }}
.gravity-line {{ stroke: var(--clay); }} .temperature-line {{ stroke: var(--olive); }}
.gravity-point {{ fill: var(--clay); }} .temperature-point {{ fill: var(--olive); }}
.table-wrap {{ overflow-x: auto; }} table {{ width: 100%; border-collapse: collapse; }}
th {{ background: var(--gray-150); font: 11px var(--mono); text-transform: uppercase; letter-spacing: .05em; text-align: left; }}
th, td {{ padding: 11px 14px; border-bottom: 1px solid var(--gray-150); }} td {{ font-size: 13px; }}
.callout {{ padding: 15px 17px; border-left: 4px solid var(--clay); background: rgba(217,119,87,.07); border-radius: var(--radius-row); }}
.rejections {{ margin-top: 16px; padding: 16px 18px; background: var(--gray-150); border-radius: var(--radius-row); }}
.rejections h3 {{ margin: 0 0 8px; font: 600 13px var(--sans); color: var(--slate); }}
.rejections ul {{ margin: 0; padding-left: 20px; font: 12px/1.6 var(--mono); }}
footer {{ border-top: 1px solid var(--gray-300); padding-top: 18px; font: 11px var(--mono); color: var(--gray-500); }}
@media (max-width: 720px) {{ .summary, .summary.primary {{ grid-template-columns: repeat(2, 1fr); }} body {{ padding: 32px 14px 80px; }} }}
</style>
</head>
<body>
<div class="page">
<header>
  <div class="eyebrow">Forge Companion · Read-only fermentation telemetry</div>
  <h1>Fermentation Report: {safe_title}</h1>
  <div class="meta">Brew {_escape(brew_id)} · generated {_escape(report_time.isoformat())}</div>
</header>
<section class="summary primary">
  <div class="card"><div class="label">Starting gravity</div><div class="value">{_gravity_number(parsed.readings[0].gravity)} SG</div></div>
  <div class="card"><div class="label">Latest gravity</div><div class="value">{_gravity_number(metrics.latest_gravity)} SG</div></div>
  <div class="card"><div class="label">Gravity change</div><div class="value">{_gravity_number(metrics.gravity_delta, signed=True)} SG</div></div>
  <div class="card"><div class="label">Temperature range</div><div class="value">{temperature_range}</div></div>
  <div class="card"><div class="label">Observation period</div><div class="value">{_duration(metrics.duration)}</div></div>
  <div class="card"><div class="label">Latest-reading age</div><div class="value">{_duration(metrics.freshness)}</div></div>
</section>
<section>
  <h2>Fermentation trace</h2>
  <div class="chart">
    {_chart(parsed, temperature_unit)}
    <div class="chart-caption">24-hour gravity trend: {_escape(slope)}. Method: {_escape(metrics.trend_note)}.</div>
  </div>
</section>
<section>
  <h2>Data quality</h2>
  <div class="summary">
    <div class="card"><div class="label">Accepted</div><div class="value">{metrics.accepted_count}</div></div>
    <div class="card"><div class="label">Rejected</div><div class="value">{metrics.rejected_count}</div></div>
    <div class="card"><div class="label">Conflicts</div><div class="value">{len(parsed.conflicting_timestamps)}</div></div>
    <div class="card"><div class="label">Largest gap</div><div class="value">{_duration(metrics.largest_gap)}</div></div>
  </div>
  {rejection_details}
</section>
<section>
  <h2>Recent readings</h2>
  <div class="table-wrap"><table>
    <thead><tr><th>Timestamp (UTC)</th><th>Gravity (SG)</th><th>Temperature ({unit})</th><th>Comment</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
</section>
<section class="callout">
  <strong>Interpretation boundary.</strong> This report describes stored BrewForge telemetry. It does not declare fermentation complete and must not trigger hardware or pressure-control actions.
</section>
<footer>Self-contained HTML · no external resources · Forge Companion unofficial community project</footer>
</div>
</body>
</html>
"""


def write_html(content: str, destination: Path) -> None:
    """Write a standalone report atomically and clean up failed temp files."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
