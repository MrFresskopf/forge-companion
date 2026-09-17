"""Contract tests for experimental side-by-side RAPT/BrewForge progress evidence."""

import json
from dataclasses import replace
from unittest.mock import Mock

import httpx
import pytest
from test_vessel_sg_diagnose_cli import rows
from test_vessel_sg_trend_cli import local_state
from typer.testing import CliRunner

import forge_companion.cli_vessel as cli
from forge_companion.cli import app

BREW = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
runner = CliRunner()


def args():
    return [
        "vessel", "compare-progress", "tank",
        "--start", "2026-09-03T00:30:00.0000001Z",
        "--end", "2026-09-03T01:00:00.0000001Z",
        "--gravity-unit", "sg", "--temperature-unit", "c",
    ]


def brew_payload(values=(1.019, 1.018)):
    data = [
        {"id": "first", "timestamp": "2026-09-03T00:30:00.0000001Z", "gravity": values[0]},
    ]
    if len(values) > 1:
        data.append(
            {"id": "last", "timestamp": "2026-09-03T01:00:00.0000001Z", "gravity": values[1]}
        )
    return {"data": data}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    local_state(tmp_path)
    path = tmp_path / "fermentation-contexts.json"
    document = json.loads(path.read_text())
    document["contexts"][0]["brewforge_brew_id"] = BREW
    path.write_text(json.dumps(document))
    events = []

    def profile():
        events.append("rapt-profile")
        return object()

    def token():
        events.append("brewforge-token")
        return "synthetic-token"

    class Client:
        def __init__(self, **kwargs):
            events.append(("brewforge-client", kwargs))

        def get(self, path):
            events.append(("brewforge-get", path))
            return brew_payload()

    monkeypatch.setattr(cli, "_profile_for_api", profile)
    monkeypatch.setattr(cli, "_token_for_api", token)
    monkeypatch.setattr(cli, "BrewForgeClient", Client)
    monkeypatch.setattr(cli, "_read_hydrometer", lambda **kwargs: rows())
    return path, document, events


def test_side_by_side_uses_common_evaluator_once_per_source_and_no_writes(
    setup, monkeypatch, tmp_path
):
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    evaluator = Mock(wraps=cli.evaluate_fermentation_progress)
    monkeypatch.setattr(cli, "evaluate_fermentation_progress", evaluator)

    result = runner.invoke(app, args())

    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert setup[2] == [
        "rapt-profile", "brewforge-token", ("brewforge-client", {"token": "synthetic-token"}),
        ("brewforge-get", f"brews/{BREW}/readings"),
    ]
    assert evaluator.call_count == 2
    assert {call.args[0][0].source for call in evaluator.call_args_list} == {"rapt", "brewforge"}
    assert all(call.kwargs["original_gravity"] == "1.050" for call in evaluator.call_args_list)
    assert "RAPT_PROGRESS_BEGIN" in result.stdout
    assert "BREWFORGE_PROGRESS_BEGIN" in result.stdout
    assert "source=rapt stream=RAPT hydrometer device" in result.stdout
    assert "source=brewforge stream=BrewForge stored-brew stream" in result.stdout
    assert "first_at=2026-09-03T00:30:00.0000001+00:00" in result.stdout
    assert "latest_at=2026-09-03T01:00:00.0000001+00:00" in result.stdout
    assert (
        "observational/window averages, not forecast, freshness, calibration, completion"
        in result.stdout
    )
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


@pytest.mark.parametrize(("source", "values", "block"), [
    ("rapt", (1019,), "RAPT_PROGRESS_NO_DATA"),
    ("brewforge", (1.019,), "BREWFORGE_PROGRESS_NO_DATA"),
])
def test_fewer_than_two_valid_selected_observations_is_bounded_no_data(
    setup, monkeypatch, source, values, block
):
    if source == "rapt":
        monkeypatch.setattr(cli, "_read_hydrometer", lambda **kwargs: rows()[:1])
    else:
        class Client:
            def __init__(self, **kwargs):
                pass
            def get(self, path):
                return brew_payload(values)
        monkeypatch.setattr(cli, "BrewForgeClient", Client)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert block in result.stdout
    assert "NO_DATA" in result.stdout


