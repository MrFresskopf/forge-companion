import json

import keyring
import pytest

import forge_companion.brewfather_credentials as credentials


class SecureBackend:
    priority = 5


SecureBackend.__module__ = "keyring.backends.Windows"


def _secure_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: SecureBackend())


def test_store_profile_writes_user_id_and_api_key_only_to_native_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    writes: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        keyring,
        "set_password",
        lambda service, account, value: writes.append((service, account, value)),
    )

    credentials.store_profile(user_id="private-user-id", api_key="private-api-key")

    assert len(writes) == 1
    service, account, raw = writes[0]
    assert (service, account) == ("forge-companion", "brewfather-api-profile")
    assert json.loads(raw) == {
        "version": 1,
        "user_id": "private-user-id",
        "api_key": "private-api-key",
    }


def test_resolve_profile_validates_and_hides_both_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda *args: json.dumps(
            {"version": 1, "user_id": "private-user-id", "api_key": "private-api-key"}
        ),
    )

    resolved = credentials.resolve_profile()

    assert resolved == credentials.ResolvedBrewfatherProfile(
        profile=credentials.BrewfatherProfile(
            user_id="private-user-id", api_key="private-api-key"
        ),
        source="keyring",
    )
    assert "private-user-id" not in repr(resolved)
    assert "private-api-key" not in repr(resolved)


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        '{"version":1,"user_id":"private-user-id","api_key":"private-api-key","extra":1}',
        '{"version":1,"user_id":"private user","api_key":"private-api-key"}',
        '{"version":1,"user_id":"private-user-id","api_key":"private api key"}',
        '{"version":1,"version":1,"user_id":"private-user-id","api_key":"private-api-key"}',
    ],
)
def test_malformed_profile_is_rejected_but_deletable(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(keyring, "get_password", lambda *args: raw)
    deleted: list[tuple[object, ...]] = []
    monkeypatch.setattr(keyring, "delete_password", lambda *args: deleted.append(args))

    with pytest.raises(credentials.InvalidStoredBrewfatherCredentialError):
        credentials.resolve_profile()

    assert credentials.delete_profile() is True
    assert deleted == [(credentials.SERVICE_NAME, credentials.ACCOUNT_NAME)]


def test_backend_failure_is_private(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        keyring,
        "get_password",
        lambda *args: (_ for _ in ()).throw(RuntimeError("private user and API key")),
    )

    with pytest.raises(credentials.BrewfatherCredentialError) as caught:
        credentials.resolve_profile()

    assert str(caught.value) == "Native credential store access failed."
    assert "private user and API key" not in str(caught.value)
