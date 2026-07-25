import json

import pytest

import forge_companion.shelly_cloud_credentials as cloud_credentials


class SecureBackend:
    priority = 5


SecureBackend.__module__ = "keyring.backends.Windows"


def _secure_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cloud_credentials.keyring, "get_keyring", lambda: SecureBackend())


def test_store_and_resolve_cloud_profile_uses_native_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    stored: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "set_password",
        lambda service, account, value: stored.__setitem__((service, account), value),
    )
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "get_password",
        lambda service, account: stored.get((service, account)),
    )

    cloud_credentials.store_profile(
        server="SHELLY-82-EU.SHELLY.CLOUD",
        device_id="AABBCCDDEEFF",
        auth_key="  synthetic-cloud-key  ",
    )
    resolved = cloud_credentials.resolve_profile()

    assert resolved == cloud_credentials.ResolvedCloudProfile(
        profile=cloud_credentials.ShellyCloudProfile(
            server="shelly-82-eu.shelly.cloud",
            device_id="aabbccddeeff",
            auth_key="synthetic-cloud-key",
        ),
        source="keyring",
    )
    raw = stored[(cloud_credentials.SERVICE_NAME, cloud_credentials.ACCOUNT_NAME)]
    assert json.loads(raw) == {
        "version": 1,
        "server": "shelly-82-eu.shelly.cloud",
        "device_id": "aabbccddeeff",
        "auth_key": "synthetic-cloud-key",
    }


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"version":1,"server":"shelly-82-eu.shelly.cloud",'
        '"device_id":"aabbccddeeff","auth_key":"synthetic-cloud-key","extra":1}',
        '{"version":1,"server":"evil.example",'
        '"server":"shelly-82-eu.shelly.cloud","device_id":"aabbccddeeff",'
        '"auth_key":"synthetic-cloud-key"}',
    ],
)
def test_resolve_rejects_ambiguous_or_unknown_stored_profile(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "get_password",
        lambda service, account: raw,
    )

    with pytest.raises(
        cloud_credentials.InvalidStoredCloudCredentialError,
        match="Stored Shelly Cloud credential is invalid",
    ):
        cloud_credentials.resolve_profile()


def test_delete_profile_removes_malformed_existing_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_backend(monkeypatch)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "get_password",
        lambda service, account: "malformed private cloud profile",
    )
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "delete_password",
        lambda service, account: calls.append((service, account)),
    )

    assert cloud_credentials.delete_profile() is True
    assert calls == [(cloud_credentials.SERVICE_NAME, cloud_credentials.ACCOUNT_NAME)]


def test_resolve_reports_missing_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(cloud_credentials.keyring, "get_password", lambda service, account: None)

    assert cloud_credentials.resolve_profile() == cloud_credentials.ResolvedCloudProfile(
        profile=None,
        source="missing",
    )


def test_delete_profile_is_idempotent_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _secure_backend(monkeypatch)
    monkeypatch.setattr(cloud_credentials.keyring, "get_password", lambda service, account: None)
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "delete_password",
        lambda service, account: (_ for _ in ()).throw(
            AssertionError("missing profile must not be deleted")
        ),
    )

    assert cloud_credentials.delete_profile() is False


def test_insecure_backend_is_rejected_before_cloud_profile_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PlaintextBackend:
        priority = 1

    PlaintextBackend.__module__ = "keyrings.alt.file"
    monkeypatch.setattr(cloud_credentials.keyring, "get_keyring", lambda: PlaintextBackend())
    monkeypatch.setattr(
        cloud_credentials.keyring,
        "get_password",
        lambda service, account: (_ for _ in ()).throw(
            AssertionError("insecure backend must not be read")
        ),
    )

    with pytest.raises(cloud_credentials.ShellyCloudCredentialError, match="native credential"):
        cloud_credentials.resolve_profile()


@pytest.mark.parametrize("operation", ["read", "store", "delete"])
def test_backend_failures_never_reflect_private_details(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    _secure_backend(monkeypatch)
    leaked = "private-cloud-key-and-backend-detail"

    if operation == "read":
        monkeypatch.setattr(
            cloud_credentials.keyring,
            "get_password",
            lambda service, account: (_ for _ in ()).throw(RuntimeError(leaked)),
        )
        action = cloud_credentials.resolve_profile
    elif operation == "store":
        monkeypatch.setattr(
            cloud_credentials.keyring,
            "set_password",
            lambda service, account, value: (_ for _ in ()).throw(RuntimeError(leaked)),
        )
        def action() -> None:
            cloud_credentials.store_profile(
                server="shelly-82-eu.shelly.cloud",
                device_id="aabbccddeeff",
                auth_key="synthetic-cloud-key",
            )
    else:
        monkeypatch.setattr(
            cloud_credentials.keyring,
            "get_password",
            lambda service, account: "existing",
        )
        monkeypatch.setattr(
            cloud_credentials.keyring,
            "delete_password",
            lambda service, account: (_ for _ in ()).throw(RuntimeError(leaked)),
        )
        action = cloud_credentials.delete_profile

    with pytest.raises(cloud_credentials.ShellyCloudCredentialError) as error:
        action()

    assert leaked not in str(error.value)