def test_mismatch_is_separate_without_winner_or_delta(setup, monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass
        def get(self, path):
            return brew_payload((1.017, 1.016))
    monkeypatch.setattr(cli, "BrewForgeClient", Client)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert "latest_sg=1.018" in result.stdout
    assert "latest_sg=1.016" in result.stdout
    assert "winner" not in result.stdout.lower()
    assert "delta" not in result.stdout.lower()


@pytest.mark.parametrize(("flag", "value"), [
    ("--gravity-unit", "SG"), ("--temperature-unit", "f"),
    ("--start", "2026-09-03T00:30:00"),
    ("--end", "2026-09-03T01:00:00.00000001Z"),
])
def test_preflight_refuses_units_or_window_before_both_credential_families(setup, flag, value):
    command = args()
    command[command.index(flag) + 1] = value
    result = runner.invoke(app, command)
    assert result.exit_code == 1
    assert result.stdout == ""
    assert setup[2] == []


def test_preflight_refuses_noncanonical_stored_original_gravity_before_credentials(setup):
    path, document, events = setup
    document["contexts"][0]["original_gravity_sg"] = "1.０５０"
    path.write_text(json.dumps(document))
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert events == []


def test_bad_source_aborts_atomically_after_other_source_succeeds(setup, monkeypatch):
    class Client:
        def __init__(self, **kwargs):
            pass
        def get(self, path):
            raise httpx.ReadTimeout("private-token")
    monkeypatch.setattr(cli, "BrewForgeClient", Client)
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Vessel progress comparison failed: request or response is invalid.\n"


def test_exact_inclusive_100ns_endpoints_are_selected(setup, monkeypatch):
    raw = rows()
    monkeypatch.setattr(cli, "_read_hydrometer", lambda **kwargs: raw)
    evaluator = Mock(wraps=cli.evaluate_fermentation_progress)
    monkeypatch.setattr(cli, "evaluate_fermentation_progress", evaluator)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    rapt_call = next(call for call in evaluator.call_args_list if call.args[0][0].source == "rapt")
    assert tuple(item.observed_at_exact for item in rapt_call.args[0]) == (
        "2026-09-03T00:30:00.0000001+00:00", "2026-09-03T00:59:00.0000001+00:00"
    )


def test_rapt_transport_window_rounds_exact_end_outward_before_filtering(setup, monkeypatch):
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return rows()

    monkeypatch.setattr(cli, "_read_hydrometer", read)
    result = runner.invoke(app, args())
    assert result.exit_code == 0, result.output
    assert calls[0]["start"].isoformat() == "2026-09-03T00:30:00+00:00"
    assert calls[0]["end"].isoformat() == "2026-09-03T01:00:00.000001+00:00"


def test_malformed_rapt_stream_aborts_before_brewforge_token(setup, monkeypatch):
    first, last = rows()
    monkeypatch.setattr(
        cli, "_read_hydrometer", lambda **kwargs: (first, replace(last, device_id="a" * 36))
    )
    result = runner.invoke(app, args())
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "brewforge-token" not in setup[2]


def test_recording_preflight_spies_fail_against_early_credential_scratch_mutation(tmp_path):
    """The ordering test is non-vacuous: an early RAPT profile call makes it red."""
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in (
        "conftest.py",
        "test_vessel_sg_trend_cli.py",
        "test_vessel_sg_diagnose_cli.py",
        Path(__file__).name,
    ):
        shutil.copyfile(root / "tests" / name, tests / name)
    module = tmp_path / "src/forge_companion/cli_vessel.py"
    text = module.read_text(encoding="utf-8")
    marker = '    """EXPERIMENTAL read-only separate RAPT and BrewForge progress observations."""\n'
    assert text.count(marker) == 1
    module.write_text(
        text.replace(marker, marker + "    _profile_for_api()  # MUTANT\n"), encoding="utf-8"
    )
    environment = dict(os.environ, PYTHONPATH=str(tmp_path / "src"), PYTHONNOUSERSITE="1")
    environment.pop("PYTHONHOME", None)
    completed = subprocess.run(
        [
            sys.executable, "-m", "pytest", str(tests / Path(__file__).name),
            "-k", "preflight_refuses_units_or_window", "-q", "--tb=short",
        ],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert completed.returncode == 1
    assert "assert ['rapt-profile'] == []" in completed.stdout
