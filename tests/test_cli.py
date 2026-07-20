import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion import __version__
from forge_companion.cli import app

runner = CliRunner()


def test_version_option_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output == f"Forge Companion {__version__}\n"


def test_help_groups_commands_by_user_goal() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Start here" in result.output
    assert "Reports and exports" in result.output
    assert "Safety experiments" in result.output


def test_doctor_requires_token_without_printing_secrets() -> None:
    result = runner.invoke(app, ["doctor"], env={"BREWFORGE_API_TOKEN": ""})

    assert result.exit_code == 2
    assert "BREWFORGE_API_TOKEN is not set" in result.output
    assert "bfk_" not in result.output


def test_backup_command_writes_file_and_reports_destination(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": [], "pagination": {"hasMore": False, "total": 0}}

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "brewforge.json"

    result = runner.invoke(
        app,
        ["snapshot", "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert destination.exists()
    assert str(destination) in result.output
    assert "test-token" not in destination.read_text(encoding="utf-8")


def test_backup_command_reports_api_error_without_traceback(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    class BrokenClient:
        def __init__(self, token: str) -> None:
            pass

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise ValueError("unexpected response")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)
    destination = tmp_path / "must-not-exist.json"

    result = runner.invoke(
        app,
        ["snapshot", "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "Snapshot failed: unexpected response" in result.output
    assert "Traceback" not in result.output
    assert not destination.exists()


def test_snapshot_does_not_echo_token_from_transport_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-token-secret"

    class BrokenClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token-secret"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)
    destination = tmp_path / "must-not-exist.json"

    result = runner.invoke(
        app,
        ["snapshot", "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": token},
    )

    assert result.exit_code == 1
    assert result.output == "Snapshot failed: API request failed.\n"
    assert token not in result.output
    assert "\x1b" not in result.output
    assert not destination.exists()


def test_inventory_audit_command_reports_findings_from_snapshot(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            {
                "format": "forge-companion-collection-snapshot-v1",
                "resources": {
                    "inventory_yeasts": [
                        {
                            "id": "yeast-1",
                            "name": "Example Yeast",
                            "quantity": 1,
                            "quantityUnit": "pkg",
                            "expiryDate": "2026-07-01",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["inventory-audit", str(snapshot), "--as-of", "2026-07-17"],
    )

    assert result.exit_code == 0
    assert "1 finding(s)" in result.output
    assert "WARNING yeasts Example Yeast: expired on 2026-07-01" in result.output


def test_fermentation_brief_uses_exactly_two_gets_and_writes_report(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[str] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append(path)
            if path == f"brews/{brew_id}":
                return {"id": brew_id, "name": "Example Wit"}
            if path == f"brews/{brew_id}/readings":
                return {
                    "data": [
                        {
                            "id": "reading-1",
                            "timestamp": "2026-07-17T08:00:00Z",
                            "gravity": 1.012,
                            "temperature": 29.0,
                        }
                    ]
                }
            raise AssertionError(f"unexpected GET: {path}")

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "brief.md"

    result = runner.invoke(
        app,
        [
            "fermentation-brief",
            brew_id,
            "--output",
            str(destination),
            "--temperature-unit",
            "C",
        ],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [f"brews/{brew_id}", f"brews/{brew_id}/readings"]
    assert destination.exists()
    report = destination.read_text(encoding="utf-8")
    assert "# Fermentation Brief: Example Wit" in report
    assert "test-token" not in report
    assert str(destination) in result.output


def test_fermentation_brief_selects_brew_without_detail_request(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[tuple[str, object]] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append((path, params))
            if path == "brews":
                return {
                    "data": [{"id": brew_id, "name": "Selected Brief Brew"}],
                    "pagination": {"hasMore": False, "total": 1},
                }
            if path == f"brews/{brew_id}/readings":
                return {
                    "data": [
                        {
                            "id": "reading",
                            "timestamp": "2026-07-17T08:00:00Z",
                            "gravity": 1.012,
                        }
                    ]
                }
            raise AssertionError(f"unexpected GET: {path}")

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "selected-brief.md"

    result = runner.invoke(
        app,
        [
            "fermentation-brief",
            "--select",
            "--output",
            str(destination),
        ],
        input="1\n",
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [
        ("brews", {"page": 1, "limit": 100}),
        (f"brews/{brew_id}/readings", None),
    ]
    assert "1  Selected Brief Brew" in result.output
    assert "# Fermentation Brief: Selected Brief Brew" in destination.read_text(encoding="utf-8")


def test_fermentation_brief_does_not_echo_token_from_transport_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    token = "test-token-secret"

    class BrokenClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token-secret"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)
    destination = tmp_path / "must-not-exist.md"

    result = runner.invoke(
        app,
        ["fermentation-brief", brew_id, "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": token},
    )

    assert result.exit_code == 1
    assert result.output == "Fermentation brief failed: API request failed.\n"
    assert token not in result.output
    assert "\x1b" not in result.output
    assert not destination.exists()


def test_spunding_advisor_uses_one_readings_get(monkeypatch: object) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[str] = []
    now = datetime.now(UTC)

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append(path)
            return {
                "data": [
                    {
                        "id": "reading-1",
                        "timestamp": (now - timedelta(hours=1)).isoformat(),
                        "gravity": 1.0119,
                    },
                    {
                        "id": "reading-2",
                        "timestamp": now.isoformat(),
                        "gravity": 1.0117,
                    },
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["spunding-advisor", brew_id, "--trigger-sg", "1.012"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [f"brews/{brew_id}/readings"]
    assert "Spunding advisor: CONDITION_MET" in result.output
    assert "Simulation only: no device command was sent." in result.output
    assert "test-token" not in result.output


def test_spunding_advisor_selects_brew_before_readings(monkeypatch: object) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[tuple[str, object]] = []
    now = datetime.now(UTC)

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append((path, params))
            if path == "brews":
                return {
                    "data": [{"id": brew_id, "name": "Selected Advisor Brew"}],
                    "pagination": {"hasMore": False, "total": 1},
                }
            if path == f"brews/{brew_id}/readings":
                return {
                    "data": [
                        {
                            "id": "reading-1",
                            "timestamp": (now - timedelta(hours=1)).isoformat(),
                            "gravity": 1.0119,
                        },
                        {
                            "id": "reading-2",
                            "timestamp": now.isoformat(),
                            "gravity": 1.0117,
                        },
                    ]
                }
            raise AssertionError(f"unexpected GET: {path}")

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["spunding-advisor", "--select", "--trigger-sg", "1.012"],
        input="1\n",
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [
        ("brews", {"page": 1, "limit": 100}),
        (f"brews/{brew_id}/readings", None),
    ]
    assert "1  Selected Advisor Brew" in result.output
    assert "Spunding advisor: CONDITION_MET" in result.output


def test_spunding_advisor_renders_no_decision_for_malformed_envelope(
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[str] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append(path)
            return {"unexpected": []}

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["spunding-advisor", brew_id, "--trigger-sg", "1.012"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [f"brews/{brew_id}/readings"]
    assert "Spunding advisor: NO_DECISION" in result.output
    assert "Reason: readings response is malformed" in result.output
    assert "Spunding advisor failed" not in result.output


def test_spunding_advisor_reports_api_error_without_traceback(monkeypatch: object) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class BrokenClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise ValueError("unexpected response")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)

    result = runner.invoke(
        app,
        ["spunding-advisor", brew_id, "--trigger-sg", "1.012"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "Spunding advisor failed: unexpected response" in result.output
    assert "Traceback" not in result.output
    assert "test-token" not in result.output


def test_spunding_advisor_does_not_echo_token_from_transport_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    token = "test-token-secret"

    class BrokenClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token-secret"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)

    result = runner.invoke(
        app,
        ["spunding-advisor", brew_id, "--trigger-sg", "1.012"],
        env={"BREWFORGE_API_TOKEN": token},
    )

    assert result.exit_code == 1
    assert result.output == "Spunding advisor failed: API request failed.\n"
    assert token not in result.output
    assert "\x1b" not in result.output


def test_spunding_advisor_renders_no_decision_for_timestamp_overflow(
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[str] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append(path)
            return {
                "data": [
                    {
                        "id": "boundary",
                        "timestamp": "0001-01-01T00:00:00+23:59",
                        "gravity": 1.010,
                    }
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["spunding-advisor", brew_id, "--trigger-sg", "1.012"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [f"brews/{brew_id}/readings"]
    assert "Spunding advisor: NO_DECISION" in result.output
    assert "Spunding advisor failed" not in result.output
