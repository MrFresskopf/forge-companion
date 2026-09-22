import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from test_vessel_sg_trend_cli import PILL, local_state
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli_module
from forge_companion import rapt_credentials
from forge_companion.cli import app
from forge_companion.rapt import RaptResponseError
from forge_companion.telemetry import DeviceKind, parse_rapt_telemetry

runner = CliRunner()


def args():
    return [
        "vessel", "sg-diagnose", "tank",
        "--start", "2026-09-03T00:00:00Z", "--end", "2026-09-03T01:00:00Z",
        "--trigger-sg", "1.020", "--max-age-minutes", "30",
        "--max-gap-minutes", "30", "--confirmations", "2",
    ]


def rows(values=(1019, 1018)):
    return parse_rapt_telemetry(
        [
            {"id": f"{i:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
             "createdOn": f"2026-09-03T00:{minute:02d}:00.0000001Z", "gravity": value,
             "temperature": 20.0, "battery": 90.0, "rssi": -50.0}
            for i, (minute, value) in enumerate(zip((30, 59), values, strict=True), 1)
        ], kind=DeviceKind.HYDROMETER, device_id=PILL,
    )


def test_sg_diagnostic_result_lines_render_metrics_candidates_and_source_boundary():
    from forge_companion.spunding_advisor import (
        AdvisorStatus,
        TelemetrySgEvidence,
        TelemetrySgReason,
        TelemetrySgResult,
    )

    result = TelemetrySgResult(
        status=AdvisorStatus.CONDITION_MET,
        reason=TelemetrySgReason.AT_OR_BELOW_TRIGGER,
        evidence=(TelemetrySgEvidence(observed_at_ns=123, sg=cli_module.Decimal("1.018")),),
        distinct_observations=2,
        latest_age_ns=0,
        largest_confirmation_gap_ns=1_800_000_000_000,
    )

    assert cli_module._sg_diagnostic_result_lines(
        result,
        permission_disclaimer=(
            "No fermentation-complete, packaging, or actuation permission is granted by any status."
        ),
        permission_boundary="No RAPT or Shelly device command was sent.",
    ) == [
        "status=CONDITION_MET reason=AT_OR_BELOW_TRIGGER",
        "distinct_observations=2 latest_age_ns=0 "
        "largest_confirmation_gap_ns=1800000000000",
        "Candidates are not quality-approved confirmations.",
        "CANDIDATE observed_at_ns=123 sg=1.018",
        "No fermentation-complete, packaging, or actuation permission is granted by any status.",
        "No RAPT or Shelly device command was sent.",
    ]


def test_diagnostic_normalizes_candidates_and_uses_explicit_query_end(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return rows()

    monkeypatch.setattr(cli_module, "_read_hydrometer", read)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert len(calls) == 1
    assert calls[0]["device_id"] == PILL
    assert calls[0]["end"] == datetime(2026, 9, 3, 1, tzinfo=UTC)
    assert "status=CONDITION_MET reason=AT_OR_BELOW_TRIGGER" in result.stdout
    assert "CANDIDATE observed_at_ns=" in result.stdout
    assert "sg=1.019" in result.stdout
    assert "latest_age_ns=59999999900" in result.stdout
    assert "Candidates are not quality-approved confirmations." in result.stdout
    assert "caller assertion, not unit proof or verified calibration" in result.stdout
    assert "query-end coverage, not live freshness" in result.stdout
    assert "No fermentation-complete, packaging, or actuation permission" in result.stdout
    assert "No RAPT or Shelly device command was sent." in result.stdout


@pytest.mark.parametrize("case", ["conflict", "reused-id", "wrong-device", "outside-window"])
def test_integrity_failure_has_no_partial_stdout(tmp_path, monkeypatch, case):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    first, last = rows()
    if case == "conflict":
        last = replace(last, observed_at=first.observed_at)
    elif case == "reused-id":
        last = replace(last, reading_id=first.reading_id)
    elif case == "wrong-device":
        first = replace(first, device_id="33333333-3333-4333-8333-333333333333")
        last = replace(last, device_id=first.device_id)
    else:
        last = replace(last, observed_at=datetime(2026, 9, 3, 1, tzinfo=UTC))
    monkeypatch.setattr(cli_module, "_read_hydrometer", lambda **kwargs: (first, last))
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Vessel SG diagnostic failed: request or response is invalid.\n"


@pytest.mark.parametrize("value", [219.99, 238.007, 899.99, 1200.01, None])
def test_unusable_sg_never_appears_as_usable_output(tmp_path, monkeypatch, value):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    monkeypatch.setattr(cli_module, "_read_hydrometer", lambda **kwargs: rows((1019, value)))
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Vessel SG diagnostic failed: request or response is invalid.\n"



@pytest.mark.parametrize("case", ["missing", "closed", "duplicate", "drift", "unknown"])
def test_local_context_gate_precedes_credentials(tmp_path, monkeypatch, case):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path, current=case != "drift",
                interpretation="unknown" if case == "unknown" else "sg-times-1000")
    path = tmp_path / "fermentation-contexts.json"
    document = json.loads(path.read_text())
    if case == "missing":
        document["contexts"] = []
    elif case == "closed":
        document["contexts"][0]["status"] = "closed"
    elif case == "duplicate":
        document["contexts"].append(dict(document["contexts"][0]))
    path.write_text(json.dumps(document))
    credentials = Mock()
    read = Mock()
    monkeypatch.setattr(cli_module, "_profile_for_api", credentials)
    monkeypatch.setattr(cli_module, "_read_hydrometer", read)
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Vessel SG diagnostic failed: input or local state is invalid.\n"
    credentials.assert_not_called()
    read.assert_not_called()


