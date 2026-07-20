import pytest
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion import credentials
from forge_companion.cli import app

runner = CliRunner()


def test_auth_login_prompts_twice_without_echoing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "credential-store-test-token"
    stored: list[str] = []
    monkeypatch.setattr(credentials, "store_token", stored.append)

    result = runner.invoke(
        app,
        ["auth", "login"],
        input=f"{token}\n{token}\n",
        env={"BREWFORGE_API_TOKEN": ""},
    )

    assert result.exit_code == 0
    assert stored == [token]
    assert "Credential stored in the native OS credential store." in result.output
    assert token not in result.output


def test_auth_login_warns_when_environment_override_remains_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials, "store_token", lambda token: None)

    result = runner.invoke(
        app,
        ["auth", "login"],
        input="stored-token\nstored-token\n",
        env={"BREWFORGE_API_TOKEN": "environment-token"},
    )

    assert result.exit_code == 0
    assert "BREWFORGE_API_TOKEN currently overrides the stored credential." in result.output
    assert "stored-token" not in result.output
    assert "environment-token" not in result.output


def test_auth_login_reports_invalid_environment_without_calling_it_an_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials, "store_token", lambda token: None)

    result = runner.invoke(
        app,
        ["auth", "login"],
        input="stored-token\nstored-token\n",
        env={"BREWFORGE_API_TOKEN": "invalid token"},
    )

    assert result.exit_code == 0
    assert (
        "BREWFORGE_API_TOKEN is set but invalid and prevents stored credential use."
        in result.output
    )
    assert "currently overrides" not in result.output
    assert "invalid token" not in result.output


@pytest.mark.parametrize(
    "prompt_input",
    [
        "first-secret\ndifferent-secret\n",
        "",
    ],
)
def test_auth_login_confirmation_failure_or_eof_never_stores(
    monkeypatch: pytest.MonkeyPatch,
    prompt_input: str,
) -> None:
    stored: list[str] = []
    monkeypatch.setattr(credentials, "store_token", stored.append)

    result = runner.invoke(
        app,
        ["auth", "login"],
        input=prompt_input,
        env={"BREWFORGE_API_TOKEN": ""},
    )

    assert result.exit_code == 1
    assert stored == []
    assert "first-secret" not in result.output
    assert "different-secret" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("resolved", "expected", "exit_code"),
    [
        (
            credentials.ResolvedToken("environment-secret", "environment"),
            "Authentication source: BREWFORGE_API_TOKEN environment override.",
            0,
        ),
        (
            credentials.ResolvedToken("stored-secret", "keyring"),
            "Authentication source: native OS credential store.",
            0,
        ),
        (
            credentials.ResolvedToken(None, "missing"),
            "Authentication is not configured.",
            1,
        ),
    ],
)
def test_auth_status_reports_only_non_secret_source(
    monkeypatch: pytest.MonkeyPatch,
    resolved: credentials.ResolvedToken,
    expected: str,
    exit_code: int,
) -> None:
    monkeypatch.setattr(credentials, "resolve_token", lambda: resolved)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == exit_code
    assert expected in result.output
    assert "environment-secret" not in result.output
    assert "stored-secret" not in result.output


def test_auth_logout_is_idempotent_and_reports_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials, "delete_token", lambda: False)

    result = runner.invoke(
        app,
        ["auth", "logout"],
        env={"BREWFORGE_API_TOKEN": "environment-secret"},
    )

    assert result.exit_code == 0
    assert "No stored credential was present." in result.output
    assert "BREWFORGE_API_TOKEN remains active and was not changed." in result.output
    assert "environment-secret" not in result.output


def test_auth_logout_reports_invalid_environment_without_calling_it_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(credentials, "delete_token", lambda: False)

    result = runner.invoke(
        app,
        ["auth", "logout"],
        env={"BREWFORGE_API_TOKEN": "invalid token"},
    )

    assert result.exit_code == 0
    assert (
        "BREWFORGE_API_TOKEN is set but invalid and prevents stored credential use."
        in result.output
    )
    assert "remains active" not in result.output
    assert "invalid token" not in result.output


def test_auth_backend_failure_is_safe_and_has_no_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    leaked = "backend-reflected-secret"

    def fail() -> credentials.ResolvedToken:
        raise credentials.CredentialStoreError(f"Native credential store failed: {leaked}")

    monkeypatch.setattr(credentials, "resolve_token", fail)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 1
    assert result.output == "Authentication failed: native credential store access failed.\n"
    assert leaked not in result.output
    assert "Traceback" not in result.output


def test_auth_status_reports_invalid_stored_credential_accurately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> credentials.ResolvedToken:
        raise credentials.InvalidStoredCredentialError("reflected stored secret")

    monkeypatch.setattr(credentials, "resolve_token", fail)

    result = runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 1
    assert result.output == (
        "Authentication failed: stored credential is invalid; "
        "run `forge-companion auth logout` and log in again.\n"
    )
    assert "reflected stored secret" not in result.output


def test_auth_status_reports_invalid_environment_accurately() -> None:
    result = runner.invoke(
        app,
        ["auth", "status"],
        env={"BREWFORGE_API_TOKEN": "invalid token"},
    )

    assert result.exit_code == 1
    assert result.output == "Authentication failed: BREWFORGE_API_TOKEN is invalid.\n"
    assert "invalid token" not in result.output


def test_existing_api_command_uses_stored_token_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "stored-secret"
    client_tokens: list[str] = []

    class StubClient:
        def __init__(self, token: str) -> None:
            client_tokens.append(token)

    monkeypatch.setattr(
        credentials,
        "resolve_token",
        lambda: credentials.ResolvedToken(token=token, source="keyring"),
    )
    monkeypatch.setattr(cli, "BrewForgeClient", StubClient)
    monkeypatch.setattr(cli, "run_doctor", lambda client: [])

    result = runner.invoke(app, ["doctor"], env={"BREWFORGE_API_TOKEN": ""})

    assert result.exit_code == 0
    assert client_tokens == [token]
    assert token not in result.output
