"""Native operating-system credential storage for BrewForge API tokens."""

import os
from dataclasses import dataclass
from typing import Literal

import keyring

SERVICE_NAME = "forge-companion"
ACCOUNT_NAME = "brewforge-api-token"
_ENVIRONMENT_NAME = "BREWFORGE_API_TOKEN"
_NATIVE_BACKEND_MODULES = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
)


class CredentialStoreError(RuntimeError):
    """Report a credential-store failure without exposing backend details."""


class InvalidEnvironmentCredentialError(CredentialStoreError):
    """Report an invalid environment override without exposing its value."""


class InvalidStoredCredentialError(CredentialStoreError):
    """Report an invalid stored credential without exposing its value."""


@dataclass(frozen=True)
class ResolvedToken:
    """A token together with its non-secret source."""

    token: str | None
    source: Literal["environment", "keyring", "missing"]


def _require_native_backend() -> None:
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except Exception:
        raise CredentialStoreError("Native credential store access failed.") from None

    backend_module = type(backend).__module__
    native_backend = any(
        backend_module == allowed_module or backend_module.startswith(f"{allowed_module}.")
        for allowed_module in _NATIVE_BACKEND_MODULES
    )
    if not isinstance(priority, (int, float)) or priority <= 0 or not native_backend:
        raise CredentialStoreError("A supported native credential store is not available.")


def _read_stored_token() -> str | None:
    _require_native_backend()
    try:
        token = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise CredentialStoreError("Native credential store access failed.") from None
    if token is None:
        return None
    normalized = token.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise InvalidStoredCredentialError("Stored BrewForge credential is invalid.")
    return normalized


def _environment_token_state() -> tuple[str | None, Literal["absent", "valid", "invalid"]]:
    normalized = os.getenv(_ENVIRONMENT_NAME, "").strip()
    if not normalized:
        return None, "absent"
    if any(character.isspace() for character in normalized):
        return None, "invalid"
    return normalized, "valid"


def environment_override_status() -> Literal["absent", "valid", "invalid"]:
    """Report environment override state without returning or displaying its value."""
    _, status = _environment_token_state()
    return status


def resolve_token() -> ResolvedToken:
    """Resolve environment override first, then the native credential store."""
    environment_token, environment_status = _environment_token_state()
    if environment_status == "invalid":
        raise InvalidEnvironmentCredentialError("BrewForge environment credential is invalid.")
    if environment_token is not None:
        return ResolvedToken(token=environment_token, source="environment")

    stored_token = _read_stored_token()
    if stored_token is not None:
        return ResolvedToken(token=stored_token, source="keyring")
    return ResolvedToken(token=None, source="missing")


def store_token(token: str) -> None:
    """Store one validated token in the native credential store."""
    normalized = token.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("BrewForge API token must not be empty or contain whitespace")

    _require_native_backend()
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, normalized)
    except Exception:
        raise CredentialStoreError("Native credential store access failed.") from None


def delete_token() -> bool:
    """Delete the stored token and report whether an entry existed."""
    _require_native_backend()
    try:
        exists = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is not None
    except Exception:
        raise CredentialStoreError("Native credential store access failed.") from None
    if not exists:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise CredentialStoreError("Native credential store access failed.") from None
    return True