@pytest.mark.parametrize(("flag", "value"), [
    ("--trigger-sg", "NaN"), ("--trigger-sg", "invalid"), ("--trigger-sg", "1.21"),
    ("--max-age-minutes", "nan"), ("--max-age-minutes", "0"),
    ("--max-gap-minutes", "2881"), ("--max-gap-minutes", "-1"),
    ("--max-age-minutes", "1e-20"),
    ("--start", "2026-09-03T00:00:00"),
    ("--start", "2026-09-03T01:00:00Z"),
    ("--end", "2026-09-12T01:00:00Z"),
    ("--end", "2026-09-03T01:00:00.0000001Z"),
])
def test_invalid_policy_precedes_credentials(tmp_path, monkeypatch, flag, value):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    credentials = Mock()
    monkeypatch.setattr(cli_module, "_profile_for_api", credentials)
    command = args()
    command[command.index(flag) + 1] = value
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr
    credentials.assert_not_called()


@pytest.mark.parametrize("flag", ["--start", "--end", "--trigger-sg",
                                  "--max-age-minutes", "--max-gap-minutes", "--confirmations"])
def test_policy_has_no_defaults(tmp_path, monkeypatch, flag):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    credentials = Mock()
    monkeypatch.setattr(cli_module, "_profile_for_api", credentials)
    command = args()
    index = command.index(flag)
    del command[index:index + 2]
    result = runner.invoke(app, command)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr
    credentials.assert_not_called()


@pytest.mark.parametrize(("case", "status", "reason"), [
    ("empty", "NO_DECISION", "NO_READINGS"),
    ("insufficient", "NO_DECISION", "INSUFFICIENT_CONFIRMATIONS"),
    ("stale", "NO_DECISION", "STALE"),
    ("gap", "NO_DECISION", "GAP_EXCEEDED"),
    ("wait", "WAIT", "ABOVE_TRIGGER"),
])
def test_diagnostic_quality_outcomes(tmp_path, monkeypatch, case, status, reason):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    readings = rows((1021, 1018)) if case == "wait" else rows()
    if case == "empty":
        readings = ()
    elif case == "insufficient":
        readings = readings[:1]
    command = args()
    if case in {"stale", "gap"}:
        flag = "--max-age-minutes" if case == "stale" else "--max-gap-minutes"
        command[command.index(flag) + 1] = "0.5"
    monkeypatch.setattr(cli_module, "_read_hydrometer", lambda **kwargs: readings)
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert f"status={status} reason={reason}" in result.stdout
    assert result.stdout.count("CANDIDATE observed_at_ns=") == len(readings)
    assert "Candidates are not quality-approved confirmations." in result.stdout
    if case == "empty":
        assert "latest_age_ns=NO_DATA" in result.stdout


@pytest.mark.parametrize(("case", "code"), [("missing", 2), ("store-error", 1)])
def test_shared_credential_setup_exit_contract(tmp_path, monkeypatch, case, code):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    resolver = Mock(return_value=rapt_credentials.ResolvedRaptProfile(None, "missing"))
    if case == "store-error":
        resolver.side_effect = rapt_credentials.RaptCredentialError("private-backend-detail")
    monkeypatch.setattr(rapt_credentials, "resolve_profile", resolver)
    read = Mock()
    monkeypatch.setattr(cli_module, "_read_hydrometer", read)
    result = runner.invoke(app, args())
    assert result.exit_code == code
    assert result.stdout == ""
    assert result.stderr
    assert "private-backend-detail" not in result.stderr
    read.assert_not_called()


def test_request_failure_is_generic_and_does_not_evaluate(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    read = Mock(side_effect=RaptResponseError("private-response-detail"))
    evaluator = Mock()
    monkeypatch.setattr(cli_module, "_read_hydrometer", read)
    monkeypatch.setattr(cli_module, "explain_telemetry_sg", evaluator)
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Vessel SG diagnostic failed: request or response is invalid.\n"
    evaluator.assert_not_called()


def test_one_normalization_one_evaluator_no_local_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    monkeypatch.setattr(cli_module, "_profile_for_api", lambda: object())
    raw = rows()
    monkeypatch.setattr(cli_module, "_read_hydrometer", lambda **kwargs: raw)
    normalizer = Mock(wraps=cli_module.normalize_rapt_sg)
    evaluator = Mock(wraps=cli_module.explain_telemetry_sg)
    monkeypatch.setattr(cli_module, "normalize_rapt_sg", normalizer)
    monkeypatch.setattr(cli_module, "explain_telemetry_sg", evaluator)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    normalizer.assert_called_once_with(raw, gravity_interpretation="sg-times-1000")
    evaluator.assert_called_once()
    assert evaluator.call_args.args[0][0].gravity_raw == 1.019
    assert evaluator.call_args.kwargs["gravity_unit"] == "sg"
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize("value", ["1", "6", "invalid"])
def test_confirmation_parser_range_precedes_credentials(tmp_path, monkeypatch, value):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    credentials = Mock()
    monkeypatch.setattr(cli_module, "_profile_for_api", credentials)
    command = args()
    command[command.index("--confirmations") + 1] = value
    result = runner.invoke(app, command)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr
    credentials.assert_not_called()
