from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from typer.testing import CliRunner

from forge_companion.cli import app
from forge_companion.hopper import (
    HopperStatus,
    arm_hopper_plan,
    create_hopper_plan,
    hopper_plan_lock,
    load_hopper_plan,
    simulate_hopper_plan,
    validate_hopper_plan,
    write_hopper_plan,
)

runner = CliRunner()


def test_hopper_without_subcommand_shows_available_commands() -> None:
    result = runner.invoke(app, ["hopper"])

    assert result.exit_code == 0
    assert "plan" in result.output
    assert "status" in result.output
    assert "fire" in result.output
    assert "Missing command" not in result.output


def test_cloud_auth_without_subcommand_shows_available_commands() -> None:
    result = runner.invoke(app, ["hopper", "cloud-auth"])

    assert result.exit_code == 0
    assert "login" in result.output
    assert "status" in result.output
    assert "logout" in result.output
    assert "Missing command" not in result.output


def _patch_cloud_preflight_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from forge_companion import shelly_cloud, shelly_cloud_credentials

    class FakeReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            return shelly_cloud.ShellyCloudSwitchStatus(
                device_id="5432046e5f58",
                channel=channel,
                online=True,
                output=False,
                source="timer",
            )

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", FakeReadOnlyClient)
    monkeypatch.setattr("forge_companion.cli.sleep_seconds", lambda seconds: None)
    monkeypatch.setattr("forge_companion.cli._is_interactive_terminal", lambda: True)
    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )


def test_hopper_plan_command_creates_offline_draft(tmp_path: Path) -> None:
    destination = tmp_path / "private-hopper-name.json"

    result = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--trigger-at",
            "2099-01-01T18:00:00+00:00",
            "--pulse-ms",
            "1500",
            "--brew-id",
            "fce879bf-bf02-437a-ad7c-4cbaa4aaf881",
            "--output",
            str(destination),
        ],
        env={"BREWFORGE_API_TOKEN": "must-not-be-used"},
    )

    assert result.exit_code == 0
    assert result.output == (
        "Hopper simulation plan written.\nStatus: DRAFT\nNo device or network was contacted.\n"
    )
    assert "private-hopper-name" not in result.output
    summary = validate_hopper_plan(load_hopper_plan(destination))
    assert summary.status is HopperStatus.DRAFT


def test_hopper_plan_error_does_not_reflect_invalid_trigger_text(tmp_path: Path) -> None:
    destination = tmp_path / "must-not-exist.json"

    result = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--trigger-at",
            "private brew schedule",
            "--pulse-ms",
            "1500",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper plan failed: trigger, pulse, or brew UUID is invalid.\n"
    assert "private brew schedule" not in result.output
    assert not destination.exists()


def test_hopper_plan_error_does_not_reflect_invalid_pulse_text(tmp_path: Path) -> None:
    destination = tmp_path / "must-not-exist.json"

    result = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--trigger-at",
            "2099-01-01T18:00:00+00:00",
            "--pulse-ms",
            "private pulse value",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper plan failed: trigger, pulse, or brew UUID is invalid.\n"
    assert "private pulse value" not in result.output
    assert not destination.exists()


def test_hopper_plan_refuses_to_overwrite_existing_file_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "existing-private-plan.json"
    destination.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(Path, "exists", lambda _path: False)

    result = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--trigger-at",
            "2099-01-01T18:00:00+00:00",
            "--pulse-ms",
            "1500",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper plan failed: destination already exists.\n"
    assert destination.read_text(encoding="utf-8") == "keep me"
    assert "existing-private-plan" not in result.output


def test_hopper_plan_timezone_overflow_fails_with_generic_error(tmp_path: Path) -> None:
    destination = tmp_path / "must-not-exist.json"

    result = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--trigger-at",
            "0001-01-01T00:00:00+14:00",
            "--pulse-ms",
            "1500",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper plan failed: trigger, pulse, or brew UUID is invalid.\n"
    assert not destination.exists()


def test_hopper_arm_command_persists_explicit_armed_state(tmp_path: Path) -> None:
    destination = tmp_path / "hopper-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1500,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        plan_id=UUID("4f18737c-102f-4f52-a0c3-69aa2c3f7281"),
    )
    write_hopper_plan(payload, destination)

    result = runner.invoke(app, ["hopper", "arm", str(destination)])

    assert result.exit_code == 0
    assert result.output == (
        "Hopper simulation plan armed.\nStatus: ARMED\nNo device or network was contacted.\n"
    )
    assert validate_hopper_plan(load_hopper_plan(destination)).status is HopperStatus.ARMED


