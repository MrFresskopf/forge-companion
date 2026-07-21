from pathlib import Path

from forge_companion.preferences import Preferences, load_preferences, save_preferences


def test_preferences_round_trip_in_explicit_config_directory(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))

    save_preferences(Preferences(temperature_unit="C"))

    assert load_preferences() == Preferences(temperature_unit="C")
    content = (tmp_path / "preferences.json").read_text(encoding="utf-8")
    assert "token" not in content.lower()
