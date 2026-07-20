import pytest
from keyring.errors import PasswordDeleteError

import forge_companion.credentials as credentials


class SecureBackend:
    priority = 5


SecureBackend.__module__ = "keyring.backends.Windows"


def _secure_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials.keyring, "get_keyring", lambda: SecureBackend())


def test_resolve_token_prefers_environment_without_opening_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", " env-token ")
    monkeypatch.setattr(
        credentials.keyring,
        "get_keyring",
        lambda: (_ for _ in ()).throw(AssertionError("keyring must not be opened")),
    )

    resolved = credentials.resolve_token()

    assert resolved == credentials.ResolvedToken(token="env-token", source="environment")


def test_resolve_token_uses_native_keyring_when_environment_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        credentials.keyring,
        "get_password",
        lambda service, account: (
            "stored-token"
            if (service, account) == (credentials.SERVICE_NAME, credentials.ACCOUNT_NAME)
            else None
        ),
    )

    resolved = credentials.resolve_token()

    assert resolved == credentials.ResolvedToken(token="stored-token", source="keyring")


def test_resolve_token_reports_missing_without_exposing_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    _secure_backend(monkeypatch)
    monkeypatch.setattr(credentials.keyring, "get_password", lambda service, account: None)

    resolved = credentials.resolve_token()

    assert resolved == credentials.ResolvedToken(token=None, source="missing")


@pytest.mark.parametrize("backend_module", ["keyrings.alt.file", "keyring.backends.WindowsEvil"])
def test_insecure_or_missing_backend_is_rejected_before_read(
    monkeypatch: pytest.MonkeyPatch,
    backend_module: str,
) -> None:
    class PlaintextBackend:
        priority = 1

    PlaintextBackend.__module__ = backend_module
    monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    monkeypatch.setattr(credentials.keyring, "get_keyring", lambda: PlaintextBackend())
    monkeypatch.setattr(
        credentials.keyring,
        "get_password",
        lambda service, account: (_ for _ in ()).throw(
            AssertionError("insecure backend must not be read")
        ),
    )

    with pytest.raises(credentials.CredentialStoreError, match="native credential store"):
        credentials.resolve_token()


def test_invalid_environment_override_fails_without_reading_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BREWFORGE_API_TOKEN", "invalid token")
    monkeypatch.setattr(
        credentials.keyring,
        "get_keyring",
        lambda: (_ for _ in ()).throw(AssertionError("invalid override must fail first")),
    )

    with pytest.raises(
        credentials.InvalidEnvironmentCredentialError,
        match="environment credential is invalid",
    ):
        credentials.resolve_token()


@pytest.mark.parametrize(
    ("environment_value", "expected"),
    [
        (None, "absent"),
        ("   ", "absent"),
        ("valid-token", "valid"),
        ("invalid token", "invalid"),
    ],
)
def test_environment_override_status_uses_the_same_validation_as_resolution(
    monkeypatch: pytest.MonkeyPatch,
    environment_value: str | None,
    expected: str,
) -> None:
    if environment_value is None:
        monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BREWFORGE_API_TOKEN", environment_value)

    assert credentials.environment_override_status() == expected


def test_store_token_validates_and_writes_only_to_native_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        credentials.keyring,
        "set_password",
        lambda service, account, token: calls.append((service, account, token)),
    )

    credentials.store_token("  stored-token  ")

    assert calls == [(credentials.SERVICE_NAME, credentials.ACCOUNT_NAME, "stored-token")]


@pytest.mark.parametrize("token", ["", "   ", "token with space", "token\nwith-control"])
def test_store_token_rejects_empty_or_whitespace_token(
    monkeypatch: pytest.MonkeyPatch,
    token: str,
) -> None:
    monkeypatch.setattr(
        credentials.keyring,
        "get_keyring",
        lambda: (_ for _ in ()).throw(AssertionError("invalid token must fail first")),
    )

    with pytest.raises(ValueError, match="must not be empty or contain whitespace"):
        credentials.store_token(token)


