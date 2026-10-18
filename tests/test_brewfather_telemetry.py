from datetime import UTC, datetime

import pytest

from forge_companion.brewfather_telemetry import adapt_brewfather_readings
from forge_companion.telemetry import DeviceKind, TelemetryValidationError

DEVICE_ID = "rapt-pill-native-stream"


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "id": "reading-1",
        "deviceId": DEVICE_ID,
        "deviceType": "Hydrometer",
        "name": "private Pill name",
        "time": 1720000000000,
        "sg": 1.0123,
        "temp": 20.1,
        "battery": 92,
        "rssi": -50,
        "target_temp": None,
        "type": "gravity",
    }
    record.update(overrides)
    return record


def test_adapter_normalizes_one_explicit_native_pill_stream() -> None:
    readings = adapt_brewfather_readings(
        [_record(), _record(id="reading-2", time=1720003600000, sg=1.0118, temp=20)],
        device_id=DEVICE_ID,
        gravity_unit="sg",
        temperature_unit="c",
        time_unit="epoch-ms",
    )

    identities = [
        (item.source, item.device_kind, item.device_id, item.reading_id) for item in readings
    ]
    assert identities == [
        ("brewfather", DeviceKind.HYDROMETER, DEVICE_ID, "reading-1"),
        ("brewfather", DeviceKind.HYDROMETER, DEVICE_ID, "reading-2"),
    ]
    assert readings[0].observed_at == datetime(2024, 7, 3, 9, 46, 40, tzinfo=UTC)
    assert readings[0].gravity_raw == 1.0123
    assert readings[0].gravity_unit == "sg"
    assert readings[0].temperature_c == 20.1
    assert readings[0].temperature_unit == "c"
    assert readings[0].battery_percent is None
    assert readings[0].rssi == -50.0


def test_adapter_filters_other_batch_devices_before_validating_the_selected_stream() -> None:
    readings = adapt_brewfather_readings(
        [_record(), {"deviceId": "other", "unexpected": object()}],
        device_id=DEVICE_ID,
        gravity_unit="sg",
        temperature_unit="c",
        time_unit="epoch-ms",
    )

    assert len(readings) == 1


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "no selected device records"),
        ([_record(sg=1.3)], "invalid gravity"),
        ([_record(temp=-274)], "invalid temperature"),
        ([_record(time=True)], "invalid timestamp"),
    ],
)
def test_adapter_rejects_invalid_selected_native_records(payload: object, message: str) -> None:
    with pytest.raises(TelemetryValidationError, match=message):
        adapt_brewfather_readings(
            payload,
            device_id=DEVICE_ID,
            gravity_unit="sg",
            temperature_unit="c",
            time_unit="epoch-ms",
        )


def test_adapter_preserves_provider_duplicate_ids_with_derived_local_identities() -> None:
    readings = adapt_brewfather_readings(
        [_record(id="same"), _record(id="same", time=1720003600000, sg=1.0118)],
        device_id=DEVICE_ID,
        gravity_unit="sg",
        temperature_unit="c",
        time_unit="epoch-ms",
    )

    assert [item.reading_id for item in readings] == ["same#0", "same#1"]


def test_adapter_requires_explicit_confirmed_units() -> None:
    with pytest.raises(TelemetryValidationError, match="units must be explicit"):
        adapt_brewfather_readings(
            [_record()],
            device_id=DEVICE_ID,
            gravity_unit="unknown",
            temperature_unit="c",
            time_unit="epoch-ms",
        )
