"""Native credential storage for a read-only Brewfather API profile."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

import keyring

SERVICE_NAME = "forge-companion"
ACCOUNT_NAME = "brewfather-api-profile"
_NATIVE_BACKEND_MODULES = (
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "keyring.backends.SecretService",
)


class BrewfatherCredentialError(RuntimeError):
    """Report credential failures without exposing backend details."""


class InvalidStoredBrewfatherCredentialError(BrewfatherCredentialError):
    """Report an invalid stored profile without exposing its content."""


@dataclass(frozen=True)
class BrewfatherProfile:
    """One Brewfather user ID and API key."""

    user_id: str = field(repr=False)
    api_key: str = field(repr=False)


@dataclass(frozen=True)
class ResolvedBrewfatherProfile:
    """A Brewfather profile together with its non-secret source."""

    profile: BrewfatherProfile | None
    source: Literal["keyring", "missing"]


def _require_native_backend() -> None:
    try:
        backend = keyring.get_keyring()
        priority = backend.priority
    except Exception:
        raise BrewfatherCredentialError("Native credential store access failed.") from None
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
        raise BrewfatherCredentialError("A supported native credential store is not available.")


def _normalize(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError(f"Brewfather {field} must not be empty or contain whitespace")
    return normalized


def store_profile(*, user_id: str, api_key: str) -> None:
    """Store a validated read-only Brewfather profile in the native keyring."""
    payload = json.dumps(
        {
            "version": 1,
            "user_id": _normalize(user_id, field="user ID"),
            "api_key": _normalize(api_key, field="API key"),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    _require_native_backend()
    try:
        keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, payload)
    except Exception:
        raise BrewfatherCredentialError("Native credential store access failed.") from None


def _decode_profile(raw: str) -> dict[str, object]:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    payload = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(payload, dict) or set(payload) != {"version", "user_id", "api_key"}:
        raise ValueError("invalid profile schema")
    return payload


def resolve_profile() -> ResolvedBrewfatherProfile:
    """Resolve and validate one Brewfather profile from the native keyring."""
    _require_native_backend()
    try:
        raw = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise BrewfatherCredentialError("Native credential store access failed.") from None
    if raw is None:
        return ResolvedBrewfatherProfile(profile=None, source="missing")
    try:
        payload = _decode_profile(raw)
        user_id = payload["user_id"]
        api_key = payload["api_key"]
        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ValueError("invalid profile version")
        if not isinstance(user_id, str) or not isinstance(api_key, str):
            raise ValueError("invalid profile values")
        profile = BrewfatherProfile(
            user_id=_normalize(user_id, field="user ID"),
            api_key=_normalize(api_key, field="API key"),
        )
    except (KeyError, TypeError, ValueError, RecursionError):
        raise InvalidStoredBrewfatherCredentialError(
            "Stored Brewfather credential is invalid."
        ) from None
    return ResolvedBrewfatherProfile(profile=profile, source="keyring")


def delete_profile() -> bool:
    """Delete a stored Brewfather profile, including malformed content."""
    _require_native_backend()
    try:
        exists = keyring.get_password(SERVICE_NAME, ACCOUNT_NAME) is not None
    except Exception:
        raise BrewfatherCredentialError("Native credential store access failed.") from None
    if not exists:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except Exception:
        raise BrewfatherCredentialError("Native credential store access failed.") from None
    return True
