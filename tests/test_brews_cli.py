import httpx
import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


def test_brews_command_uses_one_get_and_lists_name_and_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[tuple[str, object]] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append((path, params))
            return {
                "data": [{"id": brew_id, "name": "Example Wit"}],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [("brews", {"page": 1, "limit": 100})]
    assert "Example Wit" in result.output
    assert brew_id in result.output
    assert "test-token" not in result.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--page", "0"],
        ["--limit", "0"],
        ["--limit", "101"],
    ],
)
def test_brews_command_rejects_invalid_page_or_limit_before_api_call(
    arguments: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingClient:
        def __init__(self, token: str) -> None:
            raise AssertionError("client must not be created")

    monkeypatch.setattr(cli, "BrewForgeClient", ExplodingClient)

    result = runner.invoke(
        app,
        ["brews", *arguments],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 2


def test_brews_command_reports_next_page_without_fetching_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[tuple[str, object]] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            calls.append((path, params))
            return {
                "data": [{"id": brew_id, "name": "Page Two Brew"}],
                "pagination": {"hasMore": True, "total": 75},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews", "--page", "2", "--limit", "25"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert calls == [("brews", {"page": 2, "limit": 25})]
    assert "Page Two Brew" in result.output
    assert "More brews available: rerun with --page 3." in result.output


def test_brews_command_does_not_emit_partial_list_for_malformed_brew(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [
                    {"id": valid_id, "name": "Must Not Leak As Partial Success"},
                    {"id": "not-a-uuid", "name": "Broken Brew"},
                ],
                "pagination": {"hasMore": False, "total": 2},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "Brew list failed:" in result.output
    assert "Must Not Leak As Partial Success" not in result.output
    assert "Traceback" not in result.output


def test_brews_command_sanitizes_untrusted_brew_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [
                    {
                        "id": brew_id,
                        "name": "Danger\x1b[31m\n\u202eInjected",
                    }
                ],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert "\x1b" not in result.output
    assert "\u202e" not in result.output
    assert "Danger Injected" in result.output
    assert result.output.count("\n") == 1


@pytest.mark.parametrize("invalid_total", [True, -1, "1"])
def test_brews_command_rejects_invalid_pagination_total(
    invalid_total: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [],
                "pagination": {"hasMore": False, "total": invalid_total},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "pagination.total must be a non-negative integer" in result.output
    assert "Traceback" not in result.output


def test_brews_command_reports_an_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [],
                "pagination": {"hasMore": False, "total": 0},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews", "--page", "3"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert result.output == "No brews found on page 3.\n"


def test_brews_command_rejects_empty_page_when_has_more_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [],
                "pagination": {"hasMore": True, "total": 10},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "pagination made no progress while hasMore is true" in result.output
    assert "More brews available" not in result.output


def test_brews_command_rejects_name_empty_after_sanitization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [{"id": brew_id, "name": "\u202e\x00"}],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "brew name is empty after terminal sanitization" in result.output
    assert brew_id not in result.output


def test_brews_command_does_not_echo_token_from_api_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "test-token-secret"

    class BrokenClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token-secret"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m")

    monkeypatch.setattr(cli, "BrewForgeClient", BrokenClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": token},
    )

    assert result.exit_code == 1
    assert result.output == "Brew list failed: API request failed.\n"
    assert token not in result.output
    assert "\x1b" not in result.output


def test_brews_command_uses_placeholder_for_empty_api_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [{"id": brew_id, "name": ""}],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert result.output == f"<unnamed brew> | {brew_id}\n"


@pytest.mark.parametrize("invalid_name", [None, 42])
def test_brews_command_rejects_present_non_string_api_name(
    invalid_name: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [{"id": brew_id, "name": invalid_name}],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 1
    assert "brew name is not a string" in result.output
    assert brew_id not in result.output


def test_brews_command_uses_placeholder_for_missing_api_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, token: str) -> None:
            assert token == "test-token"

        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [{"id": brew_id}],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["brews"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0
    assert result.output == f"<unnamed brew> | {brew_id}\n"