def test_hopper_simulate_command_completes_lifecycle_without_hardware(tmp_path: Path) -> None:
    destination = tmp_path / "hopper-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1500,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)

    result = runner.invoke(
        app,
        ["hopper", "simulate", str(destination), "--at", "2099-01-01T18:01:00+00:00"],
    )

    assert result.exit_code == 0
    assert result.output == (
        "Hopper simulation completed.\n"
        "Status: LOCKED\n"
        "No device or network was contacted; no physical pulse was sent.\n"
    )
    assert validate_hopper_plan(load_hopper_plan(destination)).status is HopperStatus.LOCKED


def test_hopper_simulate_before_trigger_fails_without_changing_armed_plan(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "hopper-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1500,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)
    before = destination.read_bytes()

    result = runner.invoke(
        app,
        ["hopper", "simulate", str(destination), "--at", "2099-01-01T17:59:59+00:00"],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper simulation failed: plan is invalid, early, or not armed.\n"
    assert destination.read_bytes() == before


def test_hopper_simulate_datetime_overflow_fails_closed(tmp_path: Path) -> None:
    destination = tmp_path / "hopper-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=60_000,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)
    before = destination.read_bytes()

    result = runner.invoke(
        app,
        ["hopper", "simulate", str(destination), "--at", "9999-12-31T23:59:59+00:00"],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper simulation failed: plan is invalid, early, or not armed.\n"
    assert destination.read_bytes() == before


def test_hopper_simulate_timezone_normalization_overflow_fails_closed(tmp_path: Path) -> None:
    destination = tmp_path / "hopper-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1500,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)
    before = destination.read_bytes()

    result = runner.invoke(
        app,
        ["hopper", "simulate", str(destination), "--at", "9999-12-31T23:59:59-14:00"],
    )

    assert result.exit_code == 1
    assert result.output == "Hopper simulation failed: plan is invalid, early, or not armed.\n"
    assert destination.read_bytes() == before


def test_hopper_simulate_fails_closed_while_plan_is_locked(tmp_path: Path) -> None:
    destination = tmp_path / "hopper-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1500,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)
    before = destination.read_bytes()

    with hopper_plan_lock(destination):
        result = runner.invoke(
            app,
            ["hopper", "simulate", str(destination), "--at", "2099-01-01T18:01:00+00:00"],
        )

    assert result.exit_code == 1
    assert result.output == "Hopper simulation failed: plan is busy or locked.\n"
    assert destination.read_bytes() == before


def test_hopper_status_reports_safe_validated_summary(tmp_path: Path) -> None:
    destination = tmp_path / "private-brew-hopper.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1500,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 7, 22, 13, 0, tzinfo=UTC))
    completed = simulate_hopper_plan(armed, at=datetime(2099, 1, 1, 18, 1, tzinfo=UTC))
    write_hopper_plan(completed, destination)

    result = runner.invoke(app, ["hopper", "status", str(destination)])

    assert result.exit_code == 0
    assert result.output == (
        "Hopper simulation plan is valid.\n"
        "Status: LOCKED\n"
        "Trigger: 2099-01-01T18:00:00+00:00\n"
        "Pulse: 1500 ms (simulation only)\n"
        "No device or network was contacted.\n"
    )
    assert "private-brew-hopper" not in result.output


def test_hopper_status_hides_invalid_path_and_content(tmp_path: Path) -> None:
    source = tmp_path / "private-brew-hopper.json"
    source.write_text('{"comment":"secret dry hop"', encoding="utf-8")

    result = runner.invoke(app, ["hopper", "status", str(source)])

    assert result.exit_code == 1
    assert result.output == "Hopper status failed: plan is invalid or unreadable.\n"
    assert "private-brew-hopper" not in result.output
    assert "secret dry hop" not in result.output


def test_hopper_plan_cloud_uses_stored_profile_without_persisting_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forge_companion import shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )
    destination = tmp_path / "cloud-plan.json"

    result = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--cloud",
            "--trigger-at",
            "2099-01-01T18:00:00+00:00",
            "--pulse-ms",
            "1000",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert result.output == (
        "Hopper Cloud one-shot plan written.\nStatus: DRAFT\nNo device or network was contacted.\n"
    )
    raw = destination.read_text(encoding="utf-8")
    assert "synthetic-secret-key" not in raw
    payload = load_hopper_plan(destination)
    assert payload["action"]["kind"] == "cloud-pulse"


def _write_armed_cloud_plan(destination: Path) -> None:
    payload = create_hopper_plan(
        trigger_at=datetime(2026, 1, 2, 0, 0, tzinfo=UTC),
        pulse_duration_ms=100,
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 1, 1, 1, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)


def test_hopper_check_reports_read_only_cloud_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "private-cloud-plan.json"
    _write_armed_cloud_plan(destination)
    before = destination.read_bytes()

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )
    request_count = 0

    class FakeReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs == {
                "server": "shelly-82-eu.shelly.cloud",
                "device_id": "5432046e5f58",
                "auth_key": "synthetic-secret-key",
            }

        def __enter__(self) -> "FakeReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            nonlocal request_count
            request_count += 1
            return shelly_cloud.ShellyCloudSwitchStatus(
                device_id="5432046e5f58",
                channel=channel,
                online=True,
                output=False,
                source="timer",
            )

    class ForbiddenActuator:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("read-only check must not construct an actuator")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", FakeReadOnlyClient)
    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", ForbiddenActuator)

    result = runner.invoke(app, ["hopper", "check", str(destination)])

    assert result.exit_code == 0
    assert result.output == (
        "Hopper Cloud one-shot readiness check passed.\n"
        "Status: ARMED\n"
        "Trigger: reached\n"
        "Pulse: 100 ms\n"
        "Credential target: matched\n"
        "Electrical preflight: ONLINE / OFF\n"
        "Mechanical release: NOT VERIFIED\n"
        "No switch command was sent and the plan was not changed.\n"
    )
    assert request_count == 1
    assert destination.read_bytes() == before
    assert "synthetic-secret-key" not in result.output
    assert "private-cloud-plan" not in result.output


def test_hopper_check_rejects_early_plan_before_credentials_or_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "early-cloud-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=100,
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
    )
    armed = arm_hopper_plan(payload, at=datetime(2026, 1, 1, 1, 0, tzinfo=UTC))
    write_hopper_plan(armed, destination)
    before = destination.read_bytes()

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    credential_calls = 0

    def forbidden_profile() -> object:
        nonlocal credential_calls
        credential_calls += 1
        raise AssertionError("early plan must fail before credentials")

    monkeypatch.setattr(shelly_cloud_credentials, "resolve_profile", forbidden_profile)
    monkeypatch.setattr(
        shelly_cloud,
        "ShellyCloudReadOnlyClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network must not be contacted")),
    )

    result = runner.invoke(app, ["hopper", "check", str(destination)])

    assert result.exit_code == 1
    assert result.output == "Hopper check failed: plan, trigger, or Cloud profile is not ready.\n"
    assert credential_calls == 0
    assert destination.read_bytes() == before


