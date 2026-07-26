"""Native credential storage for one Shelly Cloud profile."""

import json
from dataclasses import dataclass
from typing import Literal

import keyring

from forge_companion.shelly_cloud import (
    normalize_cloud_auth_key,
    normalize_cloud_device_id,
    normalize_cloud_server,
)

SERVICE_NAME = "forge-companion"
ACCOUNT_NAME = "shelly-cloud-profile"
_NATIVE_BACKEND_MODULES = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
)


class ShellyCloudCredentialError(RuntimeError):
    """Report cloud credential failures without exposing backend details."""


class InvalidStoredCloudCredentialError(ShellyCloudCredentialError):
    """Report an invalid stored cloud profile without exposing its content."""


@dataclass(frozen=True)
class ShellyCloudProfile:
    """One validated Shelly Cloud tenant, device, and secret key."""

    server: str
    device_id: str
    auth_key: str


@dataclass(frozen=True)
class ResolvedCloudProfile:
    """A cloud profile with its non-secret source."""

    profile: ShellyCloudProfile | None
    source: Literal["keyring", "missing"]


def _require_native_backend() -> None:
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except Exception:
        raise ShellyCloudCredentialError("Native credential store access failed.") from None
    module = type(backend).__module__
    native = any(
        module == allowed or module.startswith(f"{allowed}.") for allowed in _NATIVE_BACKEND_MODULES
    )
    if not isinstance(priority, (int, float)) or priority <= 0 or not native:
        raise ShellyCloudCredentialError("A supported native credential store is not available.")


def _validated_profile(*, server: str, device_id: str, auth_key: str) -> ShellyCloudProfile:
    return ShellyCloudProfile(
        server=normalize_cloud_server(server),
        device_id=normalize_cloud_device_id(device_id),
        auth_key=normalize_cloud_auth_key(auth_key),
    )


def store_profile(*, server: str, device_id: str, auth_key: str) -> None:
    """Validate and store one cloud profile in the native keyring."""
    profile = _validated_profile(server=server, device_id=device_id, auth_key=auth_key)
    payload = json.dumps(
        {
            "version": 1,
            "server": profile.server,
            "device_id": profile.device_id,
            "auth_key": profile.auth_key,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    _require_native_backend()
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, payload)
    except Exception:
        raise ShellyCloudCredentialError("Native credential store access failed.") from None


def _decode_stored_profile(raw: str) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError("duplicate stored profile key")
            payload[key] = value
        return payload

    def reject_constant(value: str) -> None:
        raise ValueError("invalid stored profile constant")

    payload = json.loads(
        raw,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "server",
        "device_id",
        "auth_key",
    }:
        raise ValueError("invalid stored profile schema")
    return payload


def resolve_profile() -> ResolvedCloudProfile:
    """Resolve one cloud profile from the native credential store."""
    _require_native_backend()
    try:
        raw = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise ShellyCloudCredentialError("Native credential store access failed.") from None
    if raw is None:
        return ResolvedCloudProfile(profile=None, source="missing")
    try:
        payload = _decode_stored_profile(raw)
        server = payload["server"]
        device_id = payload["device_id"]
        auth_key = payload["auth_key"]
        if (
            payload["version"] != 1
            or not isinstance(server, str)
            or not isinstance(device_id, str)
            or not isinstance(auth_key, str)
        ):
            raise ValueError("invalid stored profile values")
        profile = _validated_profile(
            server=server,
            device_id=device_id,
            auth_key=auth_key,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise InvalidStoredCloudCredentialError(
            "Stored Shelly Cloud credential is invalid."
        ) from None
    return ResolvedCloudProfile(profile=profile, source="keyring")


def delete_profile() -> bool:
    """Delete a stored cloud profile, including malformed content."""
    _require_native_backend()
    try:
        exists = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is not None
    except Exception:
        raise ShellyCloudCredentialError("Native credential store access failed.") from None
    if not exists:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise ShellyCloudCredentialError("Native credential store access failed.") from None
    return True
