from datetime import UTC, datetime

import pytest

from forge_companion.telemetry import (
    DeviceKind,
    RaptTelemetrySource,
    TelemetryReading,
    TelemetryValidationError,
    parse_rapt_telemetry,
)


def test_hydrometer_payload_is_normalized_into_source_neutral_readings() -> None:
    payload = [
        {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "createdOn": "2026-09-02T08:00:00Z",
            "temperature": 21.5,
            "gravity": 1.0123,
            "gravityVelocity": -0.0012,
            "battery": 78.0,
            "rssi": -62.0,
            "futureField": "ignored",
        }
    ]

    readings = parse_rapt_telemetry(
        payload,
        kind=DeviceKind.HYDROMETER,
        device_id="11111111-1111-1111-1111-111111111111",
    )

    assert readings == (
        TelemetryReading(
            source="rapt",
            device_kind=DeviceKind.HYDROMETER,
            device_id="11111111-1111-1111-1111-111111111111",
            reading_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
            temperature_c=21.5,
            gravity_raw=1.0123,
            gravity_velocity_raw=-0.0012,
            target_temperature_c=None,
            battery_percent=78.0,
            rssi=-62.0,
        ),
    )


def test_temperature_controller_payload_keeps_target_temperature_without_inventing_gravity() -> (
    None
):
    readings = parse_rapt_telemetry(
        [
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "createdOn": "2026-09-02T09:00:00+00:00",
                "temperature": 18.4,
                "targetTemperature": 19.0,
                "controlDeviceTemperature": 18.1,
                "rssi": -55,
            }
        ],
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
        device_id="22222222-2222-2222-2222-222222222222",
    )

    assert readings[0].temperature_c == 18.4
    assert readings[0].target_temperature_c == 19.0
    assert readings[0].control_temperature_c == 18.1
    assert readings[0].gravity_raw is None
    assert readings[0].battery_percent is None


def test_one_invalid_record_rejects_the_complete_automation_input() -> None:
    payload = [
        {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "createdOn": "2026-09-02T08:00:00Z",
            "temperature": 21.5,
            "gravity": 1.0123,
            "gravityVelocity": -0.0012,
            "battery": 78.0,
            "rssi": -62.0,
        },
        {
            "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "createdOn": "2026-09-02T09:00:00Z",
            "temperature": 21.4,
            "gravity": "not-a-number",
            "gravityVelocity": -0.001,
            "battery": 77.0,
            "rssi": -63.0,
        },
    ]

    with pytest.raises(TelemetryValidationError, match="invalid gravity"):
        parse_rapt_telemetry(
            payload,
            kind=DeviceKind.HYDROMETER,
            device_id="11111111-1111-1111-1111-111111111111",
        )


def test_rapt_source_exposes_the_same_reading_contract_for_hydrometers() -> None:
    raw = [
        {
            "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "createdOn": "2026-09-02T08:00:00Z",
            "temperature": 21.5,
            "gravity": 1.0123,
            "gravityVelocity": -0.0012,
            "battery": 78.0,
            "rssi": -62.0,
        }
    ]
    calls: list[tuple[str, datetime, datetime]] = []

    class StubRaptClient:
        def get_hydrometer_telemetry(
            self, *, device_id: str, start: datetime, end: datetime
        ) -> list[dict[str, object]]:
            calls.append((device_id, start, end))
            return raw

        def get_temperature_controller_telemetry(
            self, *, device_id: str, start: datetime, end: datetime
        ) -> list[dict[str, object]]:
            raise AssertionError("wrong device family")

    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 9, 3, tzinfo=UTC)
    source = RaptTelemetrySource(client=StubRaptClient(), kind=DeviceKind.HYDROMETER)

    readings = source.get_readings(
        device_id="11111111-1111-1111-1111-111111111111",
        start=start,
        end=end,
    )

    assert len(readings) == 1
    assert readings[0].source == "rapt"
    assert calls == [("11111111-1111-1111-1111-111111111111", start, end)]


DEVICE_ID = "11111111-1111-1111-1111-111111111111"
READING_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def controller_record(**changes: object) -> dict[str, object]:
    return {"id": READING_ID, "createdOn": "2026-09-02T08:00:00Z", **changes}


