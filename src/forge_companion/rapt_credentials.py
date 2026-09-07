"""Native credential storage for one RAPT API profile."""

import json
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

import keyring

SERVICE_NAME = "forge-companion"
ACCOUNT_NAME = "rapt-api-profile"
_NATIVE_BACKEND_MODULES = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
)


class RaptCredentialError(RuntimeError):
    """Report credential failures without exposing backend details."""


class InvalidStoredRaptCredentialError(RaptCredentialError):
    """Report an invalid stored profile without exposing its content."""


@dataclass(frozen=True)
class RaptProfile:
    """One RAPT account username and API secret."""

    username: str = field(repr=False)
    api_secret: str = field(repr=False)


@dataclass(frozen=True)
class ResolvedRaptProfile:
    """A RAPT profile together with its non-secret source."""

    profile: RaptProfile | None
    source: Literal["keyring", "missing"]


def _require_native_backend() -> None:
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except Exception:
        raise RaptCredentialError("Native credential store access failed.") from None
    module = type(backend).__module__
    try:
        usable_priority = (
            not isinstance(priority, bool)
            and isinstance(priority, (int, float))
            and isfinite(priority)
            and priority > 0
        )
    except OverflowError:
        usable_priority = False
    if module not in _NATIVE_BACKEND_MODULES or not usable_priority:
        raise RaptCredentialError("A supported native credential store is not available.")


def _normalize_username(username: str) -> str:
    normalized = username.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("RAPT username must not be empty or contain whitespace")
    return normalized


def _normalize_secret(api_secret: str) -> str:
    normalized = api_secret.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("RAPT API secret must not be empty or contain whitespace")
    return normalized


def store_profile(*, username: str, api_secret: str) -> None:
    """Store a validated RAPT username and API secret in the native keyring."""
    payload = json.dumps(
        {
            "version": 1,
            "username": _normalize_username(username),
            "api_secret": _normalize_secret(api_secret),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    _require_native_backend()
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, payload)
    except Exception:
        raise RaptCredentialError("Native credential store access failed.") from None


def _decode_profile(raw: str) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    payload = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(payload, dict) or set(payload) != {
        "version",
        "username",
        "api_secret",
    }:
        raise ValueError("invalid profile schema")
    return payload


def resolve_profile() -> ResolvedRaptProfile:
    """Resolve and validate one RAPT profile from the native keyring."""
    _require_native_backend()
    try:
        raw = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise RaptCredentialError("Native credential store access failed.") from None
    if raw is None:
        return ResolvedRaptProfile(profile=None, source="missing")
    try:
        payload = _decode_profile(raw)
        username = payload["username"]
        api_secret = payload["api_secret"]
        if (
            type(payload["version"]) is not int
            or payload["version"] != 1
            or not isinstance(username, str)
            or not isinstance(api_secret, str)
        ):
            raise ValueError("invalid profile values")
        profile = RaptProfile(
            username=_normalize_username(username),
            api_secret=_normalize_secret(api_secret),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        raise InvalidStoredRaptCredentialError("Stored RAPT credential is invalid.") from None
    return ResolvedRaptProfile(profile=profile, source="keyring")


def delete_profile() -> bool:
    """Delete a stored RAPT profile, including malformed content."""
    _require_native_backend()
    try:
        exists = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is not None
    except Exception:
        raise RaptCredentialError("Native credential store access failed.") from None
    if not exists:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise RaptCredentialError("Native credential store access failed.") from None
    return True
