import pytest
from typer.testing import CliRunner

import forge_companion.cli_rapt as cli_module
from forge_companion.cli import app

runner = CliRunner()


def test_rapt_auth_login_stores_profile_without_echoing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "store_profile",
        lambda *, username, api_secret: seen.update(
            username=username,
            api_secret=api_secret,
        ),
    )

    result = runner.invoke(
        app,
        ["rapt", "auth", "login"],
        input="brewer@example.com\nprivate-api-secret\nprivate-api-secret\n",
    )

    assert result.exit_code == 0
    assert seen == {
        "username": "brewer@example.com",
        "api_secret": "private-api-secret",
    }
    assert result.output.endswith("RAPT profile stored in the native OS credential store.\n")
    assert "private-api-secret" not in result.output


def test_rapt_auth_status_reports_only_configuration_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_profile = cli_module.rapt_credentials.RaptProfile(
        username="brewer@example.com",
        api_secret="private-api-secret",
    )
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(
            profile=private_profile,
            source="keyring",
        ),
    )

    result = runner.invoke(app, ["rapt", "auth", "status"])

    assert result.exit_code == 0
    assert result.output == "RAPT profile is configured in the native OS credential store.\n"
    assert private_profile.username not in result.output
    assert private_profile.api_secret not in result.output


def test_rapt_auth_logout_deletes_only_the_stored_rapt_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def delete_profile() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(cli_module.rapt_credentials, "delete_profile", delete_profile)

    result = runner.invoke(app, ["rapt", "auth", "logout"])

    assert result.exit_code == 0
    assert calls == 1
    assert result.output == "Stored RAPT profile deleted.\n"


def test_rapt_devices_lists_both_supported_device_types_without_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_profile = cli_module.rapt_credentials.RaptProfile(
        username="brewer@example.com",
        api_secret="private-api-secret",
    )
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(
            profile=private_profile,
            source="keyring",
        ),
    )

    class FakeRaptClient:
        def __init__(self, *, username: str, api_secret: str) -> None:
            assert username == private_profile.username
            assert api_secret == private_profile.api_secret

        def __enter__(self) -> "FakeRaptClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def list_hydrometers(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "name": "Pill\u001b[31m red | tank",
                }
            ]

        def list_temperature_controllers(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "name": "Fermentation fridge",
                }
            ]

    monkeypatch.setattr(cli_module, "RaptClient", FakeRaptClient)

    result = runner.invoke(app, ["rapt", "devices"])

    assert result.exit_code == 0
    assert result.output == (
        "RAPT devices read-only.\n"
        "HYDROMETER 11111111-1111-1111-1111-111111111111 Pill red \\| tank\n"
        "TEMPERATURE_CONTROLLER 22222222-2222-2222-2222-222222222222 "
        "Fermentation fridge\n"
        "No RAPT or Shelly device command was sent.\n"
    )
    assert private_profile.username not in result.output
    assert private_profile.api_secret not in result.output


