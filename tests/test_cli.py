import json
from pathlib import Path

from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


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