def test_hopper_check_rejects_profile_mismatch_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)
    before = destination.read_bytes()

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    mismatched = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="aaaaaaaaaaaa",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(
            profile=mismatched,
            source="keyring",
        ),
    )
    monkeypatch.setattr(
        shelly_cloud,
        "ShellyCloudReadOnlyClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network must not be contacted")),
    )

    result = runner.invoke(app, ["hopper", "check", str(destination)])

    assert result.exit_code == 1
    assert result.output == "Hopper check failed: plan, trigger, or Cloud profile is not ready.\n"
    assert destination.read_bytes() == before
    assert "aaaaaaaaaaaa" not in result.output


@pytest.mark.parametrize(
    ("online", "output"),
    [(True, True), (False, False), (True, None)],
)
def test_hopper_check_requires_online_explicit_off_without_actuator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    online: bool,
    output: bool | None,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)
    before = destination.read_bytes()

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )
    request_count = 0

    class UnsafeReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "UnsafeReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            nonlocal request_count
            request_count += 1
            return shelly_cloud.ShellyCloudSwitchStatus(
                device_id="5432046e5f58",
                channel=channel,
                online=online,
                output=output,
                source="HTTP",
            )

    class ForbiddenActuator:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("read-only check must not construct an actuator")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", UnsafeReadOnlyClient)
    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", ForbiddenActuator)

    result = runner.invoke(app, ["hopper", "check", str(destination)])

    assert result.exit_code == 1
    assert result.output == (
        "Hopper check failed: electrical preflight requires an online device reporting OFF.\n"
    )
    assert request_count == 1
    assert destination.read_bytes() == before