def test_rapt_telemetry_reads_and_normalizes_a_hydrometer_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_profile = cli_module.rapt_credentials.RaptProfile(
        username="brewer@example.com",
        api_secret="private-api-secret",
    )
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(
            profile=private_profile,
            source="keyring",
        ),
    )

    class FakeRaptClient:
        def __init__(self, *, username: str, api_secret: str) -> None:
            assert username == private_profile.username
            assert api_secret == private_profile.api_secret

        def __enter__(self) -> "FakeRaptClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get_hydrometer_telemetry(self, **kwargs: object) -> list[dict[str, object]]:
            assert kwargs == {
                "device_id": "11111111-1111-1111-1111-111111111111",
                "start": cli_module.datetime(2026, 9, 1, 8, 0, tzinfo=cli_module.UTC),
                "end": cli_module.datetime(2026, 9, 1, 9, 0, tzinfo=cli_module.UTC),
            }
            return [
                {
                    "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "createdOn": "2026-09-01T08:30:00Z",
                    "temperature": 18.4,
                    "gravity": 1.042,
                    "gravityVelocity": -0.0015,
                    "battery": 84,
                    "rssi": -61,
                }
            ]

    monkeypatch.setattr(cli_module, "RaptClient", FakeRaptClient)

    result = runner.invoke(
        app,
        [
            "rapt",
            "telemetry",
            "hydrometer",
            "11111111-1111-1111-1111-111111111111",
            "--start",
            "2026-09-01T08:00:00Z",
            "--end",
            "2026-09-01T09:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert result.output == (
        "RAPT telemetry read-only.\n"
        "Device: HYDROMETER 11111111-1111-1111-1111-111111111111\n"
        "Window: 2026-09-01T08:00:00+00:00 / 2026-09-01T09:00:00+00:00\n"
        "Readings: 1\n"
        "2026-09-01T08:30:00+00:00 temperature=18.4 C gravity_raw=1.0420 "
        "battery=84.0% rssi=-61.0\n"
        "No RAPT or Shelly device command was sent.\n"
    )


def test_rapt_telemetry_reads_temperature_controller_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_profile = cli_module.rapt_credentials.RaptProfile(
        username="brewer@example.com",
        api_secret="private-api-secret",
    )
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(
            profile=private_profile,
            source="keyring",
        ),
    )

    class FakeRaptClient:
        def __init__(self, *, username: str, api_secret: str) -> None:
            pass

        def __enter__(self) -> "FakeRaptClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get_temperature_controller_telemetry(self, **kwargs: object) -> list[dict[str, object]]:
            return [
                {
                    "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                    "createdOn": "2026-09-01T08:30:00Z",
                    "temperature": 18.4,
                    "targetTemperature": 19.0,
                    "controlDeviceTemperature": 18.1,
                    "rssi": -55,
                }
            ]

    monkeypatch.setattr(cli_module, "RaptClient", FakeRaptClient)

    result = runner.invoke(
        app,
        [
            "rapt",
            "telemetry",
            "temperature-controller",
            "22222222-2222-2222-2222-222222222222",
            "--start",
            "2026-09-01T08:00:00Z",
            "--end",
            "2026-09-01T09:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert "Device: TEMPERATURE_CONTROLLER 22222222-2222-2222-2222-222222222222\n" in (
        result.output
    )
    assert (
        "2026-09-01T08:30:00+00:00 temperature=18.4 C target=19.0 C control=18.1 C rssi=-55.0\n"
    ) in result.output
    assert result.output.endswith("No RAPT or Shelly device command was sent.\n")


@pytest.mark.parametrize(
    "command",
    [
        ["rapt", "devices"],
        [
            "rapt",
            "telemetry",
            "hydrometer",
            "11111111-1111-1111-1111-111111111111",
            "--start",
            "2026-09-01T08:00:00Z",
            "--end",
            "2026-09-01T09:00:00Z",
        ],
    ],
)
def test_domain_failures_are_private_cli_errors(monkeypatch, command):
    from forge_companion.rapt import RaptAuthenticationError

    profile = cli_module.rapt_credentials.RaptProfile(
        username="test@example.com", api_secret="synthetic"
    )
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(profile=profile, source="keyring"),
    )

    def fail(**kwargs):
        raise RaptAuthenticationError("private upstream detail")

    monkeypatch.setattr(cli_module, "RaptClient", fail)
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "failed:" in result.stderr
    assert "private upstream detail" not in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "kind,device,start,end",
    [
        (
            "unknown",
            "11111111-1111-1111-1111-111111111111",
            "2026-09-01T08:00:00Z",
            "2026-09-01T09:00:00Z",
        ),
        ("hydrometer", "bad-id", "2026-09-01T08:00:00Z", "2026-09-01T09:00:00Z"),
        (
            "hydrometer",
            "11111111-1111-1111-1111-111111111111",
            "2026-09-01T08:00:00",
            "2026-09-01T09:00:00Z",
        ),
        (
            "hydrometer",
            "11111111-1111-1111-1111-111111111111",
            "2026-09-01T09:00:00Z",
            "2026-09-01T08:00:00Z",
        ),
    ],
)
def test_invalid_telemetry_input_precedes_credentials(monkeypatch, kind, device, start, end):
    def forbidden():
        pytest.fail("Credential access before local validation")

    monkeypatch.setattr(cli_module.rapt_credentials, "resolve_profile", forbidden)
    result = runner.invoke(app, ["rapt", "telemetry", kind, device, "--start", start, "--end", end])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "failed:" in result.stderr
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize(
    "command,code",
    [
        (["rapt", "auth", "status"], 1),
        (["rapt", "devices"], 2),
        (
            [
                "rapt",
                "telemetry",
                "hydrometer",
                "11111111-1111-1111-1111-111111111111",
                "--start",
                "2026-09-01T08:00:00Z",
                "--end",
                "2026-09-01T09:00:00Z",
            ],
            2,
        ),
    ],
)
def test_missing_rapt_profile_exit_streams(monkeypatch, command, code):
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(profile=None, source="missing"),
    )
    result = runner.invoke(app, command)
    assert result.exit_code == code
    assert result.stdout == ""
    assert result.stderr


