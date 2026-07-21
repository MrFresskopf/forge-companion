"""Non-secret local preferences for the comfort-oriented CLI."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from forge_companion.file_io import atomic_write_text

_CONFIG_DIRECTORY_ENV = "FORGE_COMPANION_CONFIG_DIR"


class PreferencesError(ValueError):
    """Report malformed local preferences without exposing their contents."""


@dataclass(frozen=True)
class Preferences:
    """User choices that are safe to store outside the credential store."""

    temperature_unit: str | None = None


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
    if not isinstance(payload, dict) or set(payload) - {"temperature_unit"}:
        raise PreferencesError("Preferences file has an unsupported shape.")
    unit = payload.get("temperature_unit")
    if unit not in {None, "C", "F"}:
        raise PreferencesError("Stored temperature unit must be C or F.")
    return Preferences(temperature_unit=unit)


def save_preferences(preferences: Preferences) -> None:
    """Atomically store validated non-secret preferences."""
    if preferences.temperature_unit not in {None, "C", "F"}:
        raise PreferencesError("Temperature unit must be C or F.")
    content = json.dumps(
        {"temperature_unit": preferences.temperature_unit},
        ensure_ascii=False,
        sort_keys=True,
    )
    atomic_write_text(content + "\n", preferences_path(), newline="\n")
