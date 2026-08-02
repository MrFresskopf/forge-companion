"""Non-secret local preferences for the comfort-oriented CLI."""

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forge_companion.file_io import atomic_write_text

_CONFIG_DIRECTORY_ENV = "FORGE_COMPANION_CONFIG_DIR"
HOPPER_QUALIFICATION_STATEMENT_VERSION = 1


class PreferencesError(ValueError):
    """Report malformed local preferences without exposing their contents."""


@dataclass(frozen=True)
class Preferences:
    """User choices that are safe to store outside the credential store."""

    temperature_unit: str | None = None
    hopper_qualification_statement_version: int | None = None
    hopper_qualification_attested_at: str | None = None


def _validate_hopper_qualification(
    statement_version: int | None,
    attested_at: str | None,
) -> None:
    if statement_version is None and attested_at is None:
        return
    if (
        not isinstance(statement_version, int)
        or isinstance(statement_version, bool)
        or statement_version < 1
        or not isinstance(attested_at, str)
    ):
        raise PreferencesError("Stored hopper qualification is invalid.")
    try:
        timestamp = datetime.fromisoformat(attested_at)
    except ValueError:
        raise PreferencesError("Stored hopper qualification is invalid.") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise PreferencesError("Stored hopper qualification is invalid.")


def hopper_qualification_is_current(preferences: Preferences) -> bool:
    """Return whether the current operator-attestation statement was accepted."""
    return (
        preferences.hopper_qualification_statement_version == HOPPER_QUALIFICATION_STATEMENT_VERSION
        and preferences.hopper_qualification_attested_at is not None
    )


def _config_directory() -> Path:
    override = os.getenv(_CONFIG_DIRECTORY_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        return Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming")) / "forge-companion"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "forge-companion"
    xdg = os.getenv("XDG_CONFIG_HOME", "").strip()
    return (Path(xdg).expanduser() if xdg else Path.home() / ".config") / "forge-companion"


def preferences_path() -> Path:
    """Return the platform-native preferences file path."""
    return _config_directory() / "preferences.json"


def load_preferences() -> Preferences:
    """Load validated non-secret preferences, returning defaults when absent."""
    source = preferences_path()
    if not source.exists():
        return Preferences()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PreferencesError("Preferences file is invalid or unreadable.") from None
    if not isinstance(payload, dict) or set(payload) - {
        "temperature_unit",
        "hopper_qualification_statement_version",
        "hopper_qualification_attested_at",
    }:
        raise PreferencesError("Preferences file has an unsupported shape.")
    unit = payload.get("temperature_unit")
    if unit not in {None, "C", "F"}:
        raise PreferencesError("Stored temperature unit must be C or F.")
    statement_version = payload.get("hopper_qualification_statement_version")
    attested_at = payload.get("hopper_qualification_attested_at")
    _validate_hopper_qualification(statement_version, attested_at)
    return Preferences(
        temperature_unit=unit,
        hopper_qualification_statement_version=statement_version,
        hopper_qualification_attested_at=attested_at,
    )


def save_preferences(preferences: Preferences) -> None:
    """Atomically store validated non-secret preferences."""
    if preferences.temperature_unit not in {None, "C", "F"}:
        raise PreferencesError("Temperature unit must be C or F.")
    _validate_hopper_qualification(
        preferences.hopper_qualification_statement_version,
        preferences.hopper_qualification_attested_at,
    )
    content = json.dumps(
        {
            "temperature_unit": preferences.temperature_unit,
            "hopper_qualification_statement_version": (
                preferences.hopper_qualification_statement_version
            ),
            "hopper_qualification_attested_at": preferences.hopper_qualification_attested_at,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    atomic_write_text(content + "\n", preferences_path(), newline="\n")
