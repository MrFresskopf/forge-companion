import httpx
import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()
BREW_ID = "54d34560-f1af-49f0-9a26-6caca3397f75"
COMMANDS = [
    ("fermentation-brief", []),
    ("fermentation-csv", []),
    ("spunding-advisor", ["--trigger-sg", "1.012"]),
]


@pytest.mark.parametrize(("command", "required_options"), COMMANDS)
def test_shared_selection_requires_uuid_or_select_before_client(
    monkeypatch, command: str, required_options: list[str]
) -> None:
    class ForbiddenClient:
        def __init__(self, *, token: str) -> None:
            raise AssertionError(f"client must not be created: {token}")

    monkeypatch.setattr(cli, "BrewForgeClient", ForbiddenClient)

    result = runner.invoke(app, [command, *required_options])

    assert result.exit_code == 1
    assert "provide a brew UUID or --select" in result.output


@pytest.mark.parametrize(("command", "required_options"), COMMANDS)
def test_shared_selection_rejects_uuid_with_select_before_client(
    monkeypatch, command: str, required_options: list[str]
) -> None:
    class ForbiddenClient:
        def __init__(self, *, token: str) -> None:
            raise AssertionError(f"client must not be created: {token}")

    monkeypatch.setattr(cli, "BrewForgeClient", ForbiddenClient)

    result = runner.invoke(
        app,
        [command, BREW_ID, *required_options, "--select"],
    )

    assert result.exit_code == 1
    assert "brew UUID and --select cannot be used together" in result.output


@pytest.mark.parametrize(("command", "required_options"), COMMANDS)
def test_shared_selection_rejects_pagination_without_select_before_client(
    monkeypatch, command: str, required_options: list[str]
) -> None:
    class ForbiddenClient:
        def __init__(self, *, token: str) -> None:
            raise AssertionError(f"client must not be created: {token}")

    monkeypatch.setattr(cli, "BrewForgeClient", ForbiddenClient)

    result = runner.invoke(
        app,
        [command, BREW_ID, *required_options, "--page", "2"],
    )

    assert result.exit_code == 1
    assert "--page and --limit require --select" in result.output


@pytest.mark.parametrize(
    ("command", "required_options", "expected_error"),
    [
        ("fermentation-brief", [], "Fermentation brief failed: API request failed."),
        ("fermentation-csv", [], "Fermentation CSV failed: API request failed."),
        (
            "spunding-advisor",
            ["--trigger-sg", "1.012"],
            "Spunding advisor failed: API request failed.",
        ),
    ],
)
def test_shared_selection_hides_token_from_list_transport_error(
    monkeypatch,
    command: str,
    required_options: list[str],
    expected_error: str,
) -> None:
    token = "bfk_shared_select_secret"

    class FailingClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_shared_select_secret"

        def get(self, path: str, params=None):
            assert path == "brews"
            request = httpx.Request("GET", "https://example.invalid/brews")
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m", request=request)

    monkeypatch.setattr(cli, "BrewForgeClient", FailingClient)

    result = runner.invoke(
        app,
        [command, *required_options, "--select"],
        env={"BREWFORGE_API_TOKEN": token},
    )

    assert result.exit_code == 1
    assert expected_error in result.output
    assert token not in result.output
    assert "\x1b" not in result.output


def test_shared_selection_non_integer_then_eof_never_requests_readings(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params=None) -> dict[str, object]:
            calls.append(path)
            if path != "brews":
                raise AssertionError("readings must not be requested")
            return {
                "data": [{"id": BREW_ID, "name": "Not selected"}],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "must-not-exist.csv"

    result = runner.invoke(
        app,
        ["fermentation-csv", "--select", "--output", str(destination)],
        input="not-a-number\n",
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert calls == ["brews"]
    assert not destination.exists()
    assert "Traceback" not in result.output