def test_logout_absent_is_success(monkeypatch):
    monkeypatch.setattr(cli_module.rapt_credentials, "delete_profile", lambda: False)
    result = runner.invoke(app, ["rapt", "auth", "logout"])
    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout == "No stored RAPT profile was present.\n"


@pytest.mark.parametrize(
    "command,method",
    [
        (["rapt", "auth", "status"], "resolve_profile"),
        (["rapt", "auth", "logout"], "delete_profile"),
        (["rapt", "devices"], "resolve_profile"),
    ],
)
def test_keyring_failures_have_private_stderr(monkeypatch, command, method):
    def fail():
        raise cli_module.rapt_credentials.RaptCredentialError("private backend detail")

    monkeypatch.setattr(cli_module.rapt_credentials, method, fail)
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "credential store access failed" in result.stderr
    assert "private backend detail" not in result.output


@pytest.mark.parametrize(
    "command",
    [
        ["rapt", "telemetry"],
        ["rapt", "telemetry", "hydrometer", "11111111-1111-1111-1111-111111111111"],
    ],
)
def test_parser_failure_has_stderr_without_credentials(monkeypatch, command):
    def forbidden():
        pytest.fail("Parser failure must not access credentials")

    monkeypatch.setattr(cli_module.rapt_credentials, "resolve_profile", forbidden)
    result = runner.invoke(app, command, color=True)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr


def test_invalid_device_list_does_not_print_partial_success(monkeypatch):
    profile = cli_module.rapt_credentials.RaptProfile(
        username="test@example.com", api_secret="synthetic"
    )
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(profile=profile, source="keyring"),
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_hydrometers(self):
            return [{"id": "11111111-1111-1111-1111-111111111111", "name": "valid pill"}]

        def list_temperature_controllers(self):
            return [{"id": "invalid-private-identifier", "name": "private"}]

    monkeypatch.setattr(cli_module, "RaptClient", Client)
    result = runner.invoke(app, ["rapt", "devices"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "failed:" in result.stderr
    assert "private" not in result.output


def test_controller_line_preserves_absent_measurements():
    from forge_companion.telemetry import parse_rapt_telemetry

    (reading,) = parse_rapt_telemetry(
        [
            {
                "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "createdOn": "2026-09-01T08:30:00Z",
                "targetTemperature": 19,
            }
        ],
        kind=cli_module.DeviceKind.TEMPERATURE_CONTROLLER,
        device_id="22222222-2222-2222-2222-222222222222",
    )
    assert cli_module._reading_line(reading) == "2026-09-01T08:30:00+00:00 target=19.0 C"