@pytest.mark.parametrize("failure_kind", ["transport", "response"])
def test_hopper_check_sanitizes_cloud_status_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)
    before = destination.read_bytes()

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    secret = "synthetic-secret-key"
    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key=secret,
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )

    class FailingReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FailingReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            if failure_kind == "response":
                raise shelly_cloud.ShellyCloudResponseError(f"provider reflected {secret}\x1b[31m")
            raise httpx.RequestError(f"provider reflected {secret}\x1b[31m")

    class ForbiddenActuator:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("read-only check must not construct an actuator")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", FailingReadOnlyClient)
    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", ForbiddenActuator)

    result = runner.invoke(app, ["hopper", "check", str(destination)])

    assert result.exit_code == 1
    assert result.output == "Hopper check failed: Cloud status request failed.\n"
    assert secret not in result.output
    assert "\x1b" not in result.output
    assert destination.read_bytes() == before


def test_hopper_fire_requires_explicit_fire_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    _patch_cloud_preflight_off(monkeypatch)

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )
    pulse_count = 0

    class FakeActuator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeActuator":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            nonlocal pulse_count
            import time

            pulse_count += 1
            time.sleep(toggle_after_seconds)
            return shelly_cloud.ShellyCloudPulseResult(
                accepted=True,
                readback=shelly_cloud.ShellyCloudSwitchStatus(
                    device_id="5432046e5f58",
                    channel=0,
                    online=True,
                    output=False,
                    source="timer",
                ),
            )

    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", FakeActuator)

    result = runner.invoke(app, ["hopper", "fire", str(destination)], input="FIRE\n")

    assert result.exit_code == 0
    assert pulse_count == 1
    assert validate_hopper_plan(load_hopper_plan(destination)).status is HopperStatus.LOCKED
    assert "Type FIRE to send one one-shot pulse" in result.output
    assert "Electrical read-back: OFF" in result.output
    assert "mechanical hop release" in result.output


def test_hopper_fire_confirms_before_preflight_and_waits_for_cloud_margin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    events: list[str] = []
    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr("forge_companion.cli._is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )

    def confirm(message: str) -> str:
        events.append("confirmation")
        return "FIRE"

    monkeypatch.setattr("forge_companion.cli.typer.prompt", confirm)
    monotonic_values = iter([10.0, 10.25])
    monkeypatch.setattr("forge_companion.cli.monotonic", lambda: next(monotonic_values))

    def wait_for_rate_boundary(seconds: float) -> None:
        assert seconds == pytest.approx(1.0)
        events.append("rate-wait")

    monkeypatch.setattr("forge_companion.cli.sleep_seconds", wait_for_rate_boundary)

    class FakeReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            events.append("preflight")
            return shelly_cloud.ShellyCloudSwitchStatus(
                device_id="5432046e5f58",
                channel=channel,
                online=True,
                output=False,
                source="timer",
            )

    class FakeActuator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeActuator":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            import time

            events.append("pulse")
            time.sleep(toggle_after_seconds)
            return shelly_cloud.ShellyCloudPulseResult(
                accepted=True,
                readback=shelly_cloud.ShellyCloudSwitchStatus(
                    device_id="5432046e5f58",
                    channel=channel,
                    online=True,
                    output=False,
                    source="timer",
                ),
            )

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", FakeReadOnlyClient)
    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", FakeActuator)

    result = runner.invoke(app, ["hopper", "fire", str(destination)])

    assert result.exit_code == 0
    assert events == ["confirmation", "preflight", "rate-wait", "pulse"]


def test_hopper_fire_cancellation_never_calls_actuator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    monkeypatch.setattr("forge_companion.cli._is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: (_ for _ in ()).throw(AssertionError("credentials must not be resolved")),
    )
    monkeypatch.setattr(
        shelly_cloud,
        "ShellyCloudReadOnlyClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network must not be contacted")),
    )

    class ForbiddenActuator:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("actuator must not be created")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", ForbiddenActuator)

    result = runner.invoke(app, ["hopper", "fire", str(destination)], input="no\n")

    assert result.exit_code == 1
    assert "Hopper fire cancelled" in result.output
    assert validate_hopper_plan(load_hopper_plan(destination)).status is HopperStatus.ARMED


