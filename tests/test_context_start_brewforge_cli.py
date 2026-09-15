"""CLI tests for `vessel context start-brewforge`."""

import json

from typer.testing import CliRunner

import forge_companion.cli_vessel as cli_vessel
from forge_companion.cli import app
from forge_companion.vessels import bind_vessel

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
BREW_ID = "54d34560-f1af-49f0-9a26-6caca3397f75"
runner = CliRunner()


def _bind():
    bind_vessel(
        {
            "vessel_id": "tank",
            "hydrometer": PILL,
            "temperature_controller": CONTROLLER,
            "gravity_interpretation": "unknown",
        }
    )


def _detail(**overrides):
    values = {
        "id": BREW_ID,
        "name": "Example pale ale",
        "brewDate": "2026-08-29",
        "plannedBrewDate": "2026-09-30",
        "recipe": {"yeasts": [{"name": "US-05"}]},
        "measured": {"originalGravity": 1.077},
        "calculated": {"og": 1.08, "fg": 1.015},
    }
    values.update(overrides)
    return values


def _args(brew_id=BREW_ID, *extra):
    return [
        "vessel",
        "context",
        "start-brewforge",
        "tank",
        brew_id,
        *extra,
        "--authoritative-temperature-role",
        "hydrometer",
    ]


def _base(*extra):
    return [
        "vessel",
        "context",
        "start-brewforge",
        "tank",
        *extra,
        "--authoritative-temperature-role",
        "hydrometer",
    ]


class _RecordingClient:
    def __init__(self, *, token: str, detail, pages=None) -> None:
        self.token = token
        self.detail = detail
        self.pages = pages or {}
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    def get(self, path, params=None):
        self.calls.append((path, params))
        if path == "brews":
            return self.pages[params["page"]]
        return self.detail


class _ForbiddenClient:
    def __init__(self, *, token: str) -> None:
        raise AssertionError("client must not be constructed before local preflight")


def _forbid_credentials(monkeypatch) -> list[str]:
    """Record every credential/client attempt; never raise, so the assertion is real."""
    attempts: list[str] = []

    class _Client:
        def __init__(self, *, token: str) -> None:
            attempts.append("client")

    monkeypatch.setattr(cli_vessel, "BrewForgeClient", _Client)
    monkeypatch.setattr(cli_vessel, "_token_for_api", lambda: attempts.append("credentials") or "x")
    return attempts


def test_help_lists_start_brewforge():
    result = runner.invoke(app, ["vessel", "context", "start-brewforge", "--help"])

    assert result.exit_code == 0
    assert "start-brewforge" in result.stdout


def test_requires_explicit_temperature_role():
    result = runner.invoke(app, ["vessel", "context", "start-brewforge", "tank", BREW_ID])

    assert result.exit_code == 2


def test_missing_brew_or_select_fails_before_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", _ForbiddenClient)
    monkeypatch.setattr(
        cli_vessel, "_token_for_api", lambda: (_ for _ in ()).throw(AssertionError("creds"))
    )

    result = runner.invoke(app, _base())

    assert result.exit_code == 1
    assert "provide a brew UUID or --select" in result.output


def test_unbound_vessel_fails_before_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    attempts = _forbid_credentials(monkeypatch)

    result = runner.invoke(app, _args())

    assert result.exit_code == 1
    assert "Vessel must be bound before starting a context." in result.output
    assert attempts == []


def test_active_conflict_fails_before_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    manual = runner.invoke(
        app,
        [
            "vessel",
            "context",
            "start",
            "tank",
            "--batch",
            "Existing",
            "--original-gravity-sg",
            "1.077",
            "--expected-final-gravity-sg",
            "1.015",
            "--yeast",
            "US-05",
            "--start-date",
            "2026-08-01",
            "--authoritative-temperature-role",
            "hydrometer",
        ],
    )
    assert manual.exit_code == 0, manual.output
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", _ForbiddenClient)
    monkeypatch.setattr(
        cli_vessel, "_token_for_api", lambda: (_ for _ in ()).throw(AssertionError("creds"))
    )

    result = runner.invoke(app, _args())

    assert result.exit_code == 1
    assert "active context" in result.output


def test_invalid_override_fails_before_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    attempts = _forbid_credentials(monkeypatch)

    result = runner.invoke(app, [*_args(), "--original-gravity-sg", ".077"])

    assert result.exit_code == 1
    assert "Original gravity must be a decimal in 1.xxx form." in result.output
    assert attempts == []


