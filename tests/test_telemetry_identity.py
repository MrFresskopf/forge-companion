from datetime import UTC, datetime

import pytest

from forge_companion.telemetry import DeviceKind, TelemetryReading
from forge_companion.telemetry_identity import (
    TelemetryIdentityValidationError,
    validate_telemetry_identity,
)


def reading(**changes):
    values = {
        "source": "rapt",
        "device_kind": DeviceKind.HYDROMETER,
        "device_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "reading_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "observed_at": datetime(1970, 1, 1, tzinfo=UTC),
        "observed_at_submicrosecond_ns": 900,
        "temperature_c": 20.0,
        "gravity_raw": 1.018,
        "gravity_velocity_raw": None,
        "target_temperature_c": None,
        "battery_percent": 90.0,
        "rssi": -60.0,
    }
    values.update(changes)
    return TelemetryReading(**values)


def test_validate_telemetry_identity_retains_exact_time_and_canonical_stream():
    result = validate_telemetry_identity(reading())

    assert result.observed_at_ns == 900
    assert result.stream == (
        "rapt",
        DeviceKind.HYDROMETER,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    assert result.reading_id == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.mark.parametrize(
    "changes",
    [
        {"source": " rapt"},
        {"device_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        {"reading_id": "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB"},
        {"observed_at": datetime(1970, 1, 1)},
        {"observed_at_submicrosecond_ns": 1},
    ],
)
def test_validate_telemetry_identity_rejects_untrusted_stream_or_exact_time(changes):
    with pytest.raises(TelemetryIdentityValidationError):
        validate_telemetry_identity(reading(**changes))
