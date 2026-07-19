from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


def test_fermentation_html_uses_one_get_and_writes_standalone_report(
    monkeypatch, tmp_path: Path
) -> None:
    token = "bfk_test_html_token"
    monkeypatch.setenv("BREWFORGE_API_TOKEN", token)
    calls: list[tuple[str, dict[str, object] | None]] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_html_token"

        def get(self, path: str, params: dict[str, object] | None = None) -> dict[str, object]:
            calls.append((path, params))
            return {
                "data": [
                    {
                        "id": "one",
                        "timestamp": "2026-07-18T08:00:00Z",
                        "gravity": 1.05,
                        "temperature": 29.0,
                    },
                    {
                        "id": "two",
                        "timestamp": "2026-07-18T20:00:00Z",
                        "gravity": 1.03,
                        "temperature": 29.5,
                    },
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "fermentation.html"
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    result = runner.invoke(
        app,
        [
            "fermentation-html",
            brew_id,
            "--title",
            "Jovaru split batch",
            "--temperature-unit",
            "C",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert calls == [(f"brews/{brew_id}/readings", None)]
    document = destination.read_text(encoding="utf-8")
    assert "Fermentation Report: Jovaru split batch" in document
    assert "29.0–29.5 °C" in document
    assert "2 readings written" in result.output
    assert "0 rejected" in result.output
    assert "0 conflicting timestamps" in result.output
    assert token not in result.output


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        (["not-a-uuid"], "badly formed hexadecimal UUID string"),
        (
            [
                "54d34560-f1af-49f0-9a26-6caca3397f75",
                "--temperature-unit",
                "K",
            ],
            "temperature unit must be C or F",
        ),
    ],
)
def test_fermentation_html_validates_before_client_creation(
    monkeypatch, arguments: list[str], expected_error: str
) -> None:
    class ForbiddenClient:
        def __init__(self, *, token: str) -> None:
            raise AssertionError(f"client must not be created: {token}")

    monkeypatch.setattr(cli, "BrewForgeClient", ForbiddenClient)

    result = runner.invoke(app, ["fermentation-html", *arguments])

    assert result.exit_code == 1
    assert expected_error in result.output


def test_fermentation_html_does_not_echo_token_from_http_error(monkeypatch) -> None:
    token = "bfk_secret_html_token"
    monkeypatch.setenv("BREWFORGE_API_TOKEN", token)

    class FailingClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_secret_html_token"

        def get(self, path: str, params=None):
            request = httpx.Request("GET", "https://example.invalid/readings")
            raise httpx.RequestError(f"failed with {token}\x1b[31m", request=request)

    monkeypatch.setattr(cli, "BrewForgeClient", FailingClient)

    result = runner.invoke(
        app,
        ["fermentation-html", "54d34560-f1af-49f0-9a26-6caca3397f75"],
    )

    assert result.exit_code == 1
    assert "Fermentation HTML failed: API request failed." in result.output
    assert token not in result.output
    assert "\x1b" not in result.output


def test_fermentation_html_does_not_write_malformed_payload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_html_token")

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_html_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            return {"data": "not-a-list"}

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "must-not-exist.html"

    result = runner.invoke(
        app,
        [
            "fermentation-html",
            "54d34560-f1af-49f0-9a26-6caca3397f75",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 1
    assert "readings response must contain a list-shaped data field" in result.output
    assert not destination.exists()


def test_fermentation_html_sanitizes_destination_in_terminal_output(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_html_token")

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_html_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            return {
                "data": [
                    {
                        "id": "one",
                        "timestamp": "2026-07-18T08:00:00Z",
                        "gravity": 1.04,
                    }
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "report\u202eINJECTED.html"

    result = runner.invoke(
        app,
        [
            "fermentation-html",
            "54d34560-f1af-49f0-9a26-6caca3397f75",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert destination.exists()
    assert "\u202e" not in result.output
    assert "report INJECTED.html" in result.output