def test_controller_does_not_require_undocumented_measurements() -> None:
    (reading,) = parse_rapt_telemetry(
        [controller_record()], kind=DeviceKind.TEMPERATURE_CONTROLLER, device_id=DEVICE_ID
    )
    assert reading.temperature_c is None
    assert reading.rssi is None
    assert reading.target_temperature_c is None
    assert reading.control_temperature_c is None


@pytest.mark.parametrize("value", [True, "21", float("nan"), float("inf"), 10**400])
@pytest.mark.parametrize(
    "field", ["temperature", "rssi", "targetTemperature", "controlDeviceTemperature"]
)
def test_controller_rejects_nonfinite_or_nonnumeric_values(field: str, value: object) -> None:
    with pytest.raises(TelemetryValidationError, match="invalid"):
        parse_rapt_telemetry(
            [controller_record(**{field: value})],
            kind=DeviceKind.TEMPERATURE_CONTROLLER,
            device_id=DEVICE_ID,
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-02",
        "2026-09-02T08:00:00",
        "20260902T080000Z",
        "2026-09-02X08:00:00Z",
        "0001-01-01T00:00:00+01:00",
        None,
    ],
)
def test_rejects_invalid_or_non_rfc3339_timestamp(timestamp: object) -> None:
    with pytest.raises(TelemetryValidationError, match="timestamp"):
        parse_rapt_telemetry(
            [controller_record(createdOn=timestamp)],
            kind=DeviceKind.TEMPERATURE_CONTROLLER,
            device_id=DEVICE_ID,
        )


@pytest.mark.parametrize(
    "identifier",
    [
        None,
        12,
        "private-invalid-id",
        READING_ID.upper(),
        READING_ID.replace("-", ""),
        "{" + READING_ID + "}",
    ],
)
def test_rejects_noncanonical_reading_id(identifier: object) -> None:
    with pytest.raises(TelemetryValidationError, match="identifier"):
        parse_rapt_telemetry(
            [controller_record(id=identifier)],
            kind=DeviceKind.TEMPERATURE_CONTROLLER,
            device_id=DEVICE_ID,
        )


def test_unknown_kind_fails_closed_even_for_empty_response() -> None:
    with pytest.raises(TelemetryValidationError, match="kind"):
        parse_rapt_telemetry([], kind="unexpected", device_id=DEVICE_ID)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"createdOn": "2026-09-02T09:00:00Z"},
        {"temperature": 23},
    ],
)
def test_duplicate_reading_id_rejects_entire_response(
    changes: dict[str, object],
) -> None:
    with pytest.raises(TelemetryValidationError, match="duplicate|conflict"):
        parse_rapt_telemetry(
            [controller_record(), controller_record(**changes)],
            kind=DeviceKind.TEMPERATURE_CONTROLLER,
            device_id=DEVICE_ID,
        )


def test_equal_timestamps_with_distinct_ids_are_preserved_and_ordered_by_id() -> None:
    later_id = controller_record(id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    earlier_id = controller_record(id="11111111-1111-1111-1111-111111111111")

    readings = parse_rapt_telemetry(
        [later_id, earlier_id],
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
        device_id=DEVICE_ID,
    )

    assert [reading.reading_id for reading in readings] == [earlier_id["id"], later_id["id"]]


def test_readings_are_ordered_by_utc_time_not_input_order() -> None:
    records = [
        controller_record(),
        controller_record(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", createdOn="2026-09-02T09:00:00+02:00"
        ),
    ]
    forward = parse_rapt_telemetry(
        records, kind=DeviceKind.TEMPERATURE_CONTROLLER, device_id=DEVICE_ID
    )
    backward = parse_rapt_telemetry(
        records[::-1], kind=DeviceKind.TEMPERATURE_CONTROLLER, device_id=DEVICE_ID
    )
    assert forward == backward
    assert forward[0].reading_id == records[1]["id"]


class StubControllerClient:
    def __init__(self, payload: list[dict[str, object]]) -> None:
        self.payload = payload
        self.calls = 0

    def get_temperature_controller_telemetry(
        self, *, device_id: str, start: datetime, end: datetime
    ) -> list[dict[str, object]]:
        self.calls += 1
        return self.payload

    def get_hydrometer_telemetry(
        self, *, device_id: str, start: datetime, end: datetime
    ) -> list[dict[str, object]]:
        raise AssertionError("wrong device family")


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 9, 2), datetime(2026, 9, 3, tzinfo=UTC)),
        (datetime(2026, 9, 2, tzinfo=UTC), datetime(2026, 9, 3)),
        (datetime(2026, 9, 3, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)),
        (datetime(2026, 9, 2, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)),
    ],
)
def test_source_rejects_invalid_window_before_client_call(start: datetime, end: datetime) -> None:
    client = StubControllerClient([])
    source = RaptTelemetrySource(client=client, kind=DeviceKind.TEMPERATURE_CONTROLLER)
    with pytest.raises(TelemetryValidationError, match="interval"):
        source.get_readings(device_id=DEVICE_ID, start=start, end=end)
    assert client.calls == 0


