import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli_module
from forge_companion.cli import app
from forge_companion.fermentation_contexts import start_fermentation_context
from forge_companion.vessels import bind_vessel

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
runner = CliRunner()


@pytest.fixture
def local_context(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel({
        "vessel_id": "tank", "hydrometer": PILL, "temperature_controller": CONTROLLER,
        "gravity_interpretation": "sg-times-1000",
    })
    start_fermentation_context(
        vessel_id="tank", batch_display_name="Synthetic Batch", original_gravity_sg="1.077",
        expected_final_gravity_sg="1.015", yeast="Test yeast",
        fermentation_start_date="2026-08-29",
        authoritative_temperature_role="controller",
    )
    return tmp_path


@pytest.mark.parametrize(
    "failure",
    [
        "missing-context",
        "closed",
        "changed",
        "missing-binding",
        "invalid-context",
        "invalid-binding",
        "duplicate-active",
    ],
)
def test_overview_requires_active_matching_context_before_credentials(
    local_context, monkeypatch, failure
):
    context_path = local_context / "fermentation-contexts.json"
    binding_path = local_context / "vessels.json"
    data = json.loads(context_path.read_text())
    if failure == "missing-context":
        context_path.unlink()
    elif failure == "closed":
        data["contexts"][0]["status"] = "closed"
        context_path.write_text(json.dumps(data))
    elif failure == "duplicate-active":
        duplicate = dict(data["contexts"][0])
        duplicate["context_id"] = "33333333-3333-4333-8333-333333333333"
        data["contexts"].append(duplicate)
        context_path.write_text(json.dumps(data))
    elif failure == "changed":
        data["contexts"][0]["source_devices"]["hydrometer"] = "33333333-3333-4333-8333-333333333333"
        context_path.write_text(json.dumps(data))
    elif failure == "missing-binding":
        binding_path.unlink()
    elif failure == "invalid-binding":
        binding_path.write_text("invalid")
    else:
        context_path.write_text("invalid")
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: pytest.fail("credentials accessed"))
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Vessel overview failed:" in result.stderr


def test_overview_captures_both_streams_once_with_context(local_context, monkeypatch):
    calls = []
    clock_calls = []
    monkeypatch.setattr(cli_module, "_utc_now", lambda: clock_calls.append(1) or NOW)
    monkeypatch.setattr(
        cli_module,
        "_profile_for_api",
        lambda: cli_module.rapt_credentials.RaptProfile(
            username="private-user", api_secret="private-secret",
        ),
    )

    class Client:
        def __init__(self, **kwargs):
            calls.append("client")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_hydrometer_telemetry(self, **kwargs):
            calls.append(("pill", kwargs))
            return [{"id": PILL, "createdOn": "2026-09-09T10:30:00Z",
                     "temperature": 19.0, "gravity": 1016.3,
                     "gravityVelocity": -1.0, "battery": 99, "rssi": -40}]

        def get_temperature_controller_telemetry(self, **kwargs):
            calls.append(("controller", kwargs))
            return [{"id": CONTROLLER, "createdOn": "2026-09-09T11:30:00Z",
                     "temperature": 18.0}]

    monkeypatch.setattr(cli_module, "RaptClient", Client)
    before = {p.name: p.read_bytes() for p in local_context.iterdir()}
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 0, result.output
    assert "Vessel overview read-only." in result.stdout
    assert "batch=Synthetic Batch" in result.stdout
    assert "yeast=Test yeast" in result.stdout
    assert "original_gravity_sg=1.077" in result.stdout
    assert "expected_final_gravity_sg=1.015" in result.stdout
    assert (
        "latest_recorded_phase=fermentation latest_recorded_phase_date=2026-08-29"
        in result.stdout
    )
    assert "HYDROMETER status=CURRENT" in result.stdout
    assert "TEMPERATURE_CONTROLLER status=CURRENT" in result.stdout
    assert "PILL gravity_raw=1016.3000 sg=1.0163" in result.stdout
    assert "authoritative_temperature_role=controller" in result.stdout
    assert "authoritative_temperature=18.0 C" in result.stdout
    assert "temperature=19.0 C" in result.stdout
    assert "Pill threshold_ns=5400000000000 (~90 minutes)" in result.stdout
    assert "controller threshold_ns=1800000000000 (~30 minutes)" in result.stdout
    assert clock_calls == [1]
    assert len(calls) == 3 and calls[0] == "client"
    assert [c[0] for c in calls[1:]] == ["pill", "controller"]
    for _, kwargs in calls[1:]:
        assert kwargs["start"] == NOW - timedelta(hours=48)
        assert kwargs["end"] == NOW
    assert "private" not in result.output
    assert {p.name: p.read_bytes() for p in local_context.iterdir()} == before


