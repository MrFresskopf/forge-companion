from datetime import UTC, datetime

import pytest

from forge_companion.brewforge_telemetry import adapt_brewforge_readings
from forge_companion.telemetry import DeviceKind, TelemetryReading, TelemetryValidationError

BREW_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
READING_ID = "reading-1"


def test_adapts_observed_brewforge_reading_with_explicit_units() -> None:
    readings = adapt_brewforge_readings(
        {
            "data": [
                {
                    "id": READING_ID,
                    "timestamp": "2026-07-17T08:00:00.1234567Z",
                    "gravity": 1.0123,
                    "temperature": 21.5,
                    "pressure": None,
                    "ph": None,
                    "comment": "stored reading",
                    "futureField": "ignored for forward compatibility",
                }
            ],
            "futureEnvelopeField": None,
        },
        brew_id=BREW_ID,
        gravity_unit="sg",
        temperature_unit="c",
    )

    assert readings == (
        TelemetryReading(
            source="brewforge",
            device_kind=DeviceKind.HYDROMETER,
            device_id=BREW_ID,
            reading_id=READING_ID,
            observed_at=datetime(2026, 7, 17, 8, microsecond=123456, tzinfo=UTC),
            observed_at_submicrosecond_ns=700,
            temperature_c=21.5,
            temperature_unit="c",
            gravity_raw=1.0123,
            gravity_unit="sg",
            gravity_velocity_raw=None,
            target_temperature_c=None,
            battery_percent=None,
            rssi=None,
        ),
    )
    assert readings[0].observed_at_exact == "2026-07-17T08:00:00.1234567+00:00"


@pytest.mark.parametrize(
    ("gravity_unit", "temperature_unit"),
    [
        (None, "c"),
        ("", "c"),
        ("SG", "c"),
        ("sg-times-1000", "c"),
        ("sg", None),
        ("sg", ""),
        ("sg", "C"),
        ("sg", "f"),
        (True, "c"),
        ("sg", True),
    ],
)
def test_requires_supported_explicit_units(
    gravity_unit: object, temperature_unit: object
) -> None:
    with pytest.raises(TelemetryValidationError, match="units must be explicit"):
        adapt_brewforge_readings(
            {"data": []},
            brew_id=BREW_ID,
            gravity_unit=gravity_unit,  # type: ignore[arg-type]
            temperature_unit=temperature_unit,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"data": None}, {"data": {}}, {"data": [None]}],
)
def test_malformed_collection_or_record_fails_atomically(payload: object) -> None:
    with pytest.raises(TelemetryValidationError, match="collection|record"):
        adapt_brewforge_readings(
            payload,
            brew_id=BREW_ID,
            gravity_unit="sg",
            temperature_unit="c",
        )


@pytest.mark.parametrize(
    ("brew_id", "reading_id"),
    [
        ("not-a-uuid", READING_ID),
        (BREW_ID.upper(), READING_ID),
        (BREW_ID.replace("-", ""), READING_ID),
        (BREW_ID, None),
        (BREW_ID, 1),
        (BREW_ID, ""),
        (BREW_ID, " reading-1"),
        (BREW_ID, "reading-1\n"),
        (BREW_ID, "read\x00ing"),
    ],
)
def test_requires_canonical_brew_and_source_reading_id(
    brew_id: object, reading_id: object
) -> None:
    with pytest.raises(TelemetryValidationError, match="identifier"):
        adapt_brewforge_readings(
            {
                "data": [
                    {
                        "id": reading_id,
                        "timestamp": "2026-07-17T08:00:00Z",
                        "gravity": None,
                        "temperature": None,
                    }
                ]
            },
            brew_id=brew_id,  # type: ignore[arg-type]
            gravity_unit="sg",
            temperature_unit="c",
        )