def test_hopper_fire_transport_error_consumes_one_shot_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    _patch_cloud_preflight_off(monkeypatch)

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )
    calls = 0

    class FailingActuator:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FailingActuator":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def pulse(self, *, channel: int, toggle_after_seconds: float) -> object:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("ambiguous")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", FailingActuator)

    result = runner.invoke(app, ["hopper", "fire", str(destination)], input="FIRE\n")

    assert result.exit_code == 1
    assert calls == 1
    assert "outcome is uncertain" in result.output
    assert "Do not retry automatically" in result.output
    assert validate_hopper_plan(load_hopper_plan(destination)).status is HopperStatus.FIRE_REQUESTED


def test_hopper_arm_and_status_label_cloud_plan_without_network(tmp_path: Path) -> None:
    destination = tmp_path / "cloud-plan.json"
    payload = create_hopper_plan(
        trigger_at=datetime(2099, 1, 1, 18, 0, tzinfo=UTC),
        pulse_duration_ms=1000,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
    )
    write_hopper_plan(payload, destination)

    armed = runner.invoke(app, ["hopper", "arm", str(destination)])
    status = runner.invoke(app, ["hopper", "status", str(destination)])

    assert armed.exit_code == 0
    assert armed.output == (
        "Hopper Cloud one-shot plan armed.\nStatus: ARMED\nNo device or network was contacted.\n"
    )
    assert status.exit_code == 0
    assert "Hopper Cloud one-shot plan is valid." in status.output
    assert "Pulse: 1000 ms (Cloud one-shot)" in status.output
    assert "No device or network was contacted." in status.output


def test_hopper_fire_refuses_preflight_that_is_not_online_and_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )

    class FakeReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FakeReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            return shelly_cloud.ShellyCloudSwitchStatus(
                device_id="5432046e5f58",
                channel=channel,
                online=True,
                output=True,
                source="HTTP",
            )

    class ForbiddenActuator:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("actuator must not be created when preflight is not OFF")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", FakeReadOnlyClient)
    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", ForbiddenActuator)
    monkeypatch.setattr("forge_companion.cli._is_interactive_terminal", lambda: True)

    result = runner.invoke(app, ["hopper", "fire", str(destination)], input="FIRE\n")

    assert result.exit_code == 1
    assert "preflight requires an online device reporting OFF" in result.output
    assert result.output.index("Type FIRE") < result.output.index("preflight requires")
    assert load_hopper_plan(destination)["state"]["status"] == "ARMED"


def test_hopper_fire_preflight_error_never_constructs_actuator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    profile = shelly_cloud_credentials.ShellyCloudProfile(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046e5f58",
        auth_key="synthetic-secret-key",
    )
    monkeypatch.setattr("forge_companion.cli._is_interactive_terminal", lambda: True)
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: shelly_cloud_credentials.ResolvedCloudProfile(profile=profile, source="keyring"),
    )

    class FailingReadOnlyClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "FailingReadOnlyClient":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get_switch_status(self, channel: int = 0) -> object:
            raise shelly_cloud.ShellyCloudResponseError("synthetic preflight failure")

    class ForbiddenActuator:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError("actuator must not be created after preflight failure")

    monkeypatch.setattr(shelly_cloud, "ShellyCloudReadOnlyClient", FailingReadOnlyClient)
    monkeypatch.setattr(shelly_cloud, "ShellyCloudActuator", ForbiddenActuator)

    result = runner.invoke(app, ["hopper", "fire", str(destination)], input="FIRE\n")

    assert result.exit_code == 1
    assert "failed before a pulse request was recorded" in result.output
    assert load_hopper_plan(destination)["state"]["status"] == "ARMED"


def test_hopper_fire_refuses_noninteractive_input_before_any_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "cloud-plan.json"
    _write_armed_cloud_plan(destination)

    from forge_companion import shelly_cloud, shelly_cloud_credentials

    monkeypatch.setattr("forge_companion.cli._is_interactive_terminal", lambda: False)
    monkeypatch.setattr(
        shelly_cloud_credentials,
        "resolve_profile",
        lambda: (_ for _ in ()).throw(AssertionError("credentials must not be resolved")),
    )
    monkeypatch.setattr(
        shelly_cloud,
        "ShellyCloudReadOnlyClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("network must not be contacted")),
    )

    result = runner.invoke(app, ["hopper", "fire", str(destination)], input="FIRE\n")

    assert result.exit_code == 1
    assert "interactive terminal is required" in result.output
    assert load_hopper_plan(destination)["state"]["status"] == "ARMED"
