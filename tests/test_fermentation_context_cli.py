import json

from typer.testing import CliRunner

from forge_companion.cli import app
from forge_companion.vessels import bind_vessel

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
runner = CliRunner()


def _bind():
    bind_vessel(
        {
            "vessel_id": "tank",
            "hydrometer": PILL,
            "temperature_controller": CONTROLLER,
            "gravity_interpretation": "unknown",
        }
    )


def _start_args(name="Test Batch"):
    return [
        "vessel",
        "context",
        "start",
        "tank",
        "--batch",
        name,
        "--original-gravity-sg",
        "1.077",
        "--expected-final-gravity-sg",
        "1.015",
        "--yeast",
        "US-05",
        "--start-date",
        "2026-08-29",
        "--authoritative-temperature-role",
        "hydrometer",
    ]


def test_context_start_show_close_are_offline_and_read_back_history(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    result = runner.invoke(app, _start_args())
    assert result.exit_code == 0, result.output
    assert "No credentials, API, telemetry, or device command" in result.stdout
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    context_id = stored["contexts"][0]["context_id"]

    shown = runner.invoke(app, ["vessel", "context", "show", "tank"])
    assert shown.exit_code == 0
    assert context_id in shown.stdout
    assert "start_instant=unavailable" in shown.stdout
    assert "same_day_telemetry_attribution=unavailable" in shown.stdout
    assert "binding_status=current" in shown.stdout

    assert runner.invoke(app, ["vessel", "context", "close", "tank"]).exit_code == 0
    history = runner.invoke(app, ["vessel", "context", "show", "tank", "--all"])
    assert history.exit_code == 0
    assert context_id in history.stdout and "status=closed" in history.stdout


def test_context_start_requires_explicit_switch_and_rejects_malformed_sg(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    assert runner.invoke(app, _start_args("First")).exit_code == 0
    refused = runner.invoke(app, _start_args("Second"))
    assert refused.exit_code == 1
    switched = runner.invoke(app, [*_start_args("Second"), "--switch"])
    assert switched.exit_code == 0
    malformed = _start_args("Third")
    malformed[malformed.index("1.077")] = ".1016"
    assert runner.invoke(app, [*malformed, "--switch"]).exit_code == 1


def test_changed_binding_is_reported_not_silently_used(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    assert runner.invoke(app, _start_args()).exit_code == 0
    new_pill = "33333333-3333-4333-8333-333333333333"
    new_controller = "44444444-4444-4444-8444-444444444444"
    bind_vessel(
        {
            "vessel_id": "tank",
            "hydrometer": new_pill,
            "temperature_controller": new_controller,
            "gravity_interpretation": "unknown",
        },
        replace=True,
    )
    shown = runner.invoke(app, ["vessel", "context", "show", "tank"])
    assert shown.exit_code == 0
    assert "binding_status=changed" in shown.stdout
    assert PILL in shown.stdout and new_pill not in shown.stdout