def test_sorts_by_exact_utc_instant() -> None:
    records = [
        {
            "id": "reading-b",
            "timestamp": "2026-07-17T10:00:00.1234566+02:00",
            "gravity": None,
            "temperature": None,
        },
        {
            "id": "reading-c",
            "timestamp": "2026-07-17T08:00:00.1234568Z",
            "gravity": None,
            "temperature": None,
        },
        {
            "id": "reading-a",
            "timestamp": "2026-07-17T08:00:00.1234567Z",
            "gravity": None,
            "temperature": None,
        },
    ]

    readings = adapt_brewforge_readings(
        {"data": records},
        brew_id=BREW_ID,
        gravity_unit="sg",
        temperature_unit="c",
    )

    assert [reading.reading_id for reading in readings] == [
        "reading-b",
        "reading-a",
        "reading-c",
    ]


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        1,
        "2026-07-17",
        "2026-07-17T08:00:00",
        "2026-07-17T08:00:00.12345678Z",
        "2026-07-17T08:00:00-00:00",
        "0001-01-01T00:00:00+01:00",
    ],
)
def test_invalid_timestamp_is_an_atomic_source_specific_failure(timestamp: object) -> None:
    with pytest.raises(
        TelemetryValidationError,
        match=r"^BrewForge telemetry contains an invalid timestamp$",
    ):
        adapt_brewforge_readings(
            {
                "data": [
                    {
                        "id": READING_ID,
                        "timestamp": timestamp,
                        "gravity": None,
                        "temperature": None,
                    }
                ]
            },
            brew_id=BREW_ID,
            gravity_unit="sg",
            temperature_unit="c",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("gravity", True),
        ("gravity", "1.012"),
        ("gravity", float("nan")),
        ("gravity", float("inf")),
        ("gravity", 10**400),
        ("gravity", 0.8999),
        ("gravity", 1.2001),
        ("temperature", False),
        ("temperature", "21"),
        ("temperature", float("nan")),
        ("temperature", float("inf")),
        ("temperature", 10**400),
        ("temperature", -273.1501),
    ],
)
def test_invalid_or_out_of_domain_measurement_fails_atomically(field: str, value: object) -> None:
    record: dict[str, object] = {
        "id": READING_ID,
        "timestamp": "2026-07-17T08:00:00Z",
        "gravity": None,
        "temperature": None,
    }
    record[field] = value
    with pytest.raises(
        TelemetryValidationError,
        match=rf"^BrewForge telemetry contains an invalid {field}$",
    ):
        adapt_brewforge_readings(
            {"data": [record]},
            brew_id=BREW_ID,
            gravity_unit="sg",
            temperature_unit="c",
        )


@pytest.mark.parametrize(
    "second",
    [
        {
            "id": READING_ID,
            "timestamp": "2026-07-17T09:00:00Z",
            "gravity": 1.011,
            "temperature": 21.0,
        },
        {
            "id": "reading-2",
            "timestamp": "2026-07-17T10:00:00+02:00",
            "gravity": 1.012,
            "temperature": 21.5,
        },
        {
            "id": "reading-2",
            "timestamp": "2026-07-17T08:00:00Z",
            "gravity": 1.011,
            "temperature": 21.5,
        },
    ],
)
def test_duplicate_or_conflicting_identity_or_instant_fails_atomically(
    second: dict[str, object],
) -> None:
    first = {
        "id": READING_ID,
        "timestamp": "2026-07-17T08:00:00Z",
        "gravity": 1.012,
        "temperature": 21.5,
    }
    with pytest.raises(TelemetryValidationError, match="duplicate or conflicting"):
        adapt_brewforge_readings(
            {"data": [first, second]},
            brew_id=BREW_ID,
            gravity_unit="sg",
            temperature_unit="c",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pressure", True),
        ("pressure", float("inf")),
        ("ph", "4.2"),
        ("ph", float("nan")),
        ("comment", 1),
    ],
)
def test_malformed_observed_brewforge_fields_fail_even_when_not_adapted(
    field: str, value: object
) -> None:
    record: dict[str, object] = {
        "id": READING_ID,
        "timestamp": "2026-07-17T08:00:00Z",
        "gravity": None,
        "temperature": None,
    }
    record[field] = value
    with pytest.raises(TelemetryValidationError, match=field):
        adapt_brewforge_readings(
            {"data": [record]},
            brew_id=BREW_ID,
            gravity_unit="sg",
            temperature_unit="c",
        )
