import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli_module
from forge_companion.cli import app
from forge_companion.rapt import RaptResponseError
from forge_companion.telemetry import DeviceKind, TelemetryReading, parse_rapt_telemetry

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
runner = CliRunner()


def test_conflicting_sg_report_exposes_reason_without_misleading_aggregates(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    rows = parse_rapt_telemetry(
        [
            {
                "id": f"{i:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": f"2026-09-03T00:{minute:02d}:00.0000001Z",
                "temperature": 20.0,
                "gravity": gravity,
                "battery": 90.0,
                "rssi": -50.0,
            }
            for i, (minute, gravity) in enumerate(
                [(0, 1050), (0, 1030), (30, 1045), (59, 1040), (59, 1060)], 1
            )
        ],
        kind=DeviceKind.HYDROMETER,
        device_id=PILL,
    )
    monkeypatch.setattr(cli_module, "_read_hydrometer", lambda **kwargs: rows)
    command = args()
    command[command.index("--end") + 1] = "2026-09-03T01:00:00Z"
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert "sg_status=CONFLICTING_SG_AT_TIMESTAMP" in result.stdout
    assert "endpoint_trend=NO_TREND" in result.stdout
    for field in ("first_sg", "latest_sg", "delta_sg", "observed_rate_sg_per_day"):
        assert f"{field}=NO_DATA" in result.stdout
    assert "endpoint_trend=FALLING" not in result.stdout
    assert "endpoint_trend=RISING" not in result.stdout


def local_state(tmp_path, *, interpretation="sg-times-1000", current=True):
    bound_pill = PILL if current else "33333333-3333-4333-8333-333333333333"
    (tmp_path / "vessels.json").write_text(
        json.dumps(
            {
                "schema_version": "forge-companion-vessels-v1",
                "vessels": [
                    {
                        "vessel_id": "tank",
                        "hydrometer": bound_pill,
                        "temperature_controller": CONTROLLER,
                        "gravity_interpretation": interpretation,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fermentation-contexts.json").write_text(
        json.dumps(
            {
                "schema_version": "forge-companion-fermentation-contexts-v1",
                "contexts": [
                    {
                        "context_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "vessel_id": "tank",
                        "batch_display_name": "Batch",
                        "original_gravity_sg": "1.050",
                        "expected_final_gravity_sg": "1.010",
                        "yeast": "Yeast",
                        "fermentation_start_date": "2026-09-01",
                        "start_instant": None,
                        "same_day_telemetry_attribution": "unavailable",
                        "authoritative_temperature_role": "hydrometer",
                        "source_devices": {
                            "hydrometer": PILL,
                            "temperature_controller": CONTROLLER,
                        },
                        "status": "active",
                        "phase_history": [{"phase": "cold-crash", "start_date": "2026-09-04"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def args(*extra):
    return [
        "vessel",
        "sg-trend",
        "tank",
        "--start",
        "2026-09-02T00:00:00Z",
        "--end",
        "2026-09-05T00:00:00Z",
        "--phase-timezone",
        "Europe/Berlin",
        *extra,
    ]


@pytest.mark.parametrize("case", ["unknown", "drift", "too-long", "bad-zone"])
def test_all_local_gates_precede_credentials(tmp_path, monkeypatch, case):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(
        tmp_path,
        interpretation="unknown" if case == "unknown" else "sg-times-1000",
        current=case != "drift",
    )
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: pytest.fail("credentials accessed"))
    command = args()
    if case == "too-long":
        command[command.index("--end") + 1] = "2026-09-10T00:00:00Z"
    if case == "bad-zone":
        command[command.index("--phase-timezone") + 1] = "Not/A_Zone"
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert result.stdout == ""


def test_fetches_only_snapshotted_hydrometer_and_reports_cautions(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    calls = []
    row = TelemetryReading(
        source="rapt",
        device_kind=DeviceKind.HYDROMETER,
        device_id=PILL,
        reading_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        observed_at=datetime(2026, 9, 3, tzinfo=UTC),
        temperature_c=20.0,
        gravity_raw=1040.0,
        gravity_velocity_raw=-99.0,
        target_temperature_c=None,
        battery_percent=None,
        rssi=None,
    )
    monkeypatch.setattr(
        cli_module, "_read_hydrometer", lambda **kwargs: calls.append(kwargs) or (row,)
    )
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert calls[0]["device_id"] == PILL
    assert "phase=fermentation" in result.stdout
    assert "gravity_velocity" not in result.stdout
    assert "date interpretation only; no phase start clock is known" in result.stdout
    assert "cold-crash stability is not evidence" in result.stdout
    assert "bottling" in result.stdout and "switching" in result.stdout


def test_request_failure_emits_no_partial_report(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    monkeypatch.setattr(
        cli_module,
        "_read_hydrometer",
        lambda **kwargs: (_ for _ in ()).throw(RaptResponseError("private detail")),
    )
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "private detail" not in result.output
    assert "Vessel SG trend read-only" not in result.output