def test_delete_token_is_idempotent_when_no_entry_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(credentials.keyring, "get_password", lambda service, account: None)
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: (_ for _ in ()).throw(
            AssertionError("missing entry must not be deleted")
        ),
    )

    assert credentials.delete_token() is False


def test_delete_token_removes_existing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(credentials.keyring, "get_password", lambda service, account: "secret")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: calls.append((service, account)),
    )

    assert credentials.delete_token() is True
    assert calls == [(credentials.SERVICE_NAME, credentials.ACCOUNT_NAME)]


def test_delete_token_removes_malformed_existing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(credentials.keyring, "get_password", lambda service, account: "bad token")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: calls.append((service, account)),
    )

    assert credentials.delete_token() is True
    assert calls == [(credentials.SERVICE_NAME, credentials.ACCOUNT_NAME)]


def test_backend_exception_is_wrapped_without_original_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    leaked = "backend-reflected-secret"
    monkeypatch.setattr(
        credentials.keyring,
        "get_password",
        lambda service, account: (_ for _ in ()).throw(RuntimeError(leaked)),
    )

    with pytest.raises(credentials.CredentialStoreError) as error:
        credentials.resolve_token()

    assert leaked not in str(error.value)


def test_backend_discovery_exception_is_wrapped_without_original_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    leaked = "backend-discovery-detail"
    monkeypatch.setattr(
        credentials.keyring,
        "get_keyring",
        lambda: (_ for _ in ()).throw(RuntimeError(leaked)),
    )

    with pytest.raises(credentials.CredentialStoreError) as error:
        credentials.resolve_token()

    assert leaked not in str(error.value)


@pytest.mark.parametrize("stored", ["   ", "stored token"])
def test_invalid_stored_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    stored: str,
) -> None:
    monkeypatch.delenv("BREWFORGE_API_TOKEN", raising=False)
    _secure_backend(monkeypatch)
    monkeypatch.setattr(credentials.keyring, "get_password", lambda service, account: stored)

    with pytest.raises(
        credentials.InvalidStoredCredentialError,
        match="Stored BrewForge credential is invalid",
    ):
        credentials.resolve_token()


def test_store_backend_exception_is_wrapped_without_original_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    leaked = "store-backend-detail"
    monkeypatch.setattr(
        credentials.keyring,
        "set_password",
        lambda service, account, token: (_ for _ in ()).throw(RuntimeError(leaked)),
    )

    with pytest.raises(credentials.CredentialStoreError) as error:
        credentials.store_token("stored-token")

    assert leaked not in str(error.value)


def test_delete_backend_exception_is_wrapped_without_original_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        credentials.keyring, "get_password", lambda service, account: "stored-token"
    )
    leaked = "delete-backend-detail"
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: (_ for _ in ()).throw(RuntimeError(leaked)),
    )

    with pytest.raises(credentials.CredentialStoreError) as error:
        credentials.delete_token()

    assert leaked not in str(error.value)


def test_delete_existence_check_failure_is_wrapped_without_original_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    leaked = "read-before-delete-detail"
    monkeypatch.setattr(
        credentials.keyring,
        "get_password",
        lambda service, account: (_ for _ in ()).throw(RuntimeError(leaked)),
    )
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: (_ for _ in ()).throw(
            AssertionError("delete must not run after a failed existence check")
        ),
    )

    with pytest.raises(credentials.CredentialStoreError) as error:
        credentials.delete_token()

    assert leaked not in str(error.value)


def test_password_delete_error_for_existing_entry_is_not_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        credentials.keyring, "get_password", lambda service, account: "stored-token"
    )
    monkeypatch.setattr(
        credentials.keyring,
        "delete_password",
        lambda service, account: (_ for _ in ()).throw(
            PasswordDeleteError("native deletion was denied")
        ),
    )

    with pytest.raises(credentials.CredentialStoreError, match="access failed"):
        credentials.delete_token()
