import json
from unittest.mock import Mock

from test_vessel_sg_trend_cli import local_state
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli
from forge_companion.cli import app
from forge_companion.telemetry import parse_rapt_telemetry

BREW = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
runner = CliRunner()


def args(source="rapt"):
    return [
        "vessel",
        "local-outliers",
        "tank",
        "--source",
        source,
        "--start",
        "2026-09-03T00:00:00.0000000Z",
        "--end",
        "2026-09-03T01:00:00.0000000Z",
        "--max-neighbor-gap-ns",
        "1800000000000",
        "--gravity-unit",
        "sg",
        "--gravity-threshold",
        "0.0005",
    ] + (
        ["--temperature-unit", "c", "--temperature-threshold", "0.5"]
        if source == "brewforge"
        else []
    )


def test_rapt_outlier_has_exact_evidence_and_never_assesses_raw_temperature(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    raw = parse_rapt_telemetry(
        [
            {
                "id": "00000001-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-03T00:00:00Z",
                "gravity": 1010,
                "temperature": 99,
                "battery": 90,
                "rssi": -50,
            },
            {
                "id": "00000002-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-03T00:30:00Z",
                "gravity": 1020,
                "temperature": -99,
                "battery": 90,
                "rssi": -50,
            },
            {
                "id": "00000003-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-03T01:00:00Z",
                "gravity": 1010,
                "temperature": 99,
                "battery": 90,
                "rssi": -50,
            },
        ],
        kind=cli.DeviceKind.HYDROMETER,
        device_id="11111111-1111-4111-8111-111111111111",
    )
    monkeypatch.setattr(cli, "_profile_for_api", lambda: object())
    read = Mock(return_value=raw)
    monkeypatch.setattr(cli, "_read_hydrometer", read)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert read.call_count == 1
    assert "assessments=1 findings=1" in result.stdout
    assert "metric=gravity-sg candidate_id=00000002-aaaa-4aaa-8aaa-aaaaaaaaaaaa" in result.stdout
    assert "temperature-c" not in result.stdout
    assert "evidence only" in result.stdout


def test_rapt_transport_ceil_preserves_requested_100ns_end(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    raw = parse_rapt_telemetry(
        [
            {
                "id": "00000011-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-03T00:00:00.0000001Z",
                "gravity": 1010,
                "temperature": 20,
                "battery": 90,
                "rssi": -50,
            },
            {
                "id": "00000012-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-03T00:00:00.0000005Z",
                "gravity": 1020,
                "temperature": 20,
                "battery": 90,
                "rssi": -50,
            },
            {
                "id": "00000013-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "createdOn": "2026-09-03T00:00:00.0000009Z",
                "gravity": 1010,
                "temperature": 20,
                "battery": 90,
                "rssi": -50,
            },
        ],
        kind=cli.DeviceKind.HYDROMETER,
        device_id="11111111-1111-4111-8111-111111111111",
    )
    monkeypatch.setattr(cli, "_profile_for_api", lambda: object())
    read = Mock(return_value=raw)
    monkeypatch.setattr(cli, "_read_hydrometer", read)
    command = args()
    command[command.index("--start") + 1] = "2026-09-03T00:00:00.0000001Z"
    command[command.index("--end") + 1] = "2026-09-03T00:00:00.0000009Z"

    result = runner.invoke(app, command)

    assert result.exit_code == 0, result.output
    assert read.call_args.kwargs["start"].microsecond == 0
    assert read.call_args.kwargs["end"].microsecond == 1
    assert "candidate_id=00000012-aaaa-4aaa-8aaa-aaaaaaaaaaaa" in result.stdout

def test_brewforge_one_get_temperature_requires_c_and_preflight_blocks_token(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    path = tmp_path / "fermentation-contexts.json"
    d = json.loads(path.read_text())
    d["contexts"][0]["brewforge_brew_id"] = BREW
    path.write_text(json.dumps(d))
    events = []
    monkeypatch.setattr(cli, "_token_for_api", lambda: events.append("token") or "x")

    class Client:
        def __init__(self, **kw):
            events.append("client")

        def get(self, path):
            events.append(("get", path))
            return {
                "data": [
                    {
                        "id": "a",
                        "timestamp": "2026-09-03T00:00:00Z",
                        "gravity": 1.010,
                        "temperature": 20,
                    },
                    {
                        "id": "b",
                        "timestamp": "2026-09-03T00:30:00Z",
                        "gravity": 1.010,
                        "temperature": 30,
                    },
                    {
                        "id": "c",
                        "timestamp": "2026-09-03T01:00:00Z",
                        "gravity": 1.010,
                        "temperature": 20,
                    },
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", Client)
    result = runner.invoke(app, args("brewforge"))
    assert result.exit_code == 0, result.output
    assert events == ["token", "client", ("get", f"brews/{BREW}/readings")]
    assert "metric=temperature-c candidate_id=b" in result.stdout
    bad = args("brewforge")
    bad[bad.index("--gravity-unit") + 1] = "SG"
    events.clear()
    result = runner.invoke(app, bad)
    assert result.exit_code == 1 and result.stdout == "" and events == []


def test_malformed_source_is_atomic_and_boundaries_are_unassessed(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli, "_profile_for_api", lambda: object())
    monkeypatch.setattr(cli, "_read_hydrometer", lambda **kw: ())
    result = runner.invoke(app, args())
    assert result.exit_code == 0 and "assessments=0 findings=0" in result.stdout
    monkeypatch.setattr(cli, "_read_hydrometer", lambda **kw: (object(),))
    result = runner.invoke(app, args())
    assert result.exit_code == 1 and result.stdout == ""
    assert result.stderr == "Local outlier evidence failed: request or response is invalid.\n"
