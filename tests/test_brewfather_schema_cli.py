from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from forge_companion.brewfather_client import BrewfatherClient
from forge_companion.cli import app

runner = CliRunner()


def test_client_reads_one_batch_readings_endpoint_with_basic_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "time": 1720000000000,
                    "sg": 1.0123,
                    "temp": 20.1,
                    "device": "private-device-name",
                }
            ],
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = BrewfatherClient(user_id="private-user-id", api_key="private-api-key", http=http)
        assert client.get_batch_readings("batch-123") == [
            {
                "time": 1720000000000,
                "sg": 1.0123,
                "temp": 20.1,
                "device": "private-device-name",
            }
        ]

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.brewfather.app/v2/batches/batch-123/readings"
    assert request.headers["authorization"] == "Basic cHJpdmF0ZS11c2VyLWlkOnByaXZhdGUtYXBpLWtleQ=="
    assert request.headers["accept"] == "application/json"


def test_schema_command_outputs_only_count_keys_and_value_types(monkeypatch) -> None:
    class Client:
        def __init__(self, *, user_id: str, api_key: str) -> None:
            assert user_id == "private-user-id"
            assert api_key == "private-api-key"

        def __enter__(self) -> Client:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
            assert batch_id == "batch-123"
            return [
                {"time": 1720000000000, "sg": 1.0123, "temp": 20.1, "device": "private"},
                {"time": 1720003600000, "sg": 1.0118, "temp": 20.2, "device": "private"},
            ]

    from forge_companion import cli_brewfather

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=cli_brewfather.brewfather_credentials.BrewfatherProfile(
                user_id="private-user-id", api_key="private-api-key"
            ),
            source="keyring",
        ),
    )
    monkeypatch.setattr(cli_brewfather, "BrewfatherClient", Client)

    result = runner.invoke(app, ["brewfather", "readings-schema", "batch-123"])

    assert result.exit_code == 0
    assert result.output == (
        "Brewfather readings schema (read-only).\n"
        "Records: 2\n"
        "Fields: device=str, sg=float, temp=float, time=int\n"
    )
    assert "private" not in result.output
    assert "1720" not in result.output


def test_schema_command_reports_missing_profile_without_constructing_a_client(monkeypatch) -> None:
    from forge_companion import cli_brewfather

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=None, source="missing"
        ),
    )
    monkeypatch.setattr(
        cli_brewfather,
        "BrewfatherClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("client must not be constructed")),
    )

    result = runner.invoke(app, ["brewfather", "readings-schema", "batch-123"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "Brewfather request failed: run `forge-companion brewfather auth login`.\n"
    )


def test_schema_command_rejects_documentation_placeholder_before_credentials(monkeypatch) -> None:
    from forge_companion import cli_brewfather

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: pytest.fail("placeholder must fail before credentials"),
    )

    result = runner.invoke(app, ["brewfather", "readings-schema", "BATCH_ID"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr == (
        "Brewfather readings schema failed: replace BATCH_ID with an actual batch ID.\n"
    )


def test_schema_command_reports_only_a_safe_http_status_for_provider_rejection(monkeypatch) -> None:
    from forge_companion import cli_brewfather
    from forge_companion.brewfather_client import BrewfatherRequestError

    class Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
            raise BrewfatherRequestError(401)

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=cli_brewfather.brewfather_credentials.BrewfatherProfile(
                user_id="private-user-id", api_key="private-api-key"
            ),
            source="keyring",
        ),
    )
    monkeypatch.setattr(cli_brewfather, "BrewfatherClient", Client)

    result = runner.invoke(app, ["brewfather", "readings-schema", "batch-123"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Brewfather readings schema failed: request rejected (HTTP 401).\n"


def test_devices_command_lists_only_id_type_and_record_count(monkeypatch) -> None:
    from forge_companion import cli_brewfather

    class Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
            assert batch_id == "batch-123"
            return [
                {"deviceId": "pill-1", "deviceType": "Hydrometer", "name": "private"},
                {"deviceId": "pill-1", "deviceType": "Hydrometer", "name": "private"},
                {"deviceId": "controller-1", "deviceType": "Controller", "name": "private"},
            ]

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=cli_brewfather.brewfather_credentials.BrewfatherProfile(
                user_id="private-user-id", api_key="private-api-key"
            ),
            source="keyring",
        ),
    )
    monkeypatch.setattr(cli_brewfather, "BrewfatherClient", Client)

    result = runner.invoke(app, ["brewfather", "devices", "batch-123"])

    assert result.exit_code == 0
    assert result.output == (
        "Brewfather batch devices (read-only).\n"
        "device_id=controller-1 device_type=Controller records=1\n"
        "device_id=pill-1 device_type=Hydrometer records=2\n"
    )
    assert "private" not in result.output


