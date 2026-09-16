"""Offline recording spies for the BrewForge diagnostic trust boundaries."""

import json
from dataclasses import replace
from datetime import datetime
from unittest.mock import Mock

import httpx
import pytest
from test_vessel_sg_trend_cli import local_state
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli
from forge_companion.cli import app
from forge_companion.telemetry import DeviceKind

BREW = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
runner = CliRunner()


def args():
    return [
        "vessel", "sg-diagnose-brewforge", "tank",
        "--start", "2026-09-03T00:00:00.0000001Z",
        "--end", "2026-09-03T01:00:00.0000001Z",
        "--trigger-sg", "1.020", "--max-age-minutes", "30",
        "--max-gap-minutes", "30", "--confirmations", "2",
        "--gravity-unit", "sg", "--temperature-unit", "c",
    ]


def payload():
    return {"data": [
        {"id": "first", "timestamp": "2026-09-03T00:30:00.0000001Z",
         "gravity": 1.019, "temperature": 20},
        {"id": "last", "timestamp": "2026-09-03T01:00:00.0000001Z",
         "gravity": 1.018, "temperature": 20},
    ]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    path = tmp_path / "fermentation-contexts.json"
    document = json.loads(path.read_text())
    document["contexts"][0]["brewforge_brew_id"] = BREW
    path.write_text(json.dumps(document))
    events = []
    response = payload()

    def token():
        events.append("token")
        return "test-token"

    class Client:
        def __init__(self, **kwargs):
            events.append(("client", kwargs))

        def get(self, *positional, **kwargs):
            events.append(("get", positional, kwargs))
            return response

    monkeypatch.setattr(cli, "_token_for_api", token)
    monkeypatch.setattr(cli, "BrewForgeClient", Client)
    return path, document, events, response


def test_single_get_adapter_evaluator_and_no_writes(setup, tmp_path, monkeypatch):
    _, _, events, response = setup
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    adapted = cli.adapt_brewforge_readings(
        response, brew_id=BREW, gravity_unit="sg", temperature_unit="c",
    )
    adapter = Mock(return_value=adapted)
    evaluator = Mock(wraps=cli.explain_telemetry_sg)
    monkeypatch.setattr(cli, "adapt_brewforge_readings", adapter)
    monkeypatch.setattr(cli, "explain_telemetry_sg", evaluator)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert events == ["token", ("client", {"token": "test-token"}),
                      ("get", (f"brews/{BREW}/readings",), {})]
    adapter.assert_called_once_with(response, brew_id=BREW, gravity_unit="sg", temperature_unit="c")
    evaluator.assert_called_once()
    assert evaluator.call_args.args[0] == adapted
    assert evaluator.call_args.kwargs == {
        "gravity_unit": "sg", "now_ns": 1788397200000000100,
        "trigger_sg": cli.Decimal("1.020"), "max_age_ns": 1800000000000,
        "max_gap_ns": 1800000000000, "confirmations": 2,
    }
    assert "source=brewforge gravity_unit=sg temperature_unit=c" in result.stdout
    assert "evaluation_at_ns=1788397200000000100" in result.stdout
    assert "status=CONDITION_MET reason=AT_OR_BELOW_TRIGGER" in result.stdout
    assert "latest_age_ns=0" in result.stdout
    assert "No BrewForge, RAPT, or Shelly write and no device command was sent." in result.stdout
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_exact_inclusive_window_filters_fetched_readings(setup, monkeypatch):
    """One unfiltered endpoint response is narrowed before the core is called."""
    response = setup[3]
    response["data"].extend(
        [
            {"id": "before", "timestamp": "2026-09-02T23:59:59.9999999Z",
             "gravity": 1.030, "temperature": 20},
            {"id": "after", "timestamp": "2026-09-03T01:00:00.0000002Z",
             "gravity": 1.010, "temperature": 20},
        ]
    )
    evaluator = Mock(wraps=cli.explain_telemetry_sg)
    monkeypatch.setattr(cli, "explain_telemetry_sg", evaluator)

    result = runner.invoke(app, args())

    assert result.exit_code == 0, result.output
    assert tuple(item.reading_id for item in evaluator.call_args.args[0]) == ("first", "last")


@pytest.mark.parametrize("case", [
    "missing", "closed", "duplicate", "missing-id", "bad-id", "uppercase-id",
    "bad-phase", "bad-context", "bad-json",
])
def test_context_preflight_before_token_and_client(setup, case):
    path, document, events, _ = setup
    context = document["contexts"][0]
    if case == "missing":
        document["contexts"] = []
    elif case == "closed":
        context["status"] = "closed"
    elif case == "duplicate":
        document["contexts"].append(
            dict(context, context_id="cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        )
    elif case == "missing-id":
        del context["brewforge_brew_id"]
    elif case in {"bad-id", "uppercase-id"}:
        context["brewforge_brew_id"] = "private/secret" if case == "bad-id" else BREW.upper()
    elif case == "bad-phase":
        context["phase_history"][0]["phase"] = "private-secret"
    elif case == "bad-context":
        context["original_gravity_sg"] = "private-secret"
    path.write_text("private-secret" if case == "bad-json" else json.dumps(document))
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "start-brewforge" in result.stderr
    assert "private" not in result.stderr
    assert len(result.stderr) < 250
    assert events == []


@pytest.mark.parametrize(("flag", "value"), [
    ("--gravity-unit", "sg-times-1000"), ("--gravity-unit", "SG"),
    ("--gravity-unit", "unknown"), ("--temperature-unit", "C"),
    ("--temperature-unit", "f"), ("--temperature-unit", "unknown"),
    ("--trigger-sg", "NaN"), ("--trigger-sg", "garbage"), ("--trigger-sg", "1.21"),
    ("--max-age-minutes", "nan"), ("--max-age-minutes", "0"),
    ("--max-gap-minutes", "2881"), ("--max-gap-minutes", "-1"),
    ("--max-age-minutes", "1e-20"),
    ("--start", "2026-09-03T00:00:00"),
    ("--start", "2026-09-03T01:00:00.0000001Z"),
    ("--end", "2026-09-12T01:00:00Z"),
    ("--end", "2026-09-03T01:00:00.00000001Z"),
])
def test_policy_preflight_before_token_and_client(setup, flag, value):
    command = args()
    command[command.index(flag) + 1] = value
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr
    assert setup[2] == []


@pytest.mark.parametrize("flag", ["--start", "--end", "--trigger-sg", "--max-age-minutes",
                                  "--max-gap-minutes", "--confirmations", "--gravity-unit",
                                  "--temperature-unit"])
def test_required_options_before_token_and_client(setup, flag):
    command = args()
    index = command.index(flag)
    del command[index:index + 2]
    result = runner.invoke(app, command)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert setup[2] == []


@pytest.mark.parametrize("value", ["1", "6", "garbage"])
def test_confirmation_range_before_token_and_client(setup, value):
    command = args()
    command[command.index("--confirmations") + 1] = value
    result = runner.invoke(app, command)
    assert result.exit_code == 2
    assert result.stdout == ""
    assert setup[2] == []


@pytest.mark.parametrize("case", [
    "empty", "list", "none", "object", "source", "all-source", "kind", "all-kind",
    "id", "all-id", "gravity-unit", "temperature-unit", "missing-sg", "nan-sg",
    "bool-sg", "huge-sg", "temperature", "bool-temperature", "naive", "timestamp",
    "remainder", "bool-remainder", "reading-id", "duplicate-id", "duplicate-time",
])
def test_adapter_output_rejected_before_evaluator(setup, monkeypatch, case):
    readings = cli.adapt_brewforge_readings(
        payload(), brew_id=BREW, gravity_unit="sg", temperature_unit="c",
    )
    first, last = readings
    changes = {
        "source": {"source": "rapt"}, "all-source": {"source": "rapt"},
        "kind": {"device_kind": DeviceKind.TEMPERATURE_CONTROLLER},
        "all-kind": {"device_kind": DeviceKind.TEMPERATURE_CONTROLLER},
        "id": {"device_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        "all-id": {"device_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        "gravity-unit": {"gravity_unit": None}, "temperature-unit": {"temperature_unit": "f"},
        "missing-sg": {"gravity_raw": None}, "nan-sg": {"gravity_raw": float("nan")},
        "bool-sg": {"gravity_raw": True}, "huge-sg": {"gravity_raw": 1018},
        "temperature": {"temperature_c": float("inf")},
        "bool-temperature": {"temperature_c": True},
        "naive": {"observed_at": datetime(2026, 9, 3)},
        "timestamp": {"observed_at": "private-secret"},
        "remainder": {"observed_at_submicrosecond_ns": 1},
        "bool-remainder": {"observed_at_submicrosecond_ns": True},
        "reading-id": {"reading_id": "\nprivate-secret"},
        "duplicate-id": {"reading_id": first.reading_id},
        "duplicate-time": {"observed_at": first.observed_at},
    }
    if case in changes:
        readings = (replace(first, **changes[case]) if case.startswith("all-") else first,
                    replace(last, **changes[case]))
    else:
        readings = {"empty": (), "list": list(readings), "none": None,
                    "object": (object(),)}[case]
    monkeypatch.setattr(cli, "adapt_brewforge_readings", lambda *a, **kw: readings)
    evaluator = Mock(wraps=cli.explain_telemetry_sg)
    monkeypatch.setattr(cli, "explain_telemetry_sg", evaluator)
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "BrewForge SG diagnostic failed: request or response is invalid.\n"
    evaluator.assert_not_called()


@pytest.mark.parametrize("stage", ["client", "get", "adapter"])
@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("private-secret raw HTTP payload"),
    httpx.HTTPStatusError("private-secret", request=httpx.Request("GET", "https://example.test"),
                         response=httpx.Response(500)),
    ValueError("private-secret"), TypeError("private-secret"), OSError("private-secret"),
    RecursionError("private-secret"),
])
def test_sanitized_api_and_adapter_failures(setup, monkeypatch, stage, error):
    evaluator = Mock()
    monkeypatch.setattr(cli, "explain_telemetry_sg", evaluator)
    failing = Mock(side_effect=error)
    if stage == "client":
        monkeypatch.setattr(cli, "BrewForgeClient", failing)
    elif stage == "get":
        monkeypatch.setattr(cli, "BrewForgeClient", Mock(return_value=Mock(get=failing)))
    else:
        monkeypatch.setattr(cli, "adapt_brewforge_readings", failing)
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "BrewForge SG diagnostic failed: request or response is invalid.\n"
    evaluator.assert_not_called()


@pytest.mark.parametrize("case", ["missing", "empty", "invalid", "missing-sg"])
def test_bad_payload_fails_closed(setup, monkeypatch, case):
    response = setup[3]
    if case == "missing":
        response.clear()
    elif case == "empty":
        response["data"] = []
    elif case == "invalid":
        response["data"][0]["timestamp"] = "private-secret"
    else:
        del response["data"][0]["gravity"]
    evaluator = Mock(wraps=cli.explain_telemetry_sg)
    monkeypatch.setattr(cli, "explain_telemetry_sg", evaluator)
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "BrewForge SG diagnostic failed: request or response is invalid.\n"
    evaluator.assert_not_called()


def test_command_documentation_has_explicit_units_and_scope():
    from pathlib import Path

    text = Path("docs/COMMANDS.md").read_text(encoding="utf-8")
    assert "vessel sg-diagnose-brewforge" in text
    assert "--gravity-unit sg --temperature-unit c" in text
    assert "exactly one `GET /brews/{id}/readings`" in text


@pytest.mark.parametrize(("case", "status", "reason"), [
    ("insufficient", "NO_DECISION", "INSUFFICIENT_CONFIRMATIONS"),
    ("stale", "NO_DECISION", "STALE"),
    ("gap", "NO_DECISION", "GAP_EXCEEDED"),
    ("wait", "WAIT", "ABOVE_TRIGGER"),
    ("equal", "CONDITION_MET", "AT_OR_BELOW_TRIGGER"),
])
def test_core_quality_results_exact_thresholds(setup, monkeypatch, case, status, reason):
    response = setup[3]
    command = args()
    if case == "insufficient":
        response["data"].pop()
    elif case == "stale":
        # Latest age is 30 minutes plus 100 ns: just beyond the inclusive limit.
        command[command.index("--end") + 1] = "2026-09-03T01:30:00.0000002Z"
    elif case == "gap":
        # Gap is 30 minutes plus 100 ns: just beyond the inclusive limit.
        response["data"][0]["timestamp"] = "2026-09-03T00:30:00Z"
    elif case == "wait":
        response["data"][0]["gravity"] = 1.0200001
    else:
        response["data"][0]["gravity"] = 1.020
        command[command.index("--end") + 1] = "2026-09-03T01:30:00.0000001Z"
    evaluator = Mock(wraps=cli.explain_telemetry_sg)
    monkeypatch.setattr(cli, "explain_telemetry_sg", evaluator)
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert f"status={status} reason={reason}" in result.stdout
    assert "Candidates are not quality-approved confirmations." in result.stdout
    evaluator.assert_called_once()


def test_inclusive_endpoints_and_timezone_equivalence(setup):
    command = args()
    command[command.index("--start") + 1] = "2026-09-03T00:30:00.0000001Z"
    utc = runner.invoke(app, command)
    command[command.index("--start") + 1] = "2026-09-03T02:30:00.0000001+02:00"
    command[command.index("--end") + 1] = "2026-09-03T03:00:00.0000001+02:00"
    offset = runner.invoke(app, command)
    assert utc.exit_code == offset.exit_code == 0
    assert utc.stdout == offset.stdout
    assert "latest_age_ns=0" in utc.stdout


@pytest.mark.parametrize(("end", "code"), [
    ("2026-09-10T00:00:00.0000001Z", 0),
    ("2026-09-10T00:00:00.0000002Z", 1),
])
def test_seven_day_exact_limit(setup, end, code):
    command = args()
    command[command.index("--end") + 1] = end
    result = runner.invoke(app, command)
    assert result.exit_code == code
    if code:
        assert setup[2] == []
        assert result.stdout == ""


def test_submicrosecond_window(setup):
    response = setup[3]
    response["data"][0]["timestamp"] = "2026-09-03T01:00:00Z"
    command = args()
    command[command.index("--start") + 1] = "2026-09-03T01:00:00Z"
    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    assert "largest_confirmation_gap_ns=100" in result.stdout


@pytest.mark.parametrize(("case", "code"), [("missing", 2), ("store-error", 1)])
def test_shared_brewforge_credential_errors(tmp_path, monkeypatch, case, code):
    from forge_companion import credentials

    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    path = tmp_path / "fermentation-contexts.json"
    document = json.loads(path.read_text())
    document["contexts"][0]["brewforge_brew_id"] = BREW
    path.write_text(json.dumps(document))
    resolver = Mock(return_value=credentials.ResolvedToken(None, "missing"))
    if case == "store-error":
        resolver.side_effect = credentials.CredentialStoreError("private-secret")
    monkeypatch.setattr(credentials, "resolve_token", resolver)
    client = Mock()
    monkeypatch.setattr(cli, "BrewForgeClient", client)
    result = runner.invoke(app, args())
    assert result.exit_code == code
    assert result.stdout == ""
    assert result.stderr and "private-secret" not in result.stderr
    resolver.assert_called_once()
    client.assert_not_called()


def test_no_rapt_binding_clock_or_persistence_access(setup, monkeypatch):
    spies = []
    for name in ("_utc_now", "load_vessels", "_profile_for_api", "RaptClient",
                 "start_fermentation_context", "start_brewforge_fermentation_context",
                 "append_fermentation_phase", "close_fermentation_context"):
        spy = Mock(side_effect=AssertionError(f"unexpected {name}"))
        monkeypatch.setattr(cli, name, spy)
        spies.append(spy)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    for spy in spies:
        spy.assert_not_called()


def test_preflight_event_order(setup, monkeypatch):
    events = setup[2]
    for name in ("_utc_timestamp", "_validated_max_age", "load_fermentation_contexts",
                 "context_phase_history", "_canonical_brew_id"):
        original = getattr(cli, name)

        def record(*a, _name=name, _original=original, **kw):
            events.append(_name)
            return _original(*a, **kw)

        monkeypatch.setattr(cli, name, record)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert events[:8] == [
        "_utc_timestamp", "_utc_timestamp", "_validated_max_age", "_validated_max_age",
        "load_fermentation_contexts", "context_phase_history", "_canonical_brew_id", "token",
    ]


def test_ordering_spies_kill_early_credentials_mutation(tmp_path):
    """Run the real CLI tests against a scratch source tree with one early token call."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("conftest.py", "test_vessel_sg_trend_cli.py", Path(__file__).name):
        shutil.copyfile(root / "tests" / name, tests / name)
    module = tmp_path / "src/forge_companion/cli_vessel.py"
    text = module.read_text(encoding="utf-8")
    marker = '    """EXPERIMENTAL read-only BrewForge SG diagnostics; never permission to act."""\n'
    assert text.count(marker) == 1
    module.write_text(text.replace(marker, marker + "    _token_for_api()  # MUTANT\n"),
                      encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=str(tmp_path / "src"), PYTHONNOUSERSITE="1")
    environment.pop("PYTHONHOME", None)
    command = [sys.executable, "-m", "pytest", str(tests / Path(__file__).name),
               "-k", "context_preflight_before_token_and_client or "
               "policy_preflight_before_token_and_client", "-q", "--tb=short"]
    completed = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True,
                               text=True, timeout=60, check=False)
    print("Scratch mutation: insert _token_for_api() before the first preflight try")
    print("Command:", subprocess.list2cmdline(command))
    print(completed.stdout, end="")
    print(completed.stderr, end="")
    print("Mutation exit code:", completed.returncode)
    assert completed.returncode == 1
    assert "failed" in completed.stdout
    assert "assert ['token'] == []" in completed.stdout