@pytest.mark.parametrize("timestamp", ["2026-09-01T23:59:59Z", "2026-09-03T00:00:01Z"])
def test_source_rejects_readings_outside_requested_window(timestamp: str) -> None:
    source = RaptTelemetrySource(
        client=StubControllerClient([controller_record(createdOn=timestamp)]),
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
    )
    with pytest.raises(TelemetryValidationError, match="window"):
        source.get_readings(
            device_id=DEVICE_ID,
            start=datetime(2026, 9, 2, tzinfo=UTC),
            end=datetime(2026, 9, 3, tzinfo=UTC),
        )


@pytest.mark.parametrize("timestamp", ["2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z"])
def test_requested_window_includes_both_endpoints(timestamp: str) -> None:
    source = RaptTelemetrySource(
        client=StubControllerClient([controller_record(createdOn=timestamp)]),
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
    )
    assert (
        len(
            source.get_readings(
                device_id=DEVICE_ID,
                start=datetime(2026, 9, 2, tzinfo=UTC),
                end=datetime(2026, 9, 3, tzinfo=UTC),
            )
        )
        == 1
    )


def test_source_rejects_invalid_identity_before_client_call() -> None:
    client = StubControllerClient([])
    source = RaptTelemetrySource(client=client, kind=DeviceKind.TEMPERATURE_CONTROLLER)
    with pytest.raises(TelemetryValidationError):
        source.get_readings(
            device_id="private-invalid",
            start=datetime(2026, 9, 2, tzinfo=UTC),
            end=datetime(2026, 9, 3, tzinfo=UTC),
        )
    assert client.calls == 0


@pytest.mark.parametrize("gravity", [0.4, 1012.3])
def test_gravity_is_preserved_without_undocumented_unit_conversion(gravity: float) -> None:
    (reading,) = parse_rapt_telemetry(
        [
            controller_record(
                temperature=20,
                rssi=-60,
                gravity=gravity,
                gravityVelocity=3.2,
                battery=78,
            )
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=DEVICE_ID,
    )
    assert reading.gravity_raw == gravity
    assert reading.gravity_velocity_raw == 3.2


@pytest.mark.parametrize("timestamp", ["2026-09-02T08:00:00+01:60", "2026-09-02T08:00:00-00:00"])
def test_timestamp_rejects_invalid_or_unknown_offset(timestamp: str) -> None:
    with pytest.raises(TelemetryValidationError, match="timestamp"):
        parse_rapt_telemetry(
            [controller_record(createdOn=timestamp)],
            kind=DeviceKind.TEMPERATURE_CONTROLLER,
            device_id=DEVICE_ID,
        )


def test_timestamp_accepts_six_fractional_digits() -> None:
    (reading,) = parse_rapt_telemetry(
        [controller_record(createdOn="2026-09-02T08:00:00.123456Z")],
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
        device_id=DEVICE_ID,
    )

    assert reading.observed_at == datetime(2026, 9, 2, 8, microsecond=123456, tzinfo=UTC)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-08T18:08:00.7034888+00:00",
        "2026-09-08T20:08:00.7034888+02:00",
        "2026-09-08t12:38:00.7034888-05:30",
        "2026-09-08T18:08:00.7034888z",
    ],
)
def test_timestamp_preserves_seven_fractional_digits(timestamp: str) -> None:
    (reading,) = parse_rapt_telemetry(
        [controller_record(createdOn=timestamp)],
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
        device_id=DEVICE_ID,
    )
    assert reading.observed_at == datetime(2026, 9, 8, 18, 8, microsecond=703488, tzinfo=UTC)
    assert reading.observed_at_submicrosecond_ns == 800
    assert reading.observed_at_exact == "2026-09-08T18:08:00.7034888+00:00"


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-08T18:08:00.70348880Z",
        "2026-09-08T18:08:00." + "1" * 10000 + "Z",
        "2026-09-08T18:08:00.703488xZ",
        "2026-09-08T18:08:00.7034888-00:00",
        "2026-09-08T18:08:00.7034888+01:60",
        "2026-09-08T18:08:00.7034888Z\n",
        "2026-02-30T18:08:00.7034888Z",
        "0001-01-01T00:00:00.0000001+00:01",
        "9999-12-31T23:59:59.9999999-00:01",
    ],
    ids=[
        "eight",
        "unbounded",
        "non-digit",
        "unknown-offset",
        "bad-offset",
        "newline",
        "calendar",
        "utc-underflow",
        "utc-overflow",
    ],
)
def test_exact_timestamp_rejects_invalid_or_excess_precision(timestamp: str) -> None:
    with pytest.raises(TelemetryValidationError, match="timestamp"):
        parse_rapt_telemetry(
            [controller_record(createdOn=timestamp)],
            kind=DeviceKind.TEMPERATURE_CONTROLLER,
            device_id=DEVICE_ID,
        )


