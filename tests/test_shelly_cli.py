import httpx
import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli_module
from forge_companion.cli import app
from forge_companion.shelly import ShellyReadOnlyClient, ShellySwitchStatus

runner = CliRunner()


def test_hopper_shelly_status_reads_without_resolving_token_or_sending_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class FakeShellyReadOnlyClient:
        def __init__(self, base_url: str) -> None:
            seen["base_url"] = base_url

        def __enter__(self) -> "FakeShellyReadOnlyClient":
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            seen["closed"] = True

        def get_switch_status(self, channel: int = 0) -> ShellySwitchStatus:
            seen["channel"] = channel
            seen["status_calls"] = int(seen.get("status_calls", 0)) + 1
            return ShellySwitchStatus(
                channel=0,
                output=False,
                source="HTTP_in",
                switch_on_count=19,
                temperature_c=43.2,
            )

    def fail_if_token_is_resolved() -> None:
        raise AssertionError("BrewForge token resolution must not run")

    monkeypatch.setattr(cli_module, "ShellyReadOnlyClient", FakeShellyReadOnlyClient)
    monkeypatch.setattr(cli_module.credentials, "resolve_token", fail_if_token_is_resolved)

    result = runner.invoke(
        app,
        [
            "hopper",
            "shelly-status",
            "--device-url",
            "http://private-device-name.invalid",
            "--channel",
            "0",
        ],
    )

    assert result.exit_code == 0
    assert seen == {
        "base_url": "http://private-device-name.invalid",
        "channel": 0,
        "status_calls": 1,
        "closed": True,
    }
    assert result.output == (
        "Shelly status read-only.\n"
        "Channel: 0\n"
        "Output: OFF\n"
        "Source: HTTP_in\n"
        "Switch-on count: 19\n"
        "Temperature: 43.2 C\n"
        "No switch command was sent.\n"
    )
    assert "private-device-name" not in result.output


def test_hopper_shelly_status_error_does_not_reflect_private_device_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    class FailingShellyReadOnlyClient:
        def __init__(self, base_url: str) -> None:
            pass

        def __enter__(self) -> "FailingShellyReadOnlyClient":
            return self

        def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
            seen["closed"] = True

        def get_switch_status(self, channel: int = 0) -> ShellySwitchStatus:
            seen["status_called"] = True
            raise OSError("private-device-name and local network details")

    monkeypatch.setattr(cli_module, "ShellyReadOnlyClient", FailingShellyReadOnlyClient)

    result = runner.invoke(
        app,
        [
            "hopper",
            "shelly-status",
            "--device-url",
            "http://private-device-name.invalid",
            "--channel",
            "0",
        ],
    )

    assert result.exit_code == 1
    assert seen == {"status_called": True, "closed": True}
    assert result.output == "Shelly status failed: device, channel, or response is invalid.\n"
    assert "private-device-name" not in result.output
    assert "local network details" not in result.output


def test_hopper_shelly_status_redacts_excessively_nested_device_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_json = b"[" * 10_000 + b"0" + b"]" * 10_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))

    def client_factory(base_url: str) -> ShellyReadOnlyClient:
        return ShellyReadOnlyClient(base_url=base_url, http=http)

    monkeypatch.setattr(cli_module, "ShellyReadOnlyClient", client_factory)

    result = runner.invoke(
        app,
        [
            "hopper",
            "shelly-status",
            "--device-url",
            "http://private-device-name.invalid",
            "--channel",
            "0",
        ],
    )

    assert result.exit_code == 1
    assert result.output == "Shelly status failed: device, channel, or response is invalid.\n"
    assert "Traceback" not in result.output
    assert "private-device-name" not in result.output
