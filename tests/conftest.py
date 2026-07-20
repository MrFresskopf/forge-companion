import keyring
import pytest


@pytest.fixture(autouse=True)
def block_real_credential_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Require every credential-store test to install an explicit fake backend."""

    def blocked(*args: object, **kwargs: object) -> object:
        raise AssertionError("tests must not access the real OS credential store")

    monkeypatch.setattr(keyring, "get_keyring", blocked)
    monkeypatch.setattr(keyring, "get_password", blocked)
    monkeypatch.setattr(keyring, "set_password", blocked)
    monkeypatch.setattr(keyring, "delete_password", blocked)
