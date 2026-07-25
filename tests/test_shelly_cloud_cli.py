import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli_module
from forge_companion.cli import app
from forge_companion.shelly_cloud import ShellyCloudSwitchStatus
from forge_companion.shelly_cloud_credentials import ResolvedCloudProfile, ShellyCloudProfile

runner = CliRunner()


def test_hopper_cloud_status_uses_stored_profile_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_server = "shelly-private-eu.shelly.cloud"
    private_device = "aabbccddeeff"
    private_key = "private-cloud-key"
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "resolve_profile",
        lambda: ResolvedCloudProfile(
            profile=ShellyCloudProfile(private_server, private_device, private_key),
            source="keyring",
        ),
    )

    class FakeCloudClient:
        def __init__(self, *, server: str, device_id: str, auth_key: str) -> None:
            seen.update(server=server, device_id=device_id, auth_key=auth_key)

        def __enter__(self) -> "FakeCloudClient":
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            seen["closed"] = True

        def get_switch_status(self, channel: int = 0) -> ShellyCloudSwitchStatus:
            seen["channel"] = channel
            seen["calls"] = int(seen.get("calls", 0)) + 1
            return ShellyCloudSwitchStatus(
                device_id=private_device,
                channel=0,
                online=True,
                output=False,
                source="init",
            )

    monkeypatch.setattr(cli_module, "ShellyCloudReadOnlyClient", FakeCloudClient)

    result = runner.invoke(app, ["hopper", "cloud-status"])

    assert result.exit_code == 0
    assert seen == {
        "server": private_server,
        "device_id": private_device,
        "auth_key": private_key,
        "channel": 0,
        "calls": 1,
        "closed": True,
    }
    assert result.output == (
        "Shelly Cloud status read-only.\n"
        "Online: YES\n"
        "Channel: 0\n"
        "Output: OFF\n"
        "Source: init\n"
        "No switch command was sent.\n"
    )
    assert private_server not in result.output
    assert private_device not in result.output
    assert private_key not in result.output


def test_hopper_cloud_auth_login_stores_profile_without_echoing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "store_profile",
        lambda *, server, device_id, auth_key: seen.update(
            server=server,
            device_id=device_id,
            auth_key=auth_key,
        ),
    )

    result = runner.invoke(
        app,
        ["hopper", "cloud-auth", "login"],
        input=("shelly-82-eu.shelly.cloud\naabbccddeeff\nprivate-cloud-key\nprivate-cloud-key\n"),
    )

    assert result.exit_code == 0
    assert seen == {
        "server": "shelly-82-eu.shelly.cloud",
        "device_id": "aabbccddeeff",
        "auth_key": "private-cloud-key",
    }
    assert result.output.endswith(
        "Shelly Cloud profile stored in the native OS credential store.\n"
    )
    assert "private-cloud-key" not in result.output


def test_hopper_cloud_auth_status_reports_only_configuration_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_values = ShellyCloudProfile(
        "shelly-private-eu.shelly.cloud",
        "aabbccddeeff",
        "private-cloud-key",
    )
    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "resolve_profile",
        lambda: ResolvedCloudProfile(profile=private_values, source="keyring"),
    )

    result = runner.invoke(app, ["hopper", "cloud-auth", "status"])

    assert result.exit_code == 0
    assert (
        result.output == "Shelly Cloud profile is configured in the native OS credential store.\n"
    )
    assert private_values.server not in result.output
    assert private_values.device_id not in result.output
    assert private_values.auth_key not in result.output


def test_hopper_cloud_auth_logout_deletes_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def delete_profile() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "delete_profile",
        delete_profile,
    )

    result = runner.invoke(app, ["hopper", "cloud-auth", "logout"])

    assert result.exit_code == 0
    assert calls == 1
    assert result.output == "Shelly Cloud profile removed from the native OS credential store.\n"


def test_hopper_cloud_status_requires_stored_profile_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "resolve_profile",
        lambda: ResolvedCloudProfile(profile=None, source="missing"),
    )
    monkeypatch.setattr(
        cli_module,
        "ShellyCloudReadOnlyClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("client must not be created")),
    )

    result = runner.invoke(app, ["hopper", "cloud-status"])

    assert result.exit_code == 2
    assert result.output == (
        "Shelly Cloud status failed: run `forge-companion hopper cloud-auth login`.\n"
    )


def test_hopper_cloud_status_reports_offline_output_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = ShellyCloudProfile(
        "shelly-82-eu.shelly.cloud",
        "aabbccddeeff",
        "private-cloud-key",
    )
    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "resolve_profile",
        lambda: ResolvedCloudProfile(profile=profile, source="keyring"),
    )

    class OfflineClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "OfflineClient":
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> ShellyCloudSwitchStatus:
            return ShellyCloudSwitchStatus(
                device_id=profile.device_id,
                channel=channel,
                online=False,
                output=None,
                source=None,
            )

    monkeypatch.setattr(cli_module, "ShellyCloudReadOnlyClient", OfflineClient)

    result = runner.invoke(app, ["hopper", "cloud-status"])

    assert result.exit_code == 0
    assert result.output == (
        "Shelly Cloud status read-only.\n"
        "Online: NO\n"
        "Channel: 0\n"
        "Output: UNKNOWN\n"
        "No switch command was sent.\n"
    )


def test_hopper_cloud_status_sanitizes_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = "private-cloud-key"
    profile = ShellyCloudProfile(
        "shelly-82-eu.shelly.cloud",
        "aabbccddeeff",
        private_key,
    )
    monkeypatch.setattr(
        cli_module.shelly_cloud_credentials,
        "resolve_profile",
        lambda: ResolvedCloudProfile(profile=profile, source="keyring"),
    )

    class FailingClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FailingClient":
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> ShellyCloudSwitchStatus:
            request = cli_module.httpx.Request(
                "POST",
                f"https://shelly-82-eu.shelly.cloud/v2/devices/api/get?auth_key={private_key}",
            )
            raise cli_module.httpx.ConnectError("private backend details", request=request)

    monkeypatch.setattr(cli_module, "ShellyCloudReadOnlyClient", FailingClient)

    result = runner.invoke(app, ["hopper", "cloud-status"])

    assert result.exit_code == 1
    assert result.output == "Shelly Cloud status failed: request or response is invalid.\n"
    assert private_key not in result.output
    assert "private backend details" not in result.output