def test_direct_id_uses_one_detail_get_and_persists_context(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    client = _RecordingClient(token="test-token", detail=_detail())
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(app, _args(), env={"BREWFORGE_API_TOKEN": "test-token"})

    assert result.exit_code == 0, result.output
    assert client.calls == [(f"brews/{BREW_ID}", None)]
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    context = stored["contexts"][0]
    assert context["brewforge_brew_id"] == BREW_ID
    assert context["batch_display_name"] == "Example pale ale"
    assert context["original_gravity_sg"] == "1.077"
    assert context["expected_final_gravity_sg"] == "1.015"
    assert context["yeast"] == "US-05"
    assert context["fermentation_start_date"] == "2026-08-29"
    assert "Fermentation context started from BrewForge" in result.stdout
    assert "calculated estimate, not a measurement" in result.stdout


def test_select_does_paginated_selection_then_one_detail_get(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    page_one = {
        "data": [{"id": BREW_ID, "name": "Chosen"}],
        "pagination": {"hasMore": False, "total": 1},
    }
    client = _RecordingClient(token="test-token", detail=_detail(), pages={1: page_one})
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(
        app,
        _base("--select"),
        input="1\n",
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0, result.output
    assert [path for path, _ in client.calls] == ["brews", f"brews/{BREW_ID}"]
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    assert stored["contexts"][0]["brewforge_brew_id"] == BREW_ID


def test_missing_source_field_names_override_and_does_not_write(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    client = _RecordingClient(token="test-token", detail=_detail(recipe={}))
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(app, _args(), env={"BREWFORGE_API_TOKEN": "test-token"})

    assert result.exit_code == 1
    assert "--yeast" in result.output
    assert not (tmp_path / "fermentation-contexts.json").exists()


def test_override_supplies_missing_source_field(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    client = _RecordingClient(token="test-token", detail=_detail(recipe={}))
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(
        app, [*_args(), "--yeast", "S-04"], env={"BREWFORGE_API_TOKEN": "test-token"}
    )

    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    assert stored["contexts"][0]["yeast"] == "S-04"


def test_detail_id_mismatch_fails_without_output_or_write(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    wrong = _detail(id="99999999-9999-4999-8999-999999999999")
    client = _RecordingClient(token="test-token", detail=wrong)
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(app, _args(), env={"BREWFORGE_API_TOKEN": "test-token"})

    assert result.exit_code == 1
    assert "Fermentation context started" not in result.stdout
    assert not (tmp_path / "fermentation-contexts.json").exists()


def test_api_failure_hides_token(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    token = "bfk_context_secret"

    class FailingClient:
        def __init__(self, *, token: str) -> None:
            pass

        def get(self, path, params=None):
            import httpx

            request = httpx.Request("GET", "https://example.invalid/brews")
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m", request=request)

    monkeypatch.setattr(cli_vessel, "BrewForgeClient", FailingClient)

    result = runner.invoke(app, _args(), env={"BREWFORGE_API_TOKEN": token})

    assert result.exit_code == 1
    assert token not in result.output
    assert "\x1b" not in result.output


def test_select_with_brew_id_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()

    result = runner.invoke(app, [*_args(), "--select"])

    assert result.exit_code == 1
    assert "--select cannot be used together" in result.output


def test_pagination_without_select_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()

    result = runner.invoke(app, [*_args(), "--page", "2"])

    assert result.exit_code == 1
    assert "--page and --limit require --select" in result.output


def test_read_only_no_write_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    client = _RecordingClient(token="test-token", detail=_detail())
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(app, _args(), env={"BREWFORGE_API_TOKEN": "test-token"})

    assert result.exit_code == 0, result.output
    assert all(path.startswith("brews") for path, _ in client.calls)
    assert not hasattr(client, "post")


def test_override_wins_over_present_source_value(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    client = _RecordingClient(token="test-token", detail=_detail())
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    result = runner.invoke(
        app,
        [*_args(), "--batch", "Renamed", "--expected-final-gravity-sg", "1.010"],
        env={"BREWFORGE_API_TOKEN": "test-token"},
    )

    assert result.exit_code == 0, result.output
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    assert stored["contexts"][0]["batch_display_name"] == "Renamed"
    assert stored["contexts"][0]["expected_final_gravity_sg"] == "1.010"


def test_switch_starts_new_context_from_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    _bind()
    client = _RecordingClient(token="test-token", detail=_detail())
    monkeypatch.setattr(cli_vessel, "BrewForgeClient", lambda *, token: client)

    assert runner.invoke(
        app, _args(), env={"BREWFORGE_API_TOKEN": "test-token"}
    ).exit_code == 0
    switched = runner.invoke(
        app, [*_args(), "--switch"], env={"BREWFORGE_API_TOKEN": "test-token"}
    )

    assert switched.exit_code == 0, switched.output
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    statuses = sorted(context["status"] for context in stored["contexts"])
    assert statuses == ["active", "closed"]
