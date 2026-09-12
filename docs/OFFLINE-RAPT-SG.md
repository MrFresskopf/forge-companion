# Offline RAPT → SG bridge (experimental Python API)

`forge_companion.rapt_sg.normalize_rapt_sg` is a pure, additive conversion boundary, not a
live RAPT integration. It accepts the tuple returned by `parse_rapt_telemetry` only with the
explicit caller assertion `gravity_interpretation="sg-times-1000"`. RAPT's OpenAPI does not
verify those units; plausible magnitudes are never used to select an interpretation.

```python
from decimal import Decimal

from forge_companion.rapt_sg import normalize_rapt_sg
from forge_companion.spunding_advisor import advise_telemetry_sg
from forge_companion.telemetry import DeviceKind, parse_rapt_telemetry

# payload and device_id must already be supplied offline by the caller.
raw = parse_rapt_telemetry(payload, kind=DeviceKind.HYDROMETER, device_id=device_id)
sg_readings = normalize_rapt_sg(raw, gravity_interpretation="sg-times-1000")
status = advise_telemetry_sg(
    sg_readings,
    gravity_unit="sg",
    now_ns=now_ns,                   # explicit integer UTC Unix epoch nanoseconds
    trigger_sg=Decimal(trigger_text),
    max_age_ns=max_age_ns,           # explicit positive integer, inclusive
    max_gap_ns=max_gap_ns,           # explicit positive integer, inclusive
    confirmations=confirmations,    # explicit integer 2..5, not a boolean
)
```

All operational policy remains required by the existing advisor; the bridge selects none.
`WAIT`, `CONDITION_MET`, and `NO_DECISION` are advisory statuses only. None grants actuator
permission, establishes fermentation completion, or says packaging or any action is safe.

## Input and output boundary

- One tuple of exact `TelemetryReading` instances from source `rapt`, kind `HYDROMETER`,
  and one canonical device UUID is accepted. Other sources/kinds, mixed devices, malformed
  IDs/times/numbers, and unknown interpretations raise `TelemetryValidationError`. A missing
  required argument raises Python's `TypeError`. Errors return no partial tuple.
- Present gravity must be finite numeric data (not bool) in **900..1200**, inclusive, to
  produce the existing advisor's supported **0.9..1.2 SG** interval. This range is a validation
  bound after the caller assertion, not unit detection. It rejects ordinary already-SG inputs.
- Output is a tuple of frozen `SgTelemetryReading` instances, a `TelemetryReading` subclass
  whose `gravity_raw` field is now SG for compatibility with `advise_telemetry_sg`. The marker
  is rejected as bridge input, including all-missing-SG streams. Empty tuples stay empty.
  This is an in-memory marker, **not a new serialization schema**; deliberately reconstructing
  base-class objects loses the marker and is unsupported.
- Every other field is retained unchanged, including source/device/reading IDs, temperatures,
  battery, RSSI, timestamp datetime and its exact 100 ns remainder. Missing SG stays `None`;
  no readings are dropped, sorted, deduplicated, or modified in place.
- **Gravity velocity is not converted** and its unit remains unknown. Do not interpret it as
  SG/day. Do not send output to raw-RAPT consumers such as `build_trend_report` in `sg_trend`,
  which already divides raw gravity by 1000. Use the marker only at an explicitly SG-valued
  consumer boundary; existing APIs and parsers were not changed to enforce it everywhere.
- Equal-time conflicts and repeated IDs remain present for the existing advisor to reject;
  exact-time deduplication, freshness, future time, gaps, and confirmation policy remain there.
  Missing SG and insufficient distinct observations produce `NO_DECISION`, not invented data.

## Numeric limits

Conversion takes the existing float's shortest decimal text (`Decimal(str(raw))`) and shifts
its exponent by three places without Decimal arithmetic dependent on ambient context precision.
The existing advisor still consumes floats through `Decimal(str(value))`; the bridge therefore
rejects any SG float whose decimal round trip differs from the exact shifted decimal. It does
not round away raw conflicts to manufacture equal confirmations, even at very low Decimal
precision or with rounding traps enabled. This conservative rule can reject otherwise plausible
high-precision raw values. It does **not** promise exact binary division or recover information
already lost when JSON numbers entered the existing float-based parser.

There are no credentials, configuration reads, API requests, file writes, hardware dependencies,
actuator calls, new CLI commands, or persistent schemas in this step. Live unit validation and
any future operational integration remain separate work requiring explicit approval.