@pytest.mark.parametrize(
    "fraction,remainder,canonical",
    [
        ("", 0, ""),
        (".1", 0, ".100000"),
        (".123456", 0, ".123456"),
        (".1234560", 0, ".123456"),
        (".0000000", 0, ""),
        (".0000001", 100, ".0000001"),
        (".9999999", 900, ".9999999"),
    ],
)
def test_exact_timestamp_canonicalizes_without_changing_raw_measurements(
    fraction: str,
    remainder: int,
    canonical: str,
) -> None:
    (reading,) = parse_rapt_telemetry(
        [
            controller_record(
                createdOn=f"2026-09-09T00:08:00{fraction}+06:00",
                temperature=4.6875,
                gravity=1016.3,
                gravityVelocity=-0.00741505,
                battery=99.9313,
                rssi=-29,
            )
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=DEVICE_ID,
    )
    assert reading.observed_at_submicrosecond_ns == remainder
    assert reading.observed_at_exact == f"2026-09-08T18:08:00{canonical}+00:00"
    assert reading.gravity_raw == 1016.3
    assert reading.gravity_velocity_raw == -0.00741505


def test_same_microsecond_readings_sort_by_exact_instant_before_id() -> None:
    later_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    earlier_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    readings = parse_rapt_telemetry(
        [
            controller_record(id=later_id, createdOn="2026-09-08T18:08:00.7034888Z"),
            controller_record(id=earlier_id, createdOn="2026-09-08T20:08:00.7034887+02:00"),
        ],
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
        device_id=DEVICE_ID,
    )
    assert [reading.reading_id for reading in readings] == [earlier_id, later_id]
    assert readings[0].observed_at == readings[1].observed_at


@pytest.mark.parametrize(
    "fraction,accepted",
    [
        ("7034879", False),
        ("7034880", True),
        ("7034881", True),
        ("7034889", True),
        ("7034890", True),
        ("7034891", False),
    ],
)
def test_window_compares_submicroseconds(fraction: str, accepted: bool) -> None:
    source = RaptTelemetrySource(
        client=StubControllerClient(
            [controller_record(createdOn=f"2026-09-08T20:08:00.{fraction}+02:00")]
        ),
        kind=DeviceKind.TEMPERATURE_CONTROLLER,
    )
    start = datetime(2026, 9, 8, 18, 8, microsecond=703488, tzinfo=UTC)
    end = datetime(2026, 9, 8, 18, 8, microsecond=703489, tzinfo=UTC)
    if accepted:
        assert len(source.get_readings(device_id=DEVICE_ID, start=start, end=end)) == 1
    else:
        with pytest.raises(TelemetryValidationError, match="window"):
            source.get_readings(device_id=DEVICE_ID, start=start, end=end)
