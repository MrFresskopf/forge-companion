import json

import keyring
import pytest

import forge_companion.rapt_credentials as credentials


class SecureBackend:
    priority = 5


SecureBackend.__module__ = "keyring.backends.Windows"


def _secure_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: SecureBackend())


def test_store_profile_writes_username_and_secret_only_to_native_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, account, value: calls.append((service, account, value)),
    )

    credentials.store_profile(username="brewer@example.com", api_secret="api-secret")

    assert len(calls) == 1
    service, account, raw = calls[0]
    assert service == "forge-companion"
    assert account == "rapt-api-profile"
    assert json.loads(raw) == {
        "version": 1,
        "username": "brewer@example.com",
        "api_secret": "api-secret",
    }


def test_resolve_profile_validates_the_stored_document(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda service, account: json.dumps(
            {
                "version": 1,
                "username": "brewer@example.com",
                "api_secret": "api-secret",
            }
        ),
    )

    assert credentials.resolve_profile() == credentials.ResolvedRaptProfile(
        profile=credentials.RaptProfile(
            username="brewer@example.com",
            api_secret="api-secret",
        ),
        source="keyring",
    )


def test_delete_profile_removes_an_existing_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(keyring, "get_password", lambda service, account: "stored")
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        keyring,
        "delete_password",
        lambda service, account: calls.append((service, account)),
    )

    assert credentials.delete_profile() is True
    assert calls == [("forge-companion", "rapt-api-profile")]


@pytest.mark.parametrize(
    "module,priority",
    [
        ("keyrings.alt.file", 5),
        ("keyring.backends.chainer", 5),
        ("keyring.backends.Windows.plaintext", 5),
        ("keyring.backends.Windows", 0),
        ("keyring.backends.Windows", True),
        ("keyring.backends.Windows", float("nan")),
        ("keyring.backends.Windows", float("inf")),
        ("keyring.backends.Windows", 10**400),
    ],
)
def test_rejects_non_native_or_invalid_priority_backend(
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    priority: object,
) -> None:
    backend = type("Backend", (), {"__module__": module, "priority": priority})()
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    reads: list[object] = []
    monkeypatch.setattr(keyring, "get_password", lambda *args: reads.append(args))
    with pytest.raises(credentials.RaptCredentialError, match="not available"):
        credentials.resolve_profile()
    assert reads == []


@pytest.mark.parametrize("version", [True, 1.0, "1", None, 2])
def test_profile_version_requires_exact_integer_one(
    monkeypatch: pytest.MonkeyPatch,
    version: object,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda *args: json.dumps(
            {
                "version": version,
                "username": "private-user",
                "api_secret": "private-secret",
            }
        ),
    )
    with pytest.raises(credentials.InvalidStoredRaptCredentialError):
        credentials.resolve_profile()


def test_profile_representations_do_not_expose_credentials() -> None:
    profile = credentials.RaptProfile(username="private-user", api_secret="private-secret")
    resolved = credentials.ResolvedRaptProfile(profile=profile, source="keyring")
    for value in (profile, resolved):
        assert "private-user" not in repr(value)
        assert "private-secret" not in repr(value)


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        "{}",
        "null",
        "[" * 2000,
        '{"version":1,"version":1,"username":"private-user","api_secret":"private-secret"}',
        '{"version":1,"username":false,"api_secret":"private-secret"}',
        '{"version":1,"username":"private-user","api_secret":"contains space"}',
        '{"version":1,"username":"private-user","api_secret":"private-secret","extra":1}',
    ],
)
def test_malformed_profiles_fail_privately_but_can_be_deleted(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(keyring, "get_password", lambda *args: raw)
    deleted: list[object] = []
    monkeypatch.setattr(keyring, "delete_password", lambda *args: deleted.append(args))
    with pytest.raises(credentials.InvalidStoredRaptCredentialError) as caught:
        credentials.resolve_profile()
    assert str(caught.value) == "Stored RAPT credential is invalid."
    assert credentials.delete_profile() is True
    assert deleted == [(credentials.SERVICE_NAME, credentials.ACCOUNT_NAME)]


def test_absent_profile_resolves_missing_and_logout_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(keyring, "get_password", lambda *args: None)
    assert credentials.resolve_profile() == credentials.ResolvedRaptProfile(None, "missing")
    assert credentials.delete_profile() is False


@pytest.mark.parametrize(
    "operation", ["resolve", "store", "delete-read", "delete-write", "backend"]
)
@pytest.mark.parametrize("error_type", [RuntimeError, keyring.errors.PasswordDeleteError])
def test_backend_failures_have_private_errors_and_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    error_type: type[Exception],
) -> None:
    import traceback

    _secure_backend(monkeypatch)
    monkeypatch.setattr(keyring, "get_password", lambda *args: "malformed-private-data")

    def fail(*args: object) -> None:
        raise error_type("private-user private-secret backend-path")

    target = {
        "resolve": "get_password",
        "store": "set_password",
        "delete-read": "get_password",
        "delete-write": "delete_password",
        "backend": "get_keyring",
    }[operation]
    monkeypatch.setattr(keyring, target, fail)
    with pytest.raises(credentials.RaptCredentialError) as caught:
        if operation == "store":
            credentials.store_profile(username="private-user", api_secret="private-secret")
        elif operation.startswith("delete"):
            credentials.delete_profile()
        else:
            credentials.resolve_profile()
    rendered = "".join(traceback.format_exception(caught.value))
    # Do not assert against test source lines containing synthetic literals.
    assert "private-user private-secret backend-path" not in rendered
    assert str(caught.value) == "Native credential store access failed."