def _reading(kind, *, age=timedelta(), remainder=0, temperature=19.0, gravity=1016.3):
    return cli_module.TelemetryReading(
        source="rapt",
        device_kind=kind,
        device_id=PILL if kind is cli_module.DeviceKind.HYDROMETER else CONTROLLER,
        reading_id=PILL, observed_at=NOW - age, observed_at_submicrosecond_ns=remainder,
        temperature_c=temperature, gravity_raw=gravity, gravity_velocity_raw=None,
        target_temperature_c=None, battery_percent=None, rssi=None,
    )


def _streams(monkeypatch, pill, controller):
    monkeypatch.setattr(cli_module, "_utc_now", lambda: NOW)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    monkeypatch.setattr(cli_module, "_read_vessel_streams", lambda **kwargs: (pill, controller))


@pytest.mark.parametrize("role", ["hydrometer", "controller"])
@pytest.mark.parametrize("temperature", [None, 4.25])
def test_authoritative_temperature_status_is_field_specific(
    local_context, monkeypatch, role, temperature
):
    path = local_context / "fermentation-contexts.json"
    data = json.loads(path.read_text())
    data["contexts"][0]["authoritative_temperature_role"] = role
    path.write_text(json.dumps(data))
    pill = _reading(
        cli_module.DeviceKind.HYDROMETER,
        age=timedelta(minutes=91),
        temperature=temperature,
    )
    controller = _reading(cli_module.DeviceKind.TEMPERATURE_CONTROLLER, temperature=temperature)
    _streams(monkeypatch, (pill,), (controller,))
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 0, result.output
    expected = (
        "NO_DATA" if temperature is None else ("STALE" if role == "hydrometer" else "CURRENT")
    )
    assert f"authoritative_temperature_status={expected}" in result.stdout
    assert "HYDROMETER status=STALE" in result.stdout
    assert "TEMPERATURE_CONTROLLER status=CURRENT" in result.stdout


@pytest.mark.parametrize("interpretation", ["unknown", "sg-times-1000"])
@pytest.mark.parametrize("gravity", [None, 1016.3])
def test_gravity_is_latest_only_and_requires_explicit_interpretation(
    local_context, monkeypatch, interpretation, gravity
):
    path = local_context / "vessels.json"
    data = json.loads(path.read_text())
    data["vessels"][0]["gravity_interpretation"] = interpretation
    path.write_text(json.dumps(data))
    previous = _reading(cli_module.DeviceKind.HYDROMETER, age=timedelta(minutes=1))
    latest = _reading(cli_module.DeviceKind.HYDROMETER, gravity=gravity)
    _streams(monkeypatch, (previous, latest), ())
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 0, result.output
    expected = "gravity_raw=NO_DATA sg=NO_DATA" if gravity is None else (
        "gravity_raw=1016.3000 sg=1.0163" if interpretation == "sg-times-1000"
        else "gravity_raw=1016.3000 sg=UNINTERPRETED"
    )
    assert expected in result.stdout
    assert (
        "authoritative_temperature=NO_DATA authoritative_temperature_status=NO_DATA"
        in result.stdout
    )
    assert "TEMPERATURE_CONTROLLER status=NO_DATA" in result.stdout


