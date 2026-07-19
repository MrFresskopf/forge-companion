from pathlib import Path

import httpx
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


def test_fermentation_csv_uses_one_get_and_writes_chronological_rows(
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
            return {
                "data": [
                    {
                        "id": "reading-late",
                        "timestamp": "2026-07-17T09:00:00+00:00",
                        "gravity": 1.011,
                        "temperature": 29.5,
                        "pressure": 1.2,
                        "ph": None,
                        "comment": "later, stable",
                    },
                    {
                        "id": "reading-early",
                        "timestamp": "2026-07-17T08:00:00Z",
                        "gravity": 1.0123,
                        "temperature": None,
                        "pressure": None,
                        "ph": 4.2,
                        "comment": None,
                    },
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "readings.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", brew_id, "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [f"brews/{brew_id}/readings"]
    assert destination.read_text(encoding="utf-8") == (
        "id,timestamp_utc,gravity_sg,temperature_raw,pressure,ph,comment\n"
        "reading-early,2026-07-17T08:00:00Z,1.0123,,,4.2,\n"
        'reading-late,2026-07-17T09:00:00Z,1.011,29.5,1.2,,"later, stable"\n'
    )
    assert f"2 readings written to {destination}" in result.output
    assert "test-token" not in result.output
    assert "test-token" not in destination.read_text(encoding="utf-8")


def test_fermentation_csv_does_not_write_when_no_reading_is_valid(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": [{"id": "bad", "timestamp": "invalid", "gravity": 1.012}]}

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "must-not-exist.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", brew_id, "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "no valid fermentation readings" in result.output
    assert not destination.exists()


def test_fermentation_csv_reports_rejected_records_without_hiding_valid_rows(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [
                    {"id": "bad", "timestamp": "invalid", "gravity": 1.013},
                    {
                        "id": "good",
                        "timestamp": "2026-07-17T08:00:00Z",
                        "gravity": 1.012,
                    },
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "readings.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", brew_id, "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert "1 readings written" in result.output
    assert "1 rejected" in result.output
    assert "0 conflicting timestamps" in result.output
    assert "good" in destination.read_text(encoding="utf-8")
    assert "bad" not in destination.read_text(encoding="utf-8")


def test_fermentation_csv_rejects_invalid_uuid_before_creating_client(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    class UnexpectedClient:
        def __init__(self, token: str) -> None:
            raise AssertionError("client must not be created")

    monkeypatch.setattr(cli, "BrewForgeClient", UnexpectedClient)
    destination = tmp_path / "must-not-exist.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", "not-a-uuid", "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "Fermentation CSV failed" in result.output
    assert not destination.exists()


def test_fermentation_csv_does_not_echo_token_from_api_exception(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    token = "test-token-secret"

    class BrokenClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token-secret"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)
    destination = tmp_path / "must-not-exist.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", brew_id, "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": token},
    )

    assert result.exit_code == 1
    assert result.output == "Fermentation CSV failed: API request failed.\n"
    assert token not in result.output
    assert "\x1b" not in result.output
    assert not destination.exists()


def test_fermentation_csv_sanitizes_destination_in_terminal_output(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [
                    {
                        "id": "reading-1",
                        "timestamp": "2026-07-17T08:00:00Z",
                        "gravity": 1.012,
                    }
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "readings\u202eINJECTED.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", brew_id, "--output", str(destination)],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert destination.exists()
    assert result.output.count("\n") == 1
    assert "\u202e" not in result.output
    assert "readings INJECTED.csv" in result.output
