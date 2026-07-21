from pathlib import Path

from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion.cli import app

runner = CliRunner()


class _TTYStub:
    def __init__(self, interactive: bool) -> None:
        self._interactive = interactive

    def isatty(self) -> bool:
        return self._interactive


def test_report_auto_selection_requires_interactive_input_and_output(monkeypatch) -> None:
    monkeypatch.setattr(cli.sys, "stdin", _TTYStub(True))
    monkeypatch.setattr(cli.sys, "stdout", _TTYStub(False))

    assert cli._terminal_is_interactive() is False


def test_report_without_uuid_noninteractive_requires_explicit_uuid(monkeypatch) -> None:
    created = False

    class ForbiddenClient:
        def __init__(self, *, token: str) -> None:
            nonlocal created
            created = True

    monkeypatch.setattr(cli, "BrewForgeClient", ForbiddenClient)

    result = runner.invoke(
        app,
        ["report"],
        env={"BREWFORGE_API_TOKEN": "bfk_test_report_token"},
    )

    assert result.exit_code == 1
    assert "interactive terminal" in result.output
    assert "pass an exact brew UUID" in result.output
    assert created is False


def test_report_without_uuid_selects_brew_and_writes_html(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_terminal_is_interactive", lambda: True)
    token = "bfk_test_report_token"
    monkeypatch.setenv("BREWFORGE_API_TOKEN", token)
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    calls: list[tuple[str, dict[str, object] | None]] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_report_token"

        def get(
            self, path: str, params: dict[str, object] | None = None
        ) -> dict[str, object]:
            calls.append((path, params))
            if path == "brews":
                return {
                    "data": [{"id": brew_id, "name": "Comfort Wit"}],
                    "pagination": {"hasMore": False, "total": 1},
                }
            if path == f"brews/{brew_id}/readings":
                return {
                    "data": [
                        {
                            "id": "reading",
                            "timestamp": "2026-07-21T18:00:00Z",
                            "gravity": 1.012,
                            "temperature": 29.0,
                        }
                    ]
                }
            raise AssertionError(f"unexpected GET: {path}")

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "report.html"

    result = runner.invoke(
        app,
        ["report", "--temperature-unit", "C", "--output", str(destination)],
        input="1\n",
    )

    assert result.exit_code == 0
    assert calls == [
        ("brews", {"page": 1, "limit": 25}),
        (f"brews/{brew_id}/readings", None),
    ]
    assert "1  Comfort Wit" in result.output
    assert "Enter q to cancel." in result.output
    assert destination.exists()
    assert "Fermentation Report: Comfort Wit" in destination.read_text(encoding="utf-8")
    assert token not in result.output


def test_report_selection_can_navigate_pages_explicitly(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cli, "_terminal_is_interactive", lambda: True)
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_report_token")
    first_id = "54d34560-f1af-49f0-9a26-6caca3397f75"
    second_id = "d995e6f0-69ee-422a-a781-1dd08427563a"
    calls: list[tuple[str, dict[str, object] | None]] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_report_token"

        def get(
            self, path: str, params: dict[str, object] | None = None
        ) -> dict[str, object]:
            calls.append((path, params))
            if path == "brews" and params == {"page": 1, "limit": 25}:
                return {
                    "data": [{"id": first_id, "name": "First page"}],
                    "pagination": {"hasMore": True, "total": 26},
                }
            if path == "brews" and params == {"page": 2, "limit": 25}:
                return {
                    "data": [{"id": second_id, "name": "Second page"}],
                    "pagination": {"hasMore": False, "total": 26},
                }
            if path == f"brews/{second_id}/readings":
                return {
                    "data": [
                        {
                            "id": "reading",
                            "timestamp": "2026-07-21T18:00:00Z",
                            "gravity": 1.012,
                        }
                    ]
                }
            raise AssertionError(f"unexpected GET: {path} {params}")

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "page-two.html"

    result = runner.invoke(
        app,
        ["report", "--output", str(destination)],
        input="n\np\nn\n1\n",
    )

    assert result.exit_code == 0
    assert calls == [
        ("brews", {"page": 1, "limit": 25}),
        ("brews", {"page": 2, "limit": 25}),
        ("brews", {"page": 1, "limit": 25}),
        ("brews", {"page": 2, "limit": 25}),
        (f"brews/{second_id}/readings", None),
    ]
    assert "Second page" in result.output
    assert "Enter q to cancel." in result.output
    assert destination.exists()


def test_report_selection_can_cancel_without_requesting_readings(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_terminal_is_interactive", lambda: True)
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_report_token")
    calls: list[str] = []

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_report_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            calls.append(path)
            assert path == "brews"
            return {
                "data": [
                    {
                        "id": "54d34560-f1af-49f0-9a26-6caca3397f75",
                        "name": "Not selected",
                    }
                ],
                "pagination": {"hasMore": False, "total": 1},
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)

    result = runner.invoke(app, ["report"], input="q\n")

    assert result.exit_code == 1
    assert calls == ["brews"]
    assert "Report cancelled." in result.output
    assert "Fermentation HTML failed" not in result.output
    assert "Traceback" not in result.output


def test_report_can_remember_temperature_unit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_report_token")
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path / "config"))
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_report_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            assert path == f"brews/{brew_id}/readings"
            return {
                "data": [
                    {
                        "id": "reading",
                        "timestamp": "2026-07-21T18:00:00Z",
                        "gravity": 1.012,
                        "temperature": 29.0,
                    }
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    first = tmp_path / "first.html"
    second = tmp_path / "second.html"

    remembered = runner.invoke(
        app,
        [
            "report",
            brew_id,
            "--temperature-unit",
            "C",
            "--remember",
            "--output",
            str(first),
        ],
    )
    reused = runner.invoke(app, ["report", brew_id, "--output", str(second)])

    assert remembered.exit_code == 0
    assert "Temperature unit C saved as the report default." in remembered.output
    assert reused.exit_code == 0
    assert "29.0–29.0 °C" in second.read_text(encoding="utf-8")


def test_report_explicit_unit_overrides_malformed_preferences(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "bfk_test_report_token")
    config = tmp_path / "config"
    config.mkdir()
    (config / "preferences.json").write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(config))
    brew_id = "54d34560-f1af-49f0-9a26-6caca3397f75"

    class StubClient:
        def __init__(self, *, token: str) -> None:
            assert token == "bfk_test_report_token"

        def get(self, path: str, params=None) -> dict[str, object]:
            assert path == f"brews/{brew_id}/readings"
            return {
                "data": [
                    {
                        "id": "reading",
                        "timestamp": "2026-07-21T18:00:00Z",
                        "gravity": 1.012,
                        "temperature": 29.0,
                    }
                ]
            }

    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    destination = tmp_path / "explicit.html"

    result = runner.invoke(
        app,
        [
            "report",
            brew_id,
            "--temperature-unit",
            "F",
            "--output",
            str(destination),
        ],
    )

    assert result.exit_code == 0
    assert "29.0–29.0 °F" in destination.read_text(encoding="utf-8")
