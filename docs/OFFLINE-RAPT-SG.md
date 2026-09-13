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

## Offline diagnostic result

`forge_companion.spunding_advisor.explain_telemetry_sg` accepts exactly the same required
arguments as `advise_telemetry_sg`. Use it in place of the advisor in the example above to get
an immutable `TelemetrySgResult`; the old advisor delegates to this single evaluator and
returns only its `.status`. Existing status and policy-exception behavior is preserved.
This is an experimental Python API, not CLI output or a persisted/JSON contract.

The frozen result contains:

- `status`: existing `AdvisorStatus` (`NO_DECISION`, `WAIT`, `CONDITION_MET`).
- `reason`: a `TelemetrySgReason` enum member from the table below.
- `evidence`: a tuple of frozen `TelemetrySgEvidence(observed_at_ns, sg)` candidates, ordered
  oldest to newest within the latest distinct `confirmations` instants. If fewer exist, all
  available candidates are returned. Equal-SG duplicates at an exact instant count once,
  regardless of ID or timezone offset. Each timestamp retains exact 100 ns resolution;
  each SG is `Decimal(str(gravity_raw))`, independent of ambient Decimal precision.
- `distinct_observations`: the distinct-instant count of the **whole valid input series**,
  not just the selected candidates.
- `latest_age_ns`: signed integer `now_ns - latest_instant`; negative means future data.
- `largest_confirmation_gap_ns`: the largest adjacent gap **within a complete selection**;
  `None` when there are too few distinct candidates, even if some gaps could be calculated.

Unit/collection/reading integrity failures return `NO_DECISION`, empty evidence, and `None`
for all three metrics. Validation covers the entire tuple, including readings older than
those selected. No partially validated candidates leak out on a late failure. By contrast,
insufficient/future/stale/gap results retain candidates and applicable metrics for inspection.
**Evidence does not mean quality-approved confirmations**, freshness, safety, or readiness.
Only latest age and gaps inside the latest selection are gated; historical gaps outside it
are not. The diagnostics do not add a slope, calibration check, unit verification, or defaults.

| Reason | Meaning |
| --- | --- |
| `UNDECLARED_UNIT` | Unit is missing or anything other than exact `"sg"`. |
| `INVALID_COLLECTION` | Input is not a tuple. |
| `NO_READINGS` | Empty tuple. |
| `INVALID_READING` | Wrong reading type, blank/non-string identity, or invalid device kind. |
| `INVALID_TIMESTAMP` | Invalid datetime, timezone, or 100 ns remainder. |
| `MISSING_SG` | A reading has `gravity_raw=None`. |
| `INVALID_SG` | SG is not supported finite numeric data in inclusive 0.9..1.2. |
| `MIXED_STREAM` | Source, device kind, or device ID differs within the tuple. |
| `CONFLICTING_SG` | Different SG values occur at the same exact instant. |
| `REUSED_ID` | A reading ID occurs at different exact instants. |
| `INSUFFICIENT_CONFIRMATIONS` | Fewer distinct instants than the requested count. |
| `FUTURE` | Latest age is negative. |
| `STALE` | Latest age exceeds the inclusive age limit. |
| `GAP_EXCEEDED` | A selected gap exceeds the inclusive gap limit. |
| `ABOVE_TRIGGER` | Quality gates passed; at least one selected SG exceeds trigger: `WAIT`. |
| `AT_OR_BELOW_TRIGGER` | Quality gates passed; all selected SGs are at/below trigger: `CONDITION_MET`. |

Invalid policy raises `ValueError` before unit or input checks; omitted required arguments
raise `TypeError`. Reasons report only the first blocker, not an exhaustive defect list.
Unit and collection checks precede per-reading checks in input traversal order, so tuples
with several independent defects can report different reasons when reordered. On a valid
series, precedence is insufficient confirmations, future, stale, gap, then threshold.
None of these outcomes establishes fermentation completion or permission to actuate.

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
