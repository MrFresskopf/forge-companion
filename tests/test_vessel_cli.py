import json
from datetime import UTC

import pytest
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli_module
from forge_companion.cli import app
from forge_companion.rapt import RaptResponseError

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
runner = CliRunner()


def test_bind_list_show_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(
        app,
        [
            "vessel",
            "bind",
            "fermzilla",
            "--hydrometer",
            PILL,
            "--temperature-controller",
            CONTROLLER,
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "vessels.json").read_text())
    assert data == {
        "schema_version": "forge-companion-vessels-v1",
        "vessels": [
            {
                "vessel_id": "fermzilla",
                "hydrometer": PILL,
                "temperature_controller": CONTROLLER,
                "gravity_interpretation": "unknown",
            }
        ],
    }
    assert "fermzilla" in runner.invoke(app, ["vessel", "list"]).stdout
    shown = runner.invoke(app, ["vessel", "show", "fermzilla"])
    assert shown.exit_code == 0
    assert PILL in shown.stdout and CONTROLLER in shown.stdout
    assert "gravity_interpretation=unknown" in shown.stdout


def test_bind_refuses_overwrite_and_device_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    args = ["vessel", "bind", "tank", "--hydrometer", PILL, "--temperature-controller", CONTROLLER]
    assert runner.invoke(app, args).exit_code == 0
    original = (tmp_path / "vessels.json").read_bytes()
    for attempt in [
        args,
        [*args[:2], "other", *args[3:]],
        [*args[:2], "other", *args[3:], "--replace"],
    ]:
        result = runner.invoke(app, attempt)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr
        assert (tmp_path / "vessels.json").read_bytes() == original
    assert (
        runner.invoke(
            app, [*args, "--replace", "--gravity-interpretation", "sg-times-1000"]
        ).exit_code
        == 0
    )
    assert "sg-times-1000" in runner.invoke(app, ["vessel", "show", "tank"]).stdout


def _write_binding(tmp_path, gravity_interpretation="unknown"):
    (tmp_path / "vessels.json").write_text(
        json.dumps(
            {
                "schema_version": "forge-companion-vessels-v1",
                "vessels": [
                    {
                        "vessel_id": "tank",
                        "hydrometer": PILL,
                        "temperature_controller": CONTROLLER,
                        "gravity_interpretation": gravity_interpretation,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_vessel_telemetry_reads_both_streams_before_output(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _write_binding(tmp_path, "sg-times-1000")
    profile = cli_module.rapt_credentials.RaptProfile(username="private", api_secret="secret")
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: profile)

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_hydrometer_telemetry(self, **kwargs):
            return [
                {
                    "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "createdOn": "2026-09-08T18:08:00.7034888Z",
                    "temperature": 4.6875,
                    "gravity": 1016.3,
                    "gravityVelocity": -7.41505,
                    "battery": 99,
                    "rssi": -29,
                }
            ]

        def get_temperature_controller_telemetry(self, **kwargs):
            return [
                {
                    "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "createdOn": "2026-09-08T18:09:00Z",
                    "temperature": 5,
                    "targetTemperature": 6,
                    "controlDeviceTemperature": 4.5,
                    "rssi": -31,
                }
            ]

    monkeypatch.setattr(cli_module, "RaptClient", Client)
    result = runner.invoke(
        app,
        [
            "vessel",
            "telemetry",
            "tank",
            "--start",
            "2026-09-08T18:00:00Z",
            "--end",
            "2026-09-08T19:00:00Z",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "HYDROMETER readings=1" in result.stdout
    assert "TEMPERATURE_CONTROLLER readings=1" in result.stdout
    assert "2026-09-08T18:08:00.7034888+00:00" in result.stdout
    assert "gravity_raw=1016.3000" in result.stdout
    assert "gravity_velocity_raw=-7.415050" in result.stdout
    assert "sg=1.0163" in result.stdout
    assert profile.username not in result.output and profile.api_secret not in result.output


@pytest.mark.parametrize("gravity_interpretation", ["unknown", "sg-times-1000"])
def test_vessel_telemetry_only_derives_sg_for_explicit_binding(
    tmp_path, monkeypatch, gravity_interpretation
):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _write_binding(tmp_path, gravity_interpretation)
    reading = cli_module.TelemetryReading(
        source="rapt",
        device_kind=cli_module.DeviceKind.HYDROMETER,
        device_id=PILL,
        reading_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        observed_at=cli_module.datetime(2026, 9, 8, tzinfo=UTC),
        temperature_c=None,
        gravity_raw=1016.3,
        gravity_velocity_raw=-7.4,
        target_temperature_c=None,
        battery_percent=None,
        rssi=None,
    )
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    monkeypatch.setattr(cli_module, "_read_vessel_streams", lambda **kwargs: ((reading,), ()))
    result = runner.invoke(
        app,
        [
            "vessel",
            "telemetry",
            "tank",
            "--start",
            "2026-09-08T00:00:00Z",
            "--end",
            "2026-09-09T00:00:00Z",
        ],
    )
    assert result.exit_code == 0
    assert (" sg=1.0163" in result.stdout) is (gravity_interpretation == "sg-times-1000")
    assert "gravity_velocity_raw=-7.400000" in result.stdout
    assert "sg_per_day" not in result.stdout


def test_vessel_telemetry_local_failures_precede_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))

    def forbidden():
        pytest.fail("credentials accessed before local validation")

    monkeypatch.setattr(cli_module, "_profile_for_api", forbidden)
    for args in [
        ["missing", "--start", "2026-09-08T00:00:00Z", "--end", "2026-09-09T00:00:00Z"],
        ["missing", "--start", "invalid", "--end", "2026-09-09T00:00:00Z"],
    ]:
        result = runner.invoke(app, ["vessel", "telemetry", *args])
        assert result.exit_code == 1
        assert result.stdout == ""


def test_vessel_telemetry_second_stream_failure_has_no_partial_success(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _write_binding(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())

    def fail(**kwargs):
        raise RaptResponseError("private second-device detail")

    monkeypatch.setattr(cli_module, "_read_vessel_streams", fail)
    result = runner.invoke(
        app,
        [
            "vessel",
            "telemetry",
            "tank",
            "--start",
            "2026-09-08T00:00:00Z",
            "--end",
            "2026-09-09T00:00:00Z",
        ],
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "private second-device detail" not in result.output
