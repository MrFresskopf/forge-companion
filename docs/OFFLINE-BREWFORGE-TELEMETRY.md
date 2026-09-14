# Offline BrewForge → neutral telemetry adapter (experimental Python API)

`forge_companion.brewforge_telemetry.adapt_brewforge_readings` is a pure adapter for an
already-decoded BrewForge `GET /brews/:id/readings` response. It performs no API request and reads no
credentials, configuration, files, clock, or hardware.

```python
from forge_companion.brewforge_telemetry import adapt_brewforge_readings

readings = adapt_brewforge_readings(
    payload,                         # complete decoded {"data": [...]} response
    brew_id=canonical_brew_uuid,
    gravity_unit="sg",              # required caller assertion
    temperature_unit="c",           # required caller assertion
)
```

The existing BrewForge contract and observed responses represent gravity as SG. Therefore this
boundary supports only exact `gravity_unit="sg"`; it deliberately rejects `sg-times-1000` and does
not reuse `normalize_rapt_sg`. Temperature has been observed as Celsius in one setup but is not a
universal server guarantee, so exact `temperature_unit="c"` is also required. Plausible values never
select either unit. The output stores these declarations in `gravity_unit` and `temperature_unit`;
a declaration is not independent unit verification or calibration evidence.

## Identity and time

- `source` is `brewforge` and `device_kind` is `DeviceKind.HYDROMETER`, the existing neutral kind
  for gravity-bearing fermentation streams. It does not assert that a physical hydrometer produced
  a particular stored record.
- `device_id` is the caller-supplied canonical BrewForge **brew UUID**. It identifies the stored
  stream and does not claim or fabricate a physical-device UUID.
- BrewForge reading IDs are treated as opaque source identifiers. They must be non-empty, printable,
  and already canonical (no surrounding whitespace); accepted text is preserved exactly.
- RFC 3339 timestamps are normalized to UTC. Up to seven fractional digits are preserved through
  `observed_at` plus `observed_at_submicrosecond_ns`, and `observed_at_exact` renders the canonical
  exact instant. Results are sorted by exact instant and reading ID.

## Fail-closed boundary

The complete response is validated before a tuple is returned. Any malformed collection or record,
invalid identity or timestamp, repeated reading ID, repeated exact instant, wrong field type,
boolean/non-finite number, SG outside inclusive `0.9..1.2`, or Celsius below absolute zero raises
`TelemetryValidationError`; no partial tuple is returned. Gravity and temperature may each be absent
and remain `None`, never zero. Observed `pressure`, `ph`, and `comment` fields are validated but not
copied because `TelemetryReading` has no corresponding neutral channels. Additional response and
record fields remain ignored for compatibility with the existing forward-compatible BrewForge
reading parser.

The returned tuple and `TelemetryReading` values are immutable. This adapter does not deduplicate,
choose freshness/gap/confirmation thresholds, consult current time, apply advisor policy, claim
calibration or delivery completeness, establish live freshness or fermentation completion, or grant
safety/readiness/actuator permission. Consumers such as `advise_telemetry_sg` retain all of their
existing explicit policy arguments and fail-closed rules.

This is an additive experimental Python API, not a CLI command, manifest entry, persisted schema,
configuration format, credential boundary, network integration, or hardware-control path.
