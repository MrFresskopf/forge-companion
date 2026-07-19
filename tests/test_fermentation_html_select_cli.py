import httpx
import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--select", "--page", "0"],
        ["--select", "--limit", "0"],
        ["--select", "--limit", "101"],
    ],
)
def test_fermentation_html_rejects_invalid_selection_page_before_client(
    monkeypatch, arguments: list[str]
) -> None:
    class ForbiddenClient:
        def __init__(self, *, token: str) -> None:
            raise AssertionError(f"client must not be created: {token}")

    monkeypatch.setattr(cli, "BrewForgeClient", ForbiddenClient)

    result = runner.invoke(app, ["fermentation-html", *arguments])

    assert result.exit_code == 2


def test_fermentation_html_select_does_not_echo_token_from_list_error(monkeypatch) -> None:
    token = "bfk_secret_select_token"
    monkeypatch.setenv("BREWFORGE_API_TOKEN", token)

    class FailingClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_secret_select_token"

        def get(self, path: str, params=None):
            request = httpx.Request("GET", "https://example.invalid/brews")
            raise httpx.RequestError(f"failed with {token}\x1b[31m", request=request)

    monkeypatch.setattr(cli, "BrewForgeClient", FailingClient)

    result = runner.invoke(app, ["fermentation-html", "--select"])

    assert result.exit_code == 1
    assert "Fermentation HTML failed: API request failed." in result.output
    assert token not in result.output
    assert "\x1b" not in result.output


@pytest.mark.parametrize("selection", ["0\n", "3\n"])
def test_fermentation_html_rejects_selection_outside_displayed_range(
    monkeypatch, selection: str
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")
    calls: list[str] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            calls.append(path)
            if path != "brews":
                raise AssertionError("readings must not be requested")
            return {
                "data": [
                    {
                        "id": "54d34560-f1af-49f0-9a26-6caca3397f75",
                        "name": "First",
                    },
                    {
                        "id": "d995e6f0-69ee-422a-a781-1dd08427563a",
                        "name": "Second",
                    },
                ],
                "pagination": {"hasMore": False, "total": 2},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(app, ["fermentation-html", "--select"], input=selection)

    assert result.exit_code == 1
    assert "brew number must be between 1 and 2" in result.output
    assert calls == ["brews"]


def test_fermentation_html_validates_entire_brew_page_before_printing(monkeypatch) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            assert path == "brews"
            return {
                "data": [
                    {
                        "id": "54d34560-f1af-49f0-9a26-6caca3397f75",
                        "name": "Safe first",
                    },
                    {
                        "id": "d995e6f0-69ee-422a-a781-1dd08427563a",
                        "name": 42,
                    },
                ],
                "pagination": {"hasMore": False, "total": 2},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(app, ["fermentation-html", "--select"])

    assert result.exit_code == 1
    assert "brew name is not a string" in result.output
    assert "Safe first" not in result.output


def test_fermentation_html_selects_explicit_page_and_reports_next_page(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[tuple[str, dict[str, object] | None]] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            calls.append((path, params))
            if path == "brews":
                return {
                    "data": [{"id": brew_id, "name": "Page two brew"}],
                    "pagination": {"hasMore": True, "total": 100},
                }
            if path == f"brews/{brew_id}/readings":
                return {
                    "data": [
                        {
                            "id": "reading",
                            "timestamp": "2026-07-18T08:00:00Z",
                            "gravity": 1.04,
                        }
                    ]
                }
            raise AssertionError(path)

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "selected-page.html"

    result = runner.invoke(
        app,
        [
            "fermentation-html",
            "--select",
            "--page",
            "2",
            "--limit",
            "25",
            "--output",
            str(destination),
        ],
        input="1\n",
    )

    assert result.exit_code == 0
    assert calls == [
        ("brews", {"page": 2, "limit": 25}),
        (f"brews/{brew_id}/readings", None),
    ]
    assert "More brews available: rerun with --select --page 3." in result.output


def test_fermentation_html_empty_selection_page_does_not_prompt(monkeypatch) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")
    calls: list[str] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            calls.append(path)
            assert path == "brews"
            return {
                "data": [],
                "pagination": {"hasMore": False, "total": 0},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        ["fermentation-html", "--select", "--page", "4"],
    )

    assert result.exit_code == 1
    assert "No brews found on page 4." in result.output
    assert "Brew number" not in result.output
    assert calls == ["brews"]


def test_fermentation_html_keeps_terminal_escaping_out_of_report_title(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            if path == "brews":
                return {
                    "data": [{"id": brew_id, "name": "Pipe | <Brew>"}],
                    "pagination": {"hasMore": False, "total": 1},
                }
            return {
                "data": [
                    {
                        "id": "reading",
                        "timestamp": "2026-07-18T08:00:00Z",
                        "gravity": 1.04,
                    }
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "context-safe-title.html"

    result = runner.invoke(
        app,
        ["fermentation-html", "--select", "--output", str(destination)],
        input="1\n",
    )

    assert result.exit_code == 0
    assert "Pipe \\| <Brew>" in result.output
    document = destination.read_text(encoding="utf-8")
    assert "Fermentation Report: Pipe | &lt;Brew&gt;" in document
    assert "Pipe \\|" not in document


def test_fermentation_html_rejects_uuid_coercible_non_string_id(monkeypatch) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            assert path == "brews"
            return {
                "data": [
                    {
                        "id": 12345678901234567890123456789012,
                        "name": "Must not become selectable",
                    }
                ],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(app, ["fermentation-html", "--select"])

    assert result.exit_code == 1
    assert "brew ID is not a string" in result.output
    assert "Must not become selectable" not in result.output


@pytest.mark.parametrize(
    ("page", "limit", "data_count", "has_more", "total"),
    [
        (1, 100, 2, False, 1),
        (1, 100, 1, True, 1),
        (2, 25, 5, True, 30),
        (1, 100, 1, False, 2),
        (1, 1, 2, False, 2),
    ],
)
def test_fermentation_html_rejects_contradictory_pagination_before_printing(
    monkeypatch,
    page: int,
    limit: int,
    data_count: int,
    has_more: bool,
    total: int,
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_select_token")
    ids = [
        "54d34560-f1af-49f0-9a26-6caca3397f75",
        "d995e6f0-69ee-422a-a781-1dd08427563a",
        "aa42e8b0-881c-4c5a-aa6f-fa2c8ddb5e5d",
        "0b152513-3550-4f6e-9b8a-8ccdd1a93534",
        "8664bd7d-a084-44af-97de-5f9137d2f19d",
    ]

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_select_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            assert path == "brews"
            return {
                "data": [
                    {"id": ids[index], "name": f"Hidden {index}"} for index in range(data_count)
                ],
                "pagination": {"hasMore": has_more, "total": total},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(
        app,
        [
            "fermentation-html",
            "--select",
            "--page",
            str(page),
            "--limit",
            str(limit),
        ],
    )

    assert result.exit_code == 1
    assert "pagination metadata contradicts returned data" in result.output
    assert "Hidden" not in result.output