def test_overview_preserves_100ns_age_and_empty_stream(local_context, monkeypatch):
    pill = _reading(cli_module.DeviceKind.HYDROMETER,
                    age=timedelta(minutes=90, microseconds=1), remainder=900)
    _streams(monkeypatch, (pill,), ())
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 0, result.output
    assert "HYDROMETER status=STALE" in result.stdout
    assert "2026-09-09T10:29:59.9999999+00:00" in result.stdout
    assert "age_ns=5400000000100 age_approx=90 minutes" in result.stdout
    assert "TEMPERATURE_CONTROLLER status=NO_DATA latest=NO_OBSERVATION_IN_WINDOW" in result.stdout
    assert "history=NOT_QUERIED reason=no_observations_in_recent_48h" in result.stdout


def test_no_streams_does_not_invent_measurements(local_context, monkeypatch):
    _streams(monkeypatch, (), ())
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 0
    assert result.stdout.count("status=NO_DATA latest=NO_OBSERVATION_IN_WINDOW") == 2
    assert "PILL gravity_raw=NO_DATA sg=NO_DATA" in result.stdout


@pytest.mark.parametrize("failure", ["request", "invalid", "future"])
def test_second_stream_failure_never_prints_partial_success(local_context, monkeypatch, failure):
    calls = []
    monkeypatch.setattr(cli_module, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        cli_module,
        "_profile_for_api",
        lambda: cli_module.rapt_credentials.RaptProfile(
            username="private-user", api_secret="private-secret"
        ),
    )

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get_hydrometer_telemetry(self, **kwargs):
            calls.append("pill")
            return []

        def get_temperature_controller_telemetry(self, **kwargs):
            calls.append("controller")
            if failure == "request":
                raise cli_module.RaptError("private-secret private-user")
            if failure == "invalid":
                return [{"private": "private-secret"}]
            return [{"id": CONTROLLER, "createdOn": "2026-09-09T12:00:00.0000001Z"}]

    monkeypatch.setattr(cli_module, "RaptClient", Client)
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert calls == ["pill", "controller"]
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Vessel overview failed: request or response is invalid.\n"


def test_overview_latest_recorded_phase_is_not_a_temporal_claim(local_context, monkeypatch):
    path = local_context / "fermentation-contexts.json"
    data = json.loads(path.read_text())
    data["contexts"][0]["phase_history"] = [{"phase": "cold-crash", "start_date": "2026-09-05"}]
    path.write_text(json.dumps(data))
    _streams(monkeypatch, (), ())
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 0, result.output
    assert (
        "latest_recorded_phase=cold-crash latest_recorded_phase_date=2026-09-05"
        in result.stdout
    )
    assert "active_phase=" not in result.stdout
    assert "fermentation_complete" not in result.stdout


def test_overview_future_phase_date_fails_closed(local_context, monkeypatch):
    path = local_context / "fermentation-contexts.json"
    data = json.loads(path.read_text())
    data["contexts"][0]["phase_history"] = [{"phase": "cold-crash", "start_date": "2099-09-09"}]
    path.write_text(json.dumps(data))
    _streams(monkeypatch, (), ())
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: pytest.fail("credentials accessed"))
    monkeypatch.setattr(
        cli_module, "_read_vessel_streams", lambda **kwargs: pytest.fail("streams read")
    )
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr


def test_overview_missing_profile_and_parser_errors(local_context, monkeypatch):
    monkeypatch.setattr(
        cli_module.rapt_credentials,
        "resolve_profile",
        lambda: cli_module.rapt_credentials.ResolvedRaptProfile(
            profile=None, source="missing"
        ),
    )
    monkeypatch.setattr(cli_module, "RaptClient", lambda **kwargs: pytest.fail("client accessed"))
    result = runner.invoke(app, ["vessel", "overview", "tank"])
    assert result.exit_code == 2
    assert result.stdout == "" and result.stderr
    missing_argument = runner.invoke(app, ["vessel", "overview"])
    assert missing_argument.exit_code == 2
    assert missing_argument.stdout == "" and missing_argument.stderr