def test_telemetry_summary_parses_one_explicit_pill_stream(monkeypatch) -> None:
    from forge_companion import cli_brewfather

    class Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
            assert batch_id == "batch-123"
            return [
                {
                    "id": "reading-1",
                    "deviceId": "pill-1",
                    "deviceType": "Hydrometer",
                    "time": 1720000000000,
                    "sg": 1.0123,
                    "temp": 20.1,
                    "rssi": -50,
                },
                {
                    "id": "reading-2",
                    "deviceId": "pill-1",
                    "deviceType": "Hydrometer",
                    "time": 1720003600000,
                    "sg": None,
                    "temp": 20.2,
                    "rssi": -49,
                },
            ]

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=cli_brewfather.brewfather_credentials.BrewfatherProfile(
                user_id="private-user-id", api_key="private-api-key"
            ),
            source="keyring",
        ),
    )
    monkeypatch.setattr(cli_brewfather, "BrewfatherClient", Client)

    result = runner.invoke(
        app,
        [
            "brewfather",
            "telemetry-summary",
            "batch-123",
            "pill-1",
            "--gravity-unit",
            "sg",
            "--temperature-unit",
            "c",
            "--time-unit",
            "epoch-ms",
        ],
    )

    assert result.exit_code == 0
    assert result.output == (
        "Brewfather Pill telemetry read-only (experimental).\n"
        "Device: pill-1\n"
        "Records: 2\n"
        "Rejected declared-SG readings: below=0 above=0\n"
        "First: 2024-07-03T09:46:40+00:00\n"
        "Latest: 2024-07-03T10:46:40+00:00\n"
        "SG: present=1 missing=1\n"
        "Temperature: present=2 missing=0\n"
    )


def test_telemetry_summary_reports_a_safe_adapter_failure_reason(monkeypatch) -> None:
    from forge_companion import cli_brewfather
    from forge_companion.telemetry import TelemetryValidationError

    class Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
            return []

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=cli_brewfather.brewfather_credentials.BrewfatherProfile(
                user_id="private-user-id", api_key="private-api-key"
            ),
            source="keyring",
        ),
    )
    monkeypatch.setattr(cli_brewfather, "BrewfatherClient", Client)
    monkeypatch.setattr(
        cli_brewfather,
        "adapt_brewfather_readings",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            TelemetryValidationError("Brewfather telemetry contains an invalid timestamp")
        ),
    )

    result = runner.invoke(
        app,
        [
            "brewfather",
            "telemetry-summary",
            "batch-123",
            "pill-1",
            "--gravity-unit",
            "sg",
            "--temperature-unit",
            "c",
            "--time-unit",
            "epoch-ms",
        ],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == (
        "Brewfather telemetry summary failed: selected stream is invalid (invalid timestamp).\n"
    )


def test_gravity_audit_reports_only_counts_and_scale_candidates(monkeypatch) -> None:
    from forge_companion import cli_brewfather

    class Client:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Client:
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            return None

        def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
            return [
                {"deviceId": "pill-1", "sg": 1012.3},
                {"deviceId": "pill-1", "sg": 1008.1},
                {"deviceId": "pill-1", "sg": None},
            ]

    monkeypatch.setattr(
        cli_brewfather.brewfather_credentials,
        "resolve_profile",
        lambda: cli_brewfather.brewfather_credentials.ResolvedBrewfatherProfile(
            profile=cli_brewfather.brewfather_credentials.BrewfatherProfile(
                user_id="private-user-id", api_key="private-api-key"
            ),
            source="keyring",
        ),
    )
    monkeypatch.setattr(cli_brewfather, "BrewfatherClient", Client)

    result = runner.invoke(app, ["brewfather", "gravity-audit", "batch-123", "pill-1"])

    assert result.exit_code == 0
    assert result.output == (
        "Brewfather gravity audit read-only.\n"
        "Records: 3\n"
        "SG: numeric=2 missing=1 invalid=0\n"
        "Scale candidates: sg=NO sg-times-1000=YES\n"
        "Range counts: sg=0/2 sg-times-1000=2/2\n"
        "SG partition: below=0 in-range=0 above=2\n"
    )
